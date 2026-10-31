---
layout:     post
title:      DeepSeek-V3 源码精读（3）：81 行的 freqs_cis 与 YaRN
subtitle:   32 个频率、两个圈数阈值、一张 4 MiB 的表：长上下文不是把位置拉长，是把频段分开处理
date:       2026-10-31
author:     WY
header-img: img/post-bg-map.jpg
catalog:    true
description: 从 RMSNorm 的一行 forward 读到 precompute_freqs_cis 的 81 行：64 维为什么只有 32 个角度、beta_fast 与 beta_slow 其实是旋转圈数、YaRN 只动高频那一段，以及 4 MiB 查表换掉的 61 倍 sin/cos。
math:       true
series:     deepseek-source
chapter:    3
source:     deepseek-ai/DeepSeek-V3@9b4e9788e4a3
tags:
    - 人工智能
    - 深度学习
    - 大语言模型
    - 神经网络
---

## 开篇：81 行里没有一行三角函数

上一篇把通信的账算完，结尾说这一期回到单卡内部。现在拆 270 到 395 行：一个 27 行的 `RMSNorm`，一个 81 行的 `precompute_freqs_cis`，一个 18 行的 `apply_rotary_emb`。

翻完会发现一件怪事：81 行里没有一处 `sin`，也没有一处 `cos`。所有角度打包进 `torch.polar` 的复数里，旋转靠一次复数乘法完成。所以读位置编码，读的不是三角函数怎么算，而是**哪几个频率被改了、改了多少、什么时候改**。

三条前置知识，缺一条会卡：

1. RoPE 把一对实数当成一个复数来转。64 维实数等于 32 个角度，不是 64 个。这个换算决定了表长、`view` 的形状和后面所有的除法。
2. `config_671B.json` 里没有 `max_seq_len`，也没有 `rope_theta`、`rope_factor`、`beta_fast`、`beta_slow`。这些值全部落到 `ModelArgs` 的默认值上——第 5 节会看到这一点的后果。
3. `freqs_cis` 是 buffer 不是参数，注册时带了 `persistent=False`，不进 state_dict，也不会出现在 checkpoint 里。

这期只回答一个问题：**一套按 4096 训练的位置编码，凭什么撑到 16384，代价记在谁头上。** MLA 怎么吸收、KV cache 怎么存，留给第 5、6 期。

## 第 3 期：位置不是编号，是相位

### 1. RMSNorm：一行 forward，把数学藏进 kernel

`RMSNorm` 占了 270 到 296 行。27 行里 16 行是 docstring，构造四行，真正的计算只有一行。

```python
# inference/model.py:278-294（只保留实现，删掉 docstring）
def __init__(self, dim: int, eps: float = 1e-6):
    super().__init__()
    self.dim = dim
    self.eps = eps
    self.weight = nn.Parameter(torch.ones(dim))

def forward(self, x: torch.Tensor):
    return F.rms_norm(x, (self.dim,), self.weight, self.eps)
```

三个参数没有一个是多余的：`(self.dim,)` 是 normalized_shape，只对最后一维求统计量；`weight` 初始化为全 1，没有 bias；`eps` 放在根号里面。数学写成一行是这样：

$$
\mathrm{RMSNorm}(x) = \frac{x}{\sqrt{\frac{1}{d}\sum_{i=1}^{d} x_i^{2} + \epsilon}} \odot w
$$

和 LayerNorm 的差别只有一处：分子不减均值。收益和代价都从这一处来。

先说收益。少一次求均值、少一次减法，统计量只累加平方和。在 7168 维上这不是小数：每个 token 每层要过两次 norm（`attn_norm` 与 `ffn_norm`，{% include src.html path="inference/model.py" lines="717-718" text="717-718 行" %}），61 层就是 122 次，加上输出前那一次（{% include src.html path="inference/model.py" lines="792" text="第 792 行" %}），一次前向 123 次归一化。少一遍读写，省的是纯带宽。

再说代价，这个代价经常被"RMSNorm 就是简化版 LayerNorm"这句话糊过去。它不是近似，是**换了一个不变量**：对整体缩放免疫，对整体平移不免疫。

第一点可以直接量。输入乘 3，输出一模一样，最大差 4.5e-11，纯浮点噪声。第二点听起来抽象，量一下就很具体：取一个 8 维向量，内容是"100 加上标准差 1 的噪声"，再问归一化后的向量与"纯噪声方向"的余弦相似度——

