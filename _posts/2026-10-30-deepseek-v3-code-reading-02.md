---
layout:     post
title:      DeepSeek-V3 源码精读（2）：切一刀为什么要 all-reduce
subtitle:   张量并行不是把模型切成八份，而是把两种切法接成一条接力
date:       2026-10-30
author:     WY
header-img: img/post-bg-os-metro.jpg
catalog:    true
description: 从 linear() 的三条分支读到 Column/Row 的配对：切输出维前向零通信，切输入维必须 all-reduce，再把 tp=8 下每卡 19.6% 的权重与 18.5 GB 的通信账算清楚。
math:       true
series:     deepseek-source
chapter:    2
source:     deepseek-ai/DeepSeek-V3@9b4e9788e4a3
tags:
    - 人工智能
    - 深度学习
    - 大语言模型
    - 神经网络
---

## 开篇：137 行里的那一次 all-reduce

上一篇把 809 行拆成三层，结尾留了一句预告：下一期从最小的零件开始拆。这一期就拆 131 到 267 行，一共 137 行——一个函数 `linear()`、一个基类 `Linear`、两个只差十几行的子类。

读之前先备三条前置知识，缺一条都会卡住：

1. 矩阵乘 `Y = X W^T` 里，`W` 的两个维度分别对应「输出」和「输入」，切哪一个都合法，但代价完全不同。
2. `dist.all_reduce` 默认做的是**逐元素求和**，不是拼接。整篇文章的困惑有一半来自把这两件事混为一谈。
3. `world_size` 不是配置项，是全局变量，在 `Transformer.__init__` 的 {% include src.html path="inference/model.py" lines="757-759" text="757-759 行" %}才被赋值。这意味着所有并行层的构造函数，必须在模型对象构造之后才读得到它——这也解释了为什么这些类没有把 `world_size` 做成参数。

这期只回答一个问题：**切开权重的那一刀，通信费记在谁头上。** fp8 怎么量化、MLA 怎么吸收、MoE 怎么路由，全部略过。

## 第 2 期：切一刀，账记在谁头上

### 1. 三个理由，让作者重写了 nn.Linear

`torch.nn.Linear` 能做的事，这 40 行自己又写了一遍。不是为了炫技，`Linear.__init__` 里有三个绕不开的理由。

**第一，权重可能是 fp8。** {% include src.html path="inference/model.py" lines="184-187" text="184-187 行" %}写的是：如果 `self.weight.element_size() == 1`，就给这个层额外挂一个 fp32 的 `scale` 参数，形状是 `[out/128, in/128]`。一个参数只占一字节，等于宣告它是 fp8，那它必然带着一张分块的缩放表。fp8 模式下 `nn.Linear` 直接被排除，因为它的 `weight` 只能是浮点类型。

**第二，权重要能切。** `nn.Linear` 里没有「我这个层只负责第 3 到第 4 个头」这种概念。这里用两个子类把「切哪一维」写成类名，构造时把 `out_features` 或 `in_features` 除以 `world_size`，权重张量自然就小了。

**第三，bias 的位置有讲究。** 行并行必须把 bias 留到求和之后再加，晚一步都不行。这个顺序写死在 `forward` 里，`nn.Linear` 也给不了。

三条理由落到同一个出口：`linear()`（{% include src.html path="inference/model.py" lines="131-163" text="131-163 行" %}）。它有且只有三条分支：

```python
# inference/model.py:153-163
if weight.element_size() > 1:                 # 权重是 bf16，直接算
    return F.linear(x, weight, bias)
elif gemm_impl == "bf16":                     # 权重是 fp8，先反量化再用 bf16 算
    weight = weight_dequant(weight, weight.scale)
    return F.linear(x, weight, bias)
else:                                         # 权重是 fp8，激活也量化，走 fp8 GEMM
    x, scale = act_quant(x, block_size, scale_fmt)
    y = fp8_gemm(x, scale, weight, weight.scale)
    if bias is not None:
        y += bias
    return y
```