```
RMSNorm(x)   与纯信号 cos = 0.0285
LayerNorm(x) 与纯信号 cos = 0.9997
```

LayerNorm 把那个 100 剥得干干净净，RMSNorm 几乎原样留着。因为 100 是共模分量，不减均值就减不掉。换句话说，**RMSNorm 之后，残差流里的大偏置会一路传下去**。这不是 bug，是取舍：模型可以在 `weight` 里学补偿，但那是逐维的常数，学不出逐样本的中心化。

还有一个只在极端值上现形的坑：`eps` 在根号里。RMS 是 1e-2 时它毫无影响，1e-3 时把分母抬高 41%，1e-4 时抬高 9 倍。bf16 的动态范围很大，正常激活离这里很远，但一整行全零的 padding 恰好落在这个区间——这也是为什么不少实现在 norm 之前先把 padding 位置 mask 掉再做统计。

最后是一个容易读漏的形状细节。`kv_norm` 只归一化 512 维，不含那 64 维 rope。原因在 {% include src.html path="inference/model.py" lines="468-470" text="468-470 行" %}：先 `split` 再 `norm`，顺序不能反。如果对 576 维整体归一化，rope 那 64 维的尺度就会被 512 维 latent 的 RMS 牵着走；而它紧接着要乘一个模长恒为 1 的复数——旋转保模长，所以这 64 维的尺度在归一化那一步定下之后，后面再也改不了。训练时用的哪种顺序，推理就必须是哪种，这里没有"差不多"的余地。

### 2. 32 个频率：从 6.3 个 token 到 47117 个 token

`precompute_freqs_cis` 的第一行就有信息量：

```python
# inference/model.py:366
freqs = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.float32) / dim))
```

`arange(0, dim, 2)` 的步长是 2，所以这里只有 32 个数，不是 64。指数是 `i/32`，频率是 $10000^{-i/32}$。写成更好看的形式：

$$
\theta_i = b^{-2i/d}, \qquad \lambda_i = \frac{2\pi}{\theta_i} = 2\pi\, b^{2i/d}, \qquad i = 0, 1, \dots, 31
$$

λ 是"转完一圈需要多少个 token"。i=0 时 λ=6.3，一个 token 正好转 1 弧度；i=31 时 λ=47117，四万多个 token 才转完一圈。中间挑几行，顺便把 4096 窗口内的圈数算出来：

| i | θ_i | λ（token） | 4096 窗口内圈数 |
|---|---|---|---|
| 0 | 1.000000 | 6.3 | 651.9 |
| 10 | 0.056234 | 111.7 | 36.7 |
| 15 | 0.013335 | 471.2 | 8.7 |
| 22 | 0.001778 | 3533.3 | 1.16 |
| 23 | 0.001334 | 4711.7 | 0.87 |
| 31 | 0.000133 | 47117.2 | 0.09 |

这张表能读出两件事。

第一，**32 个维度不是 32 个同等重要的通道，是 32 个快慢差四个数量级的时钟**。快钟负责区分相邻 token，慢钟负责区分段落。把位置信息理解成"每个维度存一位坐标"是错的，它存的是相位，而相位要配合圈数才有意义。

第二，在 4096 的窗口里，前 23 个维度至少转满一圈，后 9 个连一圈都转不完。转不满一圈的维度，角度在训练窗口内单调递增、从没回绕，模型实际上把它们当**绝对位置的刻度**在用；转满几百圈的维度，角度早就回绕了无数次，模型只能从相位差里读**相对距离**。这个划分不是巧合——下一节的两个阈值正好落在这条缝的两侧。

顺手纠正一个说法：说"freqs_cis 有 64 个频率"是不对的。实数维是 64，但旋转是成对的，角度只有 32 个，表是 `[seqlen, 32]` 的 complex64。这个 2 倍的换算在第 4 节的每个形状里都会出现一次。

### 3. YaRN：beta_fast 与 beta_slow 是"转了几圈"

YaRN 的全部逻辑，加上两个辅助函数，一共 33 行（{% include src.html path="inference/model.py" lines="343-375" text="343-375 行" %}）：