第 4 期会把这三条分支拆开讲。这里只需要记住一件事：**所有并行层最后都落到这 11 行**，`ColumnParallelLinear` 和 `RowParallelLinear` 的 `forward` 加起来只有 8 行，因为它们把活全甩给了 `linear()`。

还有一个容易看漏的细节。`Linear.forward` 传的是 `linear(x, self.weight, self.bias, self.scale_fmt)`，带上了第四个参数；而两个并行子类的 `forward` 只传三个参数，`scale_fmt` 落到默认值 `None`。`scale_fmt` 唯一的作用在 {% include src.html path="inference/kernel.py" lines="29-31" text="kernel.py 的 29-31 行" %}——把 scale 向上取整到 2 的幂。也就是说，这个开关只对普通 `Linear` 生效。`config_671B.json` 没有设这个字段，默认 `None`，所以当前配置下这段分支是死的。读到这类不一致，先别急着当 bug 报，它更可能是「只在小矩阵上启用」的遗留。

### 2. 切输出维：列并行，前向免费

`ColumnParallelLinear` 的全部秘密在构造函数的那三行。

```python
# inference/model.py:218-234（只删掉了 docstring 与类型标注）
def __init__(self, in_features, out_features, bias=False, dtype=None):
    assert out_features % world_size == 0, f"Output features must be divisible by world_size (world_size={world_size})"
    self.part_out_features = out_features // world_size
    super().__init__(in_features, self.part_out_features, bias, dtype)

def forward(self, x):
    y = linear(x, self.weight, self.bias)
    return y
```

`out_features` 被除掉了，`in_features` 一点没动。于是每个 rank 的权重是 `[out/8, in]`，输入 `x` 是完整的 `[..., in]`，输出是 `[..., out/8]`。

数学上就是把结果按行切开：

$$
Y = \mathrm{Concat}(Y_1, Y_2, \dots, Y_8), \qquad Y_r = X W_r^{\top}
$$

八个 rank 各自算出一块，拼起来正好是完整的 `Y`。**拼接不是通信的必然理由，因为没有任何一个 rank 需要别人的结果才能算自己的那一块。** 所以这个 `forward` 里一行通信代码都没有——不是作者忘了写，是它真的不需要。

名字值得较真一下。「列并行」说的是切 `W` 的列，而 PyTorch 存的是 `[out, in]`，`W` 的列恰好就是输出维。Megatron 的论文按 `[in, out]` 的记法命名，落到 PyTorch 的张量布局上就变成了「切第 0 维」。第一次读容易在这里绕不出来。

在 MLA 里，切输出维等于**切头**。{% include src.html path="inference/model.py" lines="416" text="第 416 行" %}先算出 `self.n_local_heads = args.n_heads // world_size`，`wq_b` 的输出就是 `n_heads * qk_head_dim`；切完之后本卡拿到 16 个头，`q.view(bsz, seqlen, self.n_local_heads, self.qk_head_dim)`（{% include src.html path="inference/model.py" lines="465" text="第 465 行" %}）正好把 3072 拆成 16 × 192。**这里 `view` 用的是本地头数而不是总头数，本身就是一道形状自校验**：如果切分算错了，这一行会立刻报错，不会让错误悄悄流到下游。

顺带一个 assert 挡不住的坑。`assert out_features % world_size == 0` 只检查了整除，没检查头数。`wq_b` 的输出是 24576，`world_size = 3` 时能整除（8192），assert 通过；但 `n_heads // 3 = 42`，42 × 192 = 8064，与 8192 对不上，`view` 当场崩。**真正的要求是 `n_heads % world_size == 0`，代码只校验了它的一个必要条件。**

### 3. 切输入维：行并行，前向付费

`RowParallelLinear` 把除数换到了另一边，并且多了一行 `all_reduce`。