```python
# inference/model.py:343-375（只删掉 docstring 与空行）
def find_correction_range(low_rot, high_rot, dim, base, max_seq_len):
    low = math.floor(find_correction_dim(low_rot, dim, base, max_seq_len))
    high = math.ceil(find_correction_dim(high_rot, dim, base, max_seq_len))
    return max(low, 0), min(high, dim-1)

def linear_ramp_factor(min, max, dim):
    if min == max:
        max += 0.001
    linear_func = (torch.arange(dim, dtype=torch.float32) - min) / (max - min)
    ramp_func = torch.clamp(linear_func, 0, 1)
    return ramp_func

freqs = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.float32) / dim))
if seqlen > args.original_seq_len:
    low, high = find_correction_range(beta_fast, beta_slow, dim, base, args.original_seq_len)
    smooth = 1 - linear_ramp_factor(low, high, dim // 2)
    freqs = freqs / factor * (1 - smooth) + freqs * smooth

t = torch.arange(seqlen)
freqs = torch.outer(t, freqs)
freqs_cis = torch.polar(torch.ones_like(freqs), freqs)
```

`find_correction_dim` 的形参名字已经把答案写出来了：`num_rotations`。它算的是"转 n 圈的那个维度排第几"：

$$
i_{\mathrm{corr}}(n) = \frac{d \ln\!\left(\dfrac{L_{\mathrm{orig}}}{2\pi n}\right)}{2 \ln b}
$$

把 671B 的那组数代进去（d=64、b=10000、L=4096）：$\beta_{\mathrm{fast}}=32$ 给出 $i=10.47$，向下取整是 10；$\beta_{\mathrm{slow}}=1$ 给出 $i=22.51$，向上取整是 23。于是 `low=10`、`high=23`。

这两个数不是调出来的玄学，就是上一节表格里的两条线：**i=10 是最后一个能转满 32 圈的维度（36.7 圈），i=23 是第一个转不满 1 圈的维度（0.87 圈）**。代码用 $1 - \mathrm{ramp}$ 做为插值权重，效果是：

$$
\theta_i' = \frac{\theta_i}{s}\, r_i + \theta_i\,(1 - r_i),
\qquad r_i = \mathrm{clamp}\!\left(\frac{i - 10}{23 - 10}, 0, 1\right)
$$

$r_i = 0$ 的频率原封不动，$r_i = 1$ 的频率除以 40，中间 12 个维度线性过渡。实测：

| i | r_i | θ 变化 | λ 从 | 到 |
|---|---|---|---|---|
| 0–10 | 0 | ×1 | 6.3 | 6.3 |
| 15 | 0.38 | ×0.625 | 471.2 | 753.9 |
| 22 | 0.92 | ×0.103 | 3533.3 | 35332.9 |
| 23–31 | 1 | ×0.025 | 4711.7 | 188469.0 |

为什么这样切，一句话：**转得快的维度可以慢下来，转不动的维度不能动**。前 11 个维度在 4096 内转了 36 到 652 圈，相位信息冗余，把频率压到 1/40 只是把"每 token 转多少"改小，相对距离仍然读得出来；后 9 个维度在训练窗口里连一圈都没转完，角度是位置的单调刻度，一压缩就把训练时学到的绝对坐标整体平移掉。中间那 12 个维度处在"转了几圈但不多"的地带，硬切会在边界产生不连续，所以用线性 ramp 过渡。

这里有个值得停一下的细节：`find_correction_range` 传的是 `dim`（64），而 `linear_ramp_factor` 铺在 `dim // 2`（32）上。两个单位差了一倍。若把前者也换成 32，得到的是 (5, 12)——整条过渡带往高频端平移到一半的位置，原本不动的 5 到 10 号维度也会被压。这份代码是从 YaRN 参考实现一路抄下来的，权重也是按这套约定训的——**它不是笔误，是约定**；但如果自己复现，两边必须一致，否则频率表会错位而形状完全合法。

还有一处最容易漏：YaRN 的第二个改动不在这个函数里。{% include src.html path="inference/model.py" lines="434-437" text="434-437 行" %}把注意力温度也改了：

$$
s_{\mathrm{attn}} = d_{qk}^{-1/2}\left(0.1 \ln s + 1\right)^{2}
$$

代入 d=192、s=40：括号里是 1.36889，平方 1.8739，`softmax_scale` 从 0.07217 变成 0.13523。**只抄 `precompute_freqs_cis`、不抄这三行，注意力会软 1.87 倍**，长文本上表现为注意力弥散、复制能力下降，而且不会报任何错。

### 4. 一条 shape 流水线（bsz=1，seqlen=4，tp=8）

把上面三段接起来，这是 q 与 k 的完整路径。第四列是本卡形状，MLA 的头按 rank 切。