```python
# inference/model.py:247-267（只删掉了 docstring 与类型标注）
def __init__(self, in_features, out_features, bias=False, dtype=None):
    assert in_features % world_size == 0, f"Input features must be divisible by world_size (world_size={world_size})"
    self.part_in_features = in_features // world_size
    super().__init__(self.part_in_features, out_features, bias, dtype)

def forward(self, x):
    y = linear(x, self.weight)
    if world_size > 1:
        dist.all_reduce(y)
    if self.bias is not None:
        y += self.bias
    return y
```

这次切的是求和的那个维度（矩阵乘里的 K 维）。每个 rank 只拿到 `x` 的一段、`W` 的一段，算出来的东西**不是结果的一部分，而是结果的一部分和**：

$$
Y = \sum_{r=1}^{8} X_r W_r^{\top}
$$

八个部分和必须相加才等于 `Y`。这就是全部答案：**切输出维是「拼」，切输入维是「加」，只有「加」需要 all-reduce。** 一句话能说清，但它在代码里的表现形式是两个几乎一样的类，只差一行。

`forward` 里还有两个细节，都是写错的常见位置。

**`linear(x, self.weight)` 没有传 bias。** 如果传了，每个 rank 都会加一次 `bias`，`all_reduce` 之后变成 8 倍。正确写法是求和之后加一次，{% include src.html path="inference/model.py" lines="265-266" text="265-266 行" %}就是这个顺序。

**`world_size > 1` 的判断不是为了省一次通信。** 单卡时 `dist` 根本没初始化，直接调 `all_reduce` 会抛异常。这个判断是给「不启分布式也能跑」留的后门——`model.py` 末尾那段自测正是这么跑的。

### 4. 一条 shape 流水线（tp=8）

把上面两层接起来，MLA 在 8 卡上的完整形状变化是这样。取 `batch=1`、`seqlen=4` 做一次预填充：

| 步骤 | 代码位置 | 全局形状 | 本卡形状 |
|---|---|---|---|
| 进入 Block | {% include src.html path="inference/model.py" lines="733" text="733 行" %} | `[1, 4, 7168]` | 同（复制） |
| `wq_a` 普通 Linear | {% include src.html path="inference/model.py" lines="427" text="427 行" %} | `[1, 4, 1536]` | 同（复制） |
| `q_norm` | {% include src.html path="inference/model.py" lines="428" text="428 行" %} | `[1, 4, 1536]` | 同（复制） |
| `wq_b` 列并行 | {% include src.html path="inference/model.py" lines="429" text="429 行" %} | `[1, 4, 24576]` | `[1, 4, 3072]` |
| `view` 拆头 | {% include src.html path="inference/model.py" lines="465" text="465 行" %} | `[1, 4, 128, 192]` | `[1, 4, 16, 192]` |
| `wkv_a` 普通 Linear | {% include src.html path="inference/model.py" lines="430" text="430 行" %} | `[1, 4, 576]` | 同（复制） |
| `kv_norm` | {% include src.html path="inference/model.py" lines="431" text="431 行" %} | `[1, 4, 512]` | 同（复制） |
| `wkv_b` 列并行 | {% include src.html path="inference/model.py" lines="432" text="432 行" %} | `[1, 4, 32768]` | `[1, 4, 4096]` |
| 算完注意力 | {% include src.html path="inference/model.py" lines="496" text="496 行" %} | `[1, 4, 16384]` | `[1, 4, 2048]` |
| `wo` 行并行 | {% include src.html path="inference/model.py" lines="433" text="433 行" %} | `[1, 4, 7168]` | 部分和 → all-reduce |

最后一行是这期最值得盯住的地方。`x.flatten(2)` 把 `[1, 4, 16, 128]` 压成 `[1, 4, 2048]`，而 `wo` 的 `part_in_features` 是 `16384 / 8 = 2048`。**两个数字都是 2048，但来源完全不同**：一个是「本卡 16 个头 × 128 维」，一个是「16384 除以 8」。它们相等不是巧合，是因为头被均匀切成了 8 份。

稠密层里的 MLP 是同一个套路，只是顺序反过来：`w1` 和 `w3` 列并行（`7168 → 2304`），`w2` 行并行（`2304 → 7168`）。`silu(w1(x)) * w3(x)` 是逐元素乘，只要两边切的是同一批维度，就不需要任何通信。

### 5. 配对的真正原因：切分维度要接力

现在可以回答「为什么这两层总是成对出现」了。

不是风格问题，是**接口约定**：上一层的输出切法，必须等于下一层的输入切法。`ColumnParallelLinear` 输出 `[..., N/8]`，`RowParallelLinear` 的输入要求 `[..., K/8]`，两者能直接对接的前提是 `N = K` 且切的是同一段。

头切分保证了这一点。`wq_b` 按头切输出（16 个头），注意力的输出自然还是这 16 个头，`wo` 的输入正好等于这 16 个头的拼接。**中间一次 all-gather 都不需要。**

反过来，如果 `wq_b` 按头切、`wo` 按 hidden 维均分成 8 段（`896` 一段），两段对不上，就必须在中间插一次 `all_gather`，通信量翻倍。这就是为什么 `Column` 和 `Row` 在代码里永远相邻——它们不是两个选择，是一个协议的两半。

按这个标准再看 MLA 的两个低秩投影，会发现它们**故意没有并行**：

- `wq_a`：`Linear(7168, 1536)`，每卡都存完整的 1101 万参数
- `wkv_a`：`Linear(7168, 576)`，每卡都存完整的 413 万参数

为什么不切？因为它们后面紧跟 `RMSNorm`。**归一化是非线性的，必须看到完整向量才能算出正确的均方根。** 如果把 `wq_a` 的输出切了，`q_norm` 拿到的只是 1/8 的 1536 维，算出来的 RMS 是错的，而且错得很隐蔽——形状完全合法。

这条约束可以推广成一句话：**归一化层是张量并行的天然边界。** 想跨过它，就得付一次 all-reduce 把向量拼回来，而 `wq_a` 省下的显存是 9.6M 参数（fp8 下约 9.6 MB），多出的通信是每层每 token 1536 维。这笔账怎么算都不划算，所以作者选了复制。

### 6. 三个常见误解

**误解一：张量并行就是把模型切成 8 份，每卡 1/8。**

纯 MLP 上确实如此：`w1`、`w2`、`w3` 三个矩阵都切了，每卡正好 12.5%。但 MLA 不是。复制部分 1514 万参数，占整层 1.871 亿的 8.1%，却占了每卡 3663 万的 **41.3%**；折算下来每卡是 19.6%，比理想的 12.5% 高出 57%。

有趣的是，整个模型的每卡权重仍然接近 671/8。原因是专家占了 99%，而 `Expert` 用的是普通 `Linear`（{% include src.html path="inference/model.py" lines="619-621" text="619-621 行" %}）——专家并行切的是**专家列表**，不是矩阵，每个 rank 整整拿 32 个完整专家。所以「671 除以 8」这个估算在本模型上只差 1%，但它是被 MoE 的结构救了，不是被张量并行救了。换一个稠密模型，同样的近似会差 50% 以上。

顺带说清一件事：同一个文件里同时存在两种并行。MLA 和 MLP 用张量并行（切矩阵），MoE 用专家并行（切专家），`ParallelEmbedding` 用词表并行（切词表）。第 8 期讲前两种怎么在同一层里共存。

**误解二：列并行也要 all-reduce。**

前向不需要，反向需要。这是训练和推理的分野。

`ColumnParallelLinear` 的 `forward` 里确实没有通信（{% include src.html path="inference/model.py" lines="233-234" text="233-234 行" %}），但训练时反向传播要算 `dX = dY W`，而每个 rank 只有自己那 1/8 的 `dY`，必须先 all-reduce 才能得到完整的 `dX`。行并行正好相反：前向要 all-reduce，反向白拿。