| 步骤 | 代码位置 | 全局形状 | 本卡形状 |
|---|---|---|---|
| token id | 输入 | `[1, 4]` | 同 |
| 嵌入 | {% include src.html path="inference/model.py" lines="785" text="785 行" %} | `[1, 4, 7168]` | 同（复制） |
| 切出位置表 | {% include src.html path="inference/model.py" lines="786" text="786 行" %} | `[4, 32]` complex64 | 同 |
| `wq_a` | {% include src.html path="inference/model.py" lines="427" text="427 行" %} | `[1, 4, 1536]` | 同（复制） |
| `q_norm` | {% include src.html path="inference/model.py" lines="428" text="428 行" %} | `[1, 4, 1536]` | 同 |
| `wq_b` | {% include src.html path="inference/model.py" lines="429" text="429 行" %} | `[1, 4, 24576]` | `[1, 4, 3072]` |
| 拆头 | {% include src.html path="inference/model.py" lines="465" text="465 行" %} | `[1, 4, 128, 192]` | `[1, 4, 16, 192]` |
| 分成 nope / pe | {% include src.html path="inference/model.py" lines="466" text="466 行" %} | `[1, 4, 128, 128]` + `[1, 4, 128, 64]` | `[1, 4, 16, 128]` + `[1, 4, 16, 64]` |
| 转复数 | {% include src.html path="inference/model.py" lines="390" text="390 行" %} | `[1, 4, 128, 32]` | `[1, 4, 16, 32]` |
| 乘位置表 | {% include src.html path="inference/model.py" lines="391-392" text="391-392 行" %} | 广播 `[1, 4, 1, 32]` | 同 |
| 转回实数 | {% include src.html path="inference/model.py" lines="392" text="392 行" %} | `[1, 4, 128, 64]` | `[1, 4, 16, 64]` |
| `wkv_a` | {% include src.html path="inference/model.py" lines="430" text="430 行" %} | `[1, 4, 576]` | 同（复制） |
| 分成 latent / pe | {% include src.html path="inference/model.py" lines="469" text="469 行" %} | `[1, 4, 512]` + `[1, 4, 64]` | 同 |
| `k_pe` 加一维 | {% include src.html path="inference/model.py" lines="470" text="470 行" %} | `[1, 4, 1, 64]` | 同 |
| 写入 cache | {% include src.html path="inference/model.py" lines="484-485" text="484-485 行" %} | `[8, 16384, 512]` + `[8, 16384, 64]` | 同 |

三个地方值得盯住。

**第一，`freqs_cis.view(1, x.size(1), 1, x.size(-1))`。** 这一行写死了"输入是四维、第 1 维是序列长度"。`q_pe` 是 `[1, 4, 128, 64]`，正好；`k_pe` 调之前先 `unsqueeze(2)` 变成 `[1, 4, 1, 64]`，也正好——头维是 1，广播出去之后所有头共用同一个位置相位。如果有人先把张量转成 `[bsz, heads, seq, dim]` 再调用，`x.size(1)` 就变成了头数，广播不报错，结果全错。这类 bug 在 bf16 下不会有任何异常数值，只会让困惑度慢慢变高。

**第二，`x.float()` 与 `y.to(dtype)`。** 旋转在 fp32 里做，算完再降回 bf16（{% include src.html path="inference/model.py" lines="389-393" text="389-393 行" %}）。角度一旦到几千弧度，bf16 只有 8 位尾数，$\cos$ 的自变量误差会直接放大到结果上。这一行不是洁癖，是必需品。

**第三，cache 里每 token 只存 576 维。** 512 的 latent 加 64 的 rope，正好是第 6 期要算的那笔账：128 个头的完整 K、V 要 40960 维，这里只有 576。

### 5. 预计算：省的不是时间，是 61 次重复

`precompute_freqs_cis` 在 `Transformer.__init__` 里被调用一次（{% include src.html path="inference/model.py" lines="770" text="第 770 行" %}），结果是 `[16384, 32]` 的 complex64，正好 4.00 MiB。换成每层现算，就是 61 层 × 16384 位置 × 32 对 = 3198 万次 sin/cos，预计算之后是 52.4 万次。**省 61 倍，因为层与层之间这个表完全一样。**

但"预计算只是性能优化"这个理解漏掉了两件事。

第一，`persistent=False` 让这块表不进 state_dict。checkpoint 里没有这个 key，加载时不会因为形状不匹配报错；同时 `.to(device)` 会把它一起搬走。代价是它也不会被保存——每次起进程都要重算这 52 万次三角函数，这点开销可以忽略。

第二，也是更要命的一点：**这张表的长度由配置决定，不由实际输入决定。** `seqlen = args.max_seq_len`，而 `config_671B.json` 没有这个字段，落到 `ModelArgs` 的默认值 `4096 * 4 = 16384`（{% include src.html path="inference/model.py" lines="56" text="第 56 行" %}）。README 的参数表写的是 128K，代码默认只建了 16K 的表。想真的跑 128K，得把 `max_seq_len` 改成 131072 再重新构造模型——表长不够时，`self.freqs_cis[start_pos:start_pos+seqlen]` 会静默返回一个更短的切片，然后在随后的 `view` 处报形状错误，而不是给你一个"位置编码不够用"的提示。

更微妙的是那个 `if seqlen > args.original_seq_len`。它是**构建期开关，不是运行期开关**：只要配置的 `max_seq_len` 大于 4096，整张表就按 YaRN 改过，哪怕你只喂 100 个 token。第 100 个位置上，dim 0 的角度从 100.00 弧度变成 2.50 弧度，差了 15.5 个周期。也就是说：

> 同一份权重，`max_seq_len=4096` 和 `max_seq_len=16384` 两种配置，短文本的输出是不一样的。

这句话反过来说更有用：如果你只是想在 4K 以内复现论文里的短文本行为，就不要顺手把 `max_seq_len` 调大。调大不是"打开开关"，是"换一套位置编码"。

### 6. 三个常见误解

**误解一：`freqs_cis` 里存着 64 个频率。**

存的是 32 个角度，用复数打包。64 维实数进 `view_as_complex` 之后是 32 个复数，`freqs_cis` 的最后一维也是 32。写成 64 会让表大一倍、`view` 直接报错；更隐蔽的是在别处复现时把 `arange(0, dim, 2)` 写成 `arange(dim)`：长度从 32 变 64，指数范围从 0 到 0.97 变成 0 到 1，步长差一倍，频率全错。

**误解二：YaRN 就是把所有位置除以 factor。**

只有 9 个维度真的被除以 40（i=23 到 31），前 11 个一动不动，中间 12 个按 ramp 渐变。如果整表除以 40，那 9 个"绝对刻度"维度会瞬间失去训练时学到的坐标：它们本来靠角度单调递增来区分远近，压缩之后相邻位置的角度差只剩 1/40，信噪比直接掉下去，长文本上开头几十个 token 就开始跑偏。而且温度修正（`mscale`）根本不在这个函数里，它在 `MLA.__init__`，漏抄就是 1.87 倍的 softmax 尺度差。

**误解三：RMSNorm 是 LayerNorm 的近似，误差可以忽略。**

它不缺精度，缺的是中心化。输入带大共模分量时，两者的输出方向余弦是 0.03 和 1.00 的差别，不是小数点后几位的差别。把它当"更快的 LayerNorm"替换进一个按 LayerNorm 调过的模型，第一层可能还正常，深了之后残差流里的偏置会越积越大。反过来，在从零训练的模型里它是更省带宽的选择，多数复现里也没掉点。

### 7. 在 CPU 上把这张表重算一遍

下面这段不需要 torch、不需要 numpy，纯标准库，五十来行。它做的事和 `precompute_freqs_cis` 一样，只是把 torch 换成 `math`，顺便把中间量打出来。

```python
import math

DIM, BASE, FACTOR = 64, 10000.0, 40.0
BETA_FAST, BETA_SLOW = 32.0, 1.0
ORIG, MAXLEN = 4096, 16384


def corr_dim(rot, dim, base, max_len):
    return dim * math.log(max_len / (rot * 2 * math.pi)) / (2 * math.log(base))


def corr_range(lo_rot, hi_rot, dim, base, max_len):
    lo = math.floor(corr_dim(lo_rot, dim, base, max_len))
    hi = math.ceil(corr_dim(hi_rot, dim, base, max_len))
    return max(lo, 0), min(hi, dim - 1)


freqs = [1.0 / BASE ** (i / (DIM // 2)) for i in range(DIM // 2)]
low, high = corr_range(BETA_FAST, BETA_SLOW, DIM, BASE, ORIG)
print("low = %d, high = %d" % (low, high))


def ramp(i):
    return min(max((i - low) / (high - low), 0.0), 1.0)


print(" i      freq  wavelength  rounds@4096  ramp      freq'  wavelength'")
for i in (0, 10, 15, 22, 23, 31):
    f = freqs[i]
    lam = 2 * math.pi / f
    r = ramp(i)
    f2 = f / FACTOR * r + f * (1 - r)
    print("%2d  %.6f  %10.1f  %11.2f  %.2f  %.6f  %11.1f"
          % (i, f, lam, ORIG / lam, r, f2, 2 * math.pi / f2))

n1 = sum(1 for f in freqs if ORIG >= 2 * math.pi / f)
n32 = sum(1 for f in freqs if ORIG >= 32 * 2 * math.pi / f)
print("转满 1 圈 %d 个，转满 32 圈 %d 个，共 %d 个" % (n1, n32, len(freqs)))
print("表 %d x %d complex64 = %.2f MiB"
      % (MAXLEN, DIM // 2, MAXLEN * (DIM // 2) * 8 / 2 ** 20))


def rope_pair(a, b, ang):
    c, s = math.cos(ang), math.sin(ang)
    return a * c - b * s, a * s + b * c


a, b = 0.6, -0.8
for ang in (0.0, 0.7, 100.0):
    x, y = rope_pair(a, b, ang)
    print("angle %6.1f -> (%7.4f, %7.4f)  norm %.6f" % (ang, x, y, math.hypot(x, y)))
```