所以一对 `Column → Row`，前向一次通信、反向一次通信，总数是 2，一次都省不掉，只是记在了不同的账本上。**推理只跑前向，所以这一对只付一次——这才是「推理能用张量并行省钱」的真实原因，而不是「切开了就不用通信」。**

**误解三：all-reduce 就是把各卡的结果拼起来。**

拼接是 `all_gather`，求和是 `all_reduce`。这个文件里两者都出现了，位置刚好说明区别：

- 切输入维 → 求和。{% include src.html path="inference/model.py" lines="264" text="第 264 行" %} `dist.all_reduce(y)`
- 切输出维 → 拼接。{% include src.html path="inference/model.py" lines="794-797" text="794-797 行" %} 输出头按词表切开，`all_gather` 之后 `torch.cat`

如果把第 264 行换成 `all_gather`，形状会从 `[1, 4, 7168]` 变成 `[8, 1, 4, 7168]`，后面的残差加法直接报错；如果把它换成「只取 rank 0 的结果」，形状对、数值错，误差量级和输入本身一样大。**形状对不代表算对了**，这是张量并行最难查的一类 bug。

#### 把这四行放在一起看

整个 `model.py` 里只有四处集合通信，扫一遍就能确认没有遗漏：

| 行号 | 所在位置 | 操作 | 为什么需要 |
|---|---|---|---|
| {% include src.html path="inference/model.py" lines="127" text="127" %} | `ParallelEmbedding` | all_reduce 求和 | 词表切开，非本卡的 token 被置零，求和等价于拼接 |
| {% include src.html path="inference/model.py" lines="264" text="264" %} | `RowParallelLinear` | all_reduce 求和 | 切 K 维产生部分和 |
| {% include src.html path="inference/model.py" lines="692" text="692" %} | `MoE` | all_reduce 求和 | 专家按 rank 分工，各卡只算自己那几个专家 |
| {% include src.html path="inference/model.py" lines="794-797" text="794-797" %} | 输出头 | all_gather + cat | 按词表切了输出维，要拼不要和 |

第一行最容易被误解成「嵌入层也需要张量并行」。它是词表并行：每个 rank 只存 `vocab/8` 行，遇到不属于自己的 token，查表结果先置零（{% include src.html path="inference/model.py" lines="121-123" text="121-123 行" %}），查完之后再把不属于自己的位置清零一遍，最后 all_reduce。**七个零加一个真值，和是那个真值**——这里用求和，纯粹是因为求和能把「拼接」冒充成「加法」，省掉一次 all_gather 的显式形状处理。同一个算子，三种动机，读的时候要分清是哪一种。

第三行的 MoE 还有个顺序陷阱，第 8 期会展开：`all_reduce(y)` 必须发生在加共享专家之前（{% include src.html path="inference/model.py" lines="690-693" text="690-693 行" %}），否则共享专家的输出会被加 8 次。和 `RowParallelLinear` 里 bias 的位置是同一类错误。

### 7. 在 CPU 上把两种切法跑一遍

下面这段不需要 GPU、不需要 torch、不需要 numpy，纯标准库，五十来行。存成 `tp.py` 直接跑。