实际输出：

```
low = 10, high = 23
 i      freq  wavelength  rounds@4096  ramp      freq'  wavelength'
 0  1.000000         6.3       651.90  0.00  1.000000          6.3
10  0.056234       111.7        36.66  0.00  0.056234        111.7
15  0.013335       471.2         8.69  0.38  0.008335        753.9
22  0.001778      3533.3         1.16  0.92  0.000178      35332.9
23  0.001334      4711.7         0.87  1.00  0.000033     188469.0
31  0.000133     47117.2         0.09  1.00  0.000003    1884689.7
转满 1 圈 23 个，转满 32 圈 11 个，共 32 个
表 16384 x 32 complex64 = 4.00 MiB
angle    0.0 -> ( 0.6000, -0.8000)  norm 1.000000
angle    0.7 -> ( 0.9743, -0.2253)  norm 1.000000
angle  100.0 -> ( 0.1123, -0.9937)  norm 1.000000
```

前两段验证了阈值：`low=10` 对应"最后一个转满 32 圈的维度"，`high=23` 对应"第一个转不满 1 圈的维度"，`rounds@4096` 那一列就是判据本身。第三段是很多人会忽略的性质：**旋转永远保模长，换成任何角度，norm 都是 1.000000**。所以位置编码写错时，权重范数、激活尺度、loss 曲线的前一百步全都正常，只有注意力分数会错——这是最难查的一类错误，因为它不给你任何数值异常，只给你一个稍微笨一点的模型。

改一个数就能看到对照：把 `BETA_FAST` 从 32 改成 1，`low` 会变成 22，过渡带缩到 1 个维度，几乎变成硬切；把 `FACTOR` 从 40 改成 1，整张表退化成原始 RoPE，和训练时用的分布对不上。但两种改法打印出来的形状完全一样。

### 8. 下期预告

下一期离开位置，去看 checkpoint：`convert.py` 的 97 行和 `kernel.py` 的 197 行。

真正的问题是：**一个 671B 的模型，权重为什么能只占一半显存？** 答案在 128×128 的分块 scale 里——和这一期的 4 MiB 查表是同一个思路：用一点额外的、可预测的存储，换掉每次计算都要重复的那部分。

---

## 小结

- RoPE 的 64 维实数只有 32 个角度，`freqs_cis` 是 `[seqlen, 32]` 的 complex64。波长从 6.3 个 token 到 47117 个 token，快慢差四个数量级；在 4096 窗口里，23 个维度转满一圈，9 个转不满——这条缝就是 YaRN 要切的地方。
- `beta_fast=32`、`beta_slow=1` 不是两个玄学系数，是"转几圈"的两条阈值，代进公式得到 `low=10`、`high=23`。前 11 个维度原样保留，后 9 个除以 40，中间 12 个线性过渡。
- YaRN 不只在频率表里。`mscale` 把 `softmax_scale` 从 0.07217 抬到 0.13523，漏掉这三行，注意力会软 1.87 倍且不报错。
- RMSNorm 与 LayerNorm 的差别是"不减均值"，不是"精度低一点"。它对缩放免疫、对平移不免疫，两者对同一个带偏置输入的方向余弦是 0.03 与 1.00。
- 最容易踩的坑是构建期开关：`max_seq_len` 一旦大于 4096，整张表就被 YaRN 改过，短文本的输出也跟着变。调大上下文不是打开开关，是换一套位置编码。