```python
import random
random.seed(11)

TP, IN, OUT = 4, 8, 6
W = [[random.uniform(-1, 1) for _ in range(IN)] for _ in range(OUT)]   # [OUT, IN]
x = [random.uniform(-1, 1) for _ in range(IN)]
b = [random.uniform(-0.5, 0.5) for _ in range(OUT)]

def dot(a, c):
    return sum(i * j for i, j in zip(a, c))

ref = [dot(x, row) + bi for row, bi in zip(W, b)]      # 单卡的标准答案

def column_parallel():                                 # 切输出维
    step, out = OUT // TP, []
    for r in range(TP):
        for k, row in enumerate(W[r*step:(r+1)*step]):
            out.append(dot(x, row) + b[r*step + k])
    return out                                         # 通信 0 次

def row_parallel():                                    # 切输入维
    step, part = IN // TP, []
    for r in range(TP):
        xr = x[r*step:(r+1)*step]
        Wr = [row[r*step:(r+1)*step] for row in W]
        part.append([dot(xr, row) for row in Wr])
    y = [sum(p[j] for p in part) for j in range(OUT)]   # all_reduce
    return [y[j] + b[j] for j in range(OUT)]

def row_without_allreduce():                           # 少一步 all_reduce
    step = IN // TP
    xr, Wr = x[:step], [row[:step] for row in W]
    return [dot(xr, row) + b[j] for j, row in enumerate(Wr)]

def row_bias_before_reduce():                          # bias 加早了
    step, part = IN // TP, []
    for r in range(TP):
        xr = x[r*step:(r+1)*step]
        Wr = [row[r*step:(r+1)*step] for row in W]
        part.append([dot(xr, row) + b[j] for j, row in enumerate(Wr)])
    return [sum(p[j] for p in part) for j in range(OUT)]

def gap(a, c):
    return max(abs(i - j) for i, j in zip(a, c))

print("列并行             max|diff| = %.3e  通信 0 次" % gap(column_parallel(), ref))
print("行并行             max|diff| = %.3e  通信 1 次" % gap(row_parallel(), ref))
print("行并行去掉 allreduce max|diff| = %.3f" % gap(row_without_allreduce(), ref))
print("行并行 bias 先加     max|diff| = %.3f" % gap(row_bias_before_reduce(), ref))

n = OUT * 8
print("一次 all-reduce 载荷 %d B，ring 单卡 %.0f B" % (n, n * 2 * (TP - 1) / TP))
```

实际输出：

```
列并行             max|diff| = 0.000e+00  通信 0 次
行并行             max|diff| = 2.220e-16  通信 1 次
行并行去掉 allreduce max|diff| = 2.082
行并行 bias 先加     max|diff| = 1.281
一次 all-reduce 载荷 48 B，ring 单卡 72 B
```

前两行是 0 和 2.2e-16，都落在浮点舍入的噪声里，说明两种切法在数学上完全等价。第三、四行是故意写错的版本，误差 2.082 和 1.281，和数值本身同量级——**错了不会报错，只会悄悄给你一个看起来正常的数。**

把规模换成真实参数，账就出来了：

```python
d, H, qk_n, qk_r, vh, qkr, kvr, TP = 7168, 128, 128, 64, 128, 1536, 512, 8
wq_a, wkv_a = d*qkr, d*(kvr+qk_r)                    # 复制，不切
wq_b = qkr*H*(qk_n+qk_r); wkv_b = kvr*H*(qk_n+vh); wo = H*vh*d
full = wq_a + wq_b + wkv_a + wkv_b + wo
rank = wq_a + wq_b/TP + wkv_a + wkv_b/TP + wo/TP
print("MLA 总 %.4f B，每卡 %.4f B，占 %.1f%%（理想 12.5%%）" % (full/1e9, rank/1e9, rank/full*100))
print("复制部分占每卡 %.1f%%" % ((wq_a+wkv_a)/rank*100))
S, bpe = 4096, 2                                      # 4096 token，bf16
payload = S*d*bpe
ring = 2*(TP-1)/TP
n_dense, n_moe = 3, 58
n_ar = n_dense * 2 + n_moe * 3                        # 稠密层 2 次，MoE 层 3 次
print("单次 all-reduce %.1f MB，单卡流量 %.1f MB" % (payload/1e6, payload*ring/1e6))
print("%d 次 all-reduce，合计 %.1f GB" % (n_ar, payload*ring*n_ar/1e9))
```

```
MLA 总 0.1871 B，每卡 0.0366 B，占 19.6%（理想 12.5%）
复制部分占每卡 41.3%
单次 all-reduce 58.7 MB，单卡流量 102.8 MB
180 次 all-reduce，合计 18.5 GB
```

`n_ar` 那个 180 值得解释一句。3 个稠密层各 2 次（`wo` 和 `w2`），58 个 MoE 层各 3 次——除了 `wo` 和共享专家的 `w2`，还有 {% include src.html path="inference/model.py" lines="691-692" text="691-692 行" %}那次汇总专家输出的 all_reduce。**MoE 层比稠密层多付一次通信，这是专家并行的固定成本，按激活参数省钱的同时，网络要还债。**

最后一行才是这期真正的数字：一次 4096 token 的预填充，单卡要往外发 18.5 GB——这还是 ring 算法下 `2 × 7 / 8 = 1.75` 倍的理论值，没算拥塞和重传。而这条链路上的每一次通信，都只是在补上一篇文章画地图时那 2048 与 7168 之间的除法。**张量并行的开销不是「多了几次通信」，是「每个 token 的每一层都要把整条 hidden 维搬一遍」。**

#### 三个自测问题

1. `world_size = 4` 时，`wo` 的 `part_in_features` 是多少？如果 `x` 进 `wo` 之前还是 `[1, 4, 128, 128]`（128 个完整头，没按 rank 切），第 496 行的 `x.flatten(2)` 会得到什么形状，在哪一步崩？
2. 把第 127 行的 `dist.all_reduce(y)` 删掉，输出的形状会变吗？数值错在哪里？
3. 把第 265 行的 `y += self.bias` 挪到 `all_reduce` 之前，误差是 bias 的几倍？这个错误在单卡环境能不能测出来？

答案：`wo` 的输入是 16384，除以 4 得 4096；而 `[1, 4, 128, 128]` 压平是 16384，`F.linear` 会在维度匹配处直接报错——**这类错误反而是幸运的**。第二问形状完全不变，但只有 rank 0 负责的那些词有向量，其余位置全是 0，生成结果会退化成乱码。第三问是 4 倍（等于 `world_size`），单卡下 `world_size = 1`，`all_reduce` 被跳过，**这个 bug 在单卡环境里永远测不出来**，只在多卡上线那天集中爆发。

### 8. 下期预告

下一期离开通信，回到单卡内部：`RMSNorm` 那 27 行，加上 `precompute_freqs_cis` 的 81 行。

真正的问题是：**一套按 4096 训练出来的位置编码，怎么在长上下文上还不变形？** YaRN 给了三个参数（`rope_factor`、`beta_fast`、`beta_slow`），代码里都在那 81 行里，而且每个参数改的是不同频段——这件事画一张图就懂了，比读三段论文快。

---

## 小结

- 「切一刀就必然多出一次通信」只说对了一半。切输出维（列并行）前向零通信，切输入维（行并行）前向必须 all-reduce。两者配对，前向一次、反向一次，一次都省不掉，只是记账的位置不同。
- 配对不是风格，是协议：上一层的输出切法必须等于下一层的输入切法。头切分让 `wq_b` 的 3072 和 `wo` 的 2048 严丝合缝地接上，中间不需要 all-gather。而 `wq_a`、`wkv_a` 之所以复制，是因为后面的 `RMSNorm` 必须看到完整向量——归一化层是张量并行的天然边界。
- 三种并行在同一个文件里共存：MLA 和 MLP 切矩阵，`Expert` 切专家列表（用的是普通 `Linear`），`ParallelEmbedding` 切词表。所以「每卡 1/8」在 MLA 上是 19.6%，在全模型上却只差 1%——那是 MoE 的结构救的，不是张量并行切得均匀。
- 验收标准只有一条：形状对不算对。第 264 行的 `all_reduce` 换成 `all_gather` 会当场报错，换成「只取 rank 0」则形状正确、数值全错。后者才是真正难查的 bug。
