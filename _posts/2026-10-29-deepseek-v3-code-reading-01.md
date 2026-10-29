---
layout:     post
title:      DeepSeek-V3 源码精读（1）：809 行里的 671B
subtitle:   先画地图，再算账——读懂一个前沿模型，从搞清哪一百行最值钱开始
date:       2026-10-29
author:     WY
header-img: img/post-bg-hacker.jpg
catalog:    true
description: 把 809 行拆成三层结构，标出真正要先读的 114 行；再用一份 config 手算出 671.0B 参数与 37.4B 激活，和官方数字对上。
math:       true
series:     deepseek-source
chapter:    1
source:     deepseek-ai/DeepSeek-V3@9b4e9788e4a3
tags:
    - 人工智能
    - 深度学习
    - 大语言模型
    - 神经网络
---

## 开篇：为什么从这 809 行开始

论文告诉你模型「设计成什么样」，代码告诉你它「实际上怎么跑」。这两件事的差距，往往比想象中大：论文里一句「我们使用了多头潜在注意力」，落到代码里是 104 行的张量变形；论文里一句「采用无辅助损失的负载均衡」，落到代码里是 `Gate.forward` 里三行不起眼的加法。

我挑 DeepSeek-V3 的推理仓库做这个系列的第一季，理由很实际：它的完整前向只有 **809 行、16 个顶层定义**，一个文件。这在今天的前沿模型里几乎绝迹——大多数开源仓库要么只放模型卡和 demo 脚本，核心网络藏在别人的框架里；要么一上来就是几十万行分布式训练代码。V3 是罕见的「一个文件读完一个前沿模型」。

代价是它只包含推理，没有训练。但对读代码来说这反而是好事：训练代码里八成的篇幅在处理数据管道、并行策略和断点续训，跟「模型长什么样」关系不大。

这一期不碰任何数学细节，只做两件事：**把 809 行画成一张地图**，告诉你先读哪 114 行；**把 671B 这个数字算一遍**，让你知道它到底由什么堆出来。

## 第 1 期：先把地图画出来

### 1. 十六个定义，其实是三层

打开 `inference/model.py`，从头翻到尾，会遇到 13 个类和 3 个函数。它们不是平铺的，而是清清楚楚的三层。

| 层 | 定义 | 行号 | 行数 | 职责 |
|---|---|---|---|---|
| 入口 | `ModelArgs` | {% include src.html path="inference/model.py" lines="20-88" %} | 69 | 全部超参，一个 dataclass |
| 入口 | `Block` | {% include src.html path="inference/model.py" lines="696-737" %} | 42 | 一层 = 注意力 + 前馈 + 两个 norm |
| 入口 | `Transformer` | {% include src.html path="inference/model.py" lines="738-809" %} | 72 | 嵌入 → 61 层 → norm → 输出头 |
| 结构 | `MLA` | {% include src.html path="inference/model.py" lines="396-499" %} | 104 | 注意力本体 |
| 结构 | `MLP` | {% include src.html path="inference/model.py" lines="500-534" %} | 35 | 稠密前馈（只在前 3 层） |
| 结构 | `Gate` | {% include src.html path="inference/model.py" lines="535-600" %} | 66 | 路由打分 |
| 结构 | `Expert` | {% include src.html path="inference/model.py" lines="601-635" %} | 35 | 单个专家 |
| 结构 | `MoE` | {% include src.html path="inference/model.py" lines="636-695" %} | 60 | 256 个专家怎么组织 |
| 算子 | `ParallelEmbedding` | {% include src.html path="inference/model.py" lines="89-130" %} | 42 | 词表并行的嵌入 |
| 算子 | `linear` | {% include src.html path="inference/model.py" lines="131-165" %} | 35 | 带 fp8 分支的矩阵乘 |
| 算子 | `Linear` | {% include src.html path="inference/model.py" lines="166-207" %} | 42 | 自定义线性层基类 |
| 算子 | `ColumnParallelLinear` | {% include src.html path="inference/model.py" lines="208-236" %} | 29 | 切输出维 |
| 算子 | `RowParallelLinear` | {% include src.html path="inference/model.py" lines="237-269" %} | 33 | 切输入维 |
| 算子 | `RMSNorm` | {% include src.html path="inference/model.py" lines="270-296" %} | 27 | 归一化 |
| 算子 | `precompute_freqs_cis` | {% include src.html path="inference/model.py" lines="297-377" %} | 81 | 预计算旋转位置编码 |
| 算子 | `apply_rotary_emb` | {% include src.html path="inference/model.py" lines="378-395" %} | 18 | 把 RoPE 应用到 q/k |

把行数加起来：入口层 183 行，结构层 300 行，算子层 307 行，加上 `ModelArgs` 的 69 行，是 859——比 809 多，因为相邻定义的边界行有重叠（每个类的最后一行既是本类的结尾，也是下一个定义之前的空行）。真正的有效代码约 790 行，剩下的是空行。

**阅读顺序不是从上往下。** 我的建议是：先读入口层的 114 行（`Block` 42 + `Transformer` 72），因为它是骨架；再读结构层的 300 行，那是这个模型真正的创新；算子层留到最后按需查——你不需要知道 `ColumnParallelLinear` 怎么实现，也能理解 MLA 在做什么。

按这个顺序，第一遍花的时间大致是这样：

| 遍次 | 读什么 | 读完应该知道 | 大概耗时 |
|---|---|---|---|
| 第一遍 | `Block` {% include src.html path="inference/model.py" lines="696-737" %} | 一层里有哪两个子层、残差加在哪 | 10 分钟 |
| 第二遍 | `Transformer` {% include src.html path="inference/model.py" lines="738-809" %} | 61 层怎么串、输出怎么取、mask 什么时候建 | 15 分钟 |
| 第三遍 | `MLA` + `MoE` | 创新点在哪、和标准 Transformer 差在哪 | 40 分钟 |
| 随时查 | 算子层 307 行 | 某个变换的精确公式 | 按需 |

第一遍不要试图看懂 `MLA`。它 104 行里有大量形状变换，在不知道「它要解决什么问题」之前读，只会记住一堆变量名。

### 2. 一条 shape 流水线

读代码卡住的时候，九成是因为不知道此刻手里的张量是什么形状。所以每期我都会先给一条流水线。

以一次单条推理（batch 里只有一个序列）为例，输入是 `[1, S]` 的 token id：

| 步骤 | 代码位置 | 张量形状 |
|---|---|---|
| 输入 token id | `forward` 参数 | `[1, S]`，int64 |
| 词嵌入 | {% include src.html path="inference/model.py" lines="785" text="785 行" %} `self.embed(tokens)` | `[1, S, 7168]` |
| 61 个 Block | {% include src.html path="inference/model.py" lines="790-791" text="790-791 行" %}循环 | `[1, S, 7168]` 不变 |
| 取最后一个位置 | {% include src.html path="inference/model.py" lines="792" text="792 行" %} `[:, -1]` | `[1, 7168]` |
| 输出头投影 | {% include src.html path="inference/model.py" lines="793" text="793 行" %} `self.head(h)` | `[1, 129280]` |

注意倒数第二步。**`Transformer.forward` 返回的不是每个位置的分布，只是最后一个位置的分布。** 这一行决定了整个仓库的性质：它是推理实现，不是训练实现。训练时要对每个位置算损失，绝不会在这里切片。

想亲手验证这条流水线，文件末尾 {% include src.html path="inference/model.py" lines="801-808" text="801-808 行" %}有个五行的小测试：造一个 `[2, 128]` 的随机 token，跑一遍 `Transformer`，打印输出尺寸。它默认跑不起来（`device` 写死成 `cuda`），但改成 `cpu` 就能跑。跑完你会发现输出是 `(2, 102400)` 而不是 `(2, 128, 102400)`——两件事同时被验证了：切片确实发生了，而且这里用的是 `ModelArgs` 的**默认词表大小**，不是 129280。这个细节下一节就会用到。

### 3. 骨架的 72 行

`Transformer` 是整份代码里最值得逐行读的一段，因为它把所有零件按正确顺序拧在一起。摘出核心的二十来行：

```python
# inference/model.py:757-770, 784-798
global world_size, rank
world_size = dist.get_world_size() if dist.is_initialized() else 1
rank = dist.get_rank() if dist.is_initialized() else 0
Linear.dtype = torch.float8_e4m3fn if args.dtype == "fp8" else torch.bfloat16
Linear.scale_fmt = args.scale_fmt
super().__init__()
self.max_seq_len = args.max_seq_len
self.embed = ParallelEmbedding(args.vocab_size, args.dim)
self.layers = torch.nn.ModuleList()
for layer_id in range(args.n_layers):
    self.layers.append(Block(layer_id, args))
self.norm = RMSNorm(args.dim)
self.head = ColumnParallelLinear(args.dim, args.vocab_size, dtype=torch.get_default_dtype())
self.register_buffer("freqs_cis", precompute_freqs_cis(args), persistent=False)

# ---- forward ----
seqlen = tokens.size(1)
h = self.embed(tokens)
freqs_cis = self.freqs_cis[start_pos:start_pos+seqlen]
mask = None
if seqlen > 1:
    mask = torch.full((seqlen, seqlen), float("-inf"), device=tokens.device).triu_(1)
for layer in self.layers:
    h = layer(h, start_pos, freqs_cis, mask)
h = self.norm(h)[:, -1]
logits = self.head(h)
```

这二十几行里有三个细节，值得单独拎出来。

**第一，`Linear.dtype` 是类属性，不是实例属性。** {% include src.html path="inference/model.py" lines="760" text="第 760 行" %}给 `Linear` 这个类赋了一个类型，之后所有 `Linear` 子类实例都用它。含义是：一个进程里只能有一种精度，你不能让第 1 层跑 bf16、第 30 层跑 fp8。这是刻意的简化——真实部署里按层混精度的收益很小，而分支判断的成本会渗到每一次矩阵乘里。

**第二，`freqs_cis` 用了 `persistent=False`。** 这个 buffer 不进 `state_dict`，也就是说它不会被存进 checkpoint。好处是：你改了 `rope_theta` 或 `rope_factor`，不需要重新保存一遍权重；坏处是：加载权重时如果忘了重新算，位置编码会静默错位。第 3 期会专门讲这 81 行。

**第三，mask 只在 `seqlen > 1` 时构造。** 解码阶段每次只喂一个 token，`seqlen == 1`，因果掩码没有任何意义（一个位置不需要遮谁），直接传 `None`。这一行省下的是每一步都分配一个 `S × S` 矩阵的开销——在长上下文里这不是小数。

#### 顺带看一眼 Block 的那两行

`Block.forward` 一共只有两行有效代码，但这两行决定了整个模型能不能训练得动：

```python
# inference/model.py:733-734
x = x + self.attn(self.attn_norm(x), start_pos, freqs_cis, mask)
x = x + self.ffn(self.ffn_norm(x))
```

读法是：先把 `x` 归一化再送进子层，子层的输出加回**原始的** `x`。这叫 pre-norm。

对比另一种写法 post-norm：`x = norm(x + f(x))`。两者数学上都能用，但 pre-norm 的梯度回传路径上只有加法，没有任何缩放或归一化操作，61 层叠起来也不会把梯度压没或放大。post-norm 在深层网络里需要靠 warmup 精心调学习率才能训起来，这是 2019 年前后一批论文反复踩的坑。

代码里不会解释这个选择，但一眼就能看出来：**归一化放在子层入口，不放在残差出口。** 看到这个结构，你就知道作者选了更稳的那条路。

### 4. 算账：671B 是怎么堆出来的

现在做一件很少有人认真做的事：不查任何资料，只用配置文件把参数量算出来。

`inference/configs/config_671B.json` 里的关键数字是：`dim=7168`、`inter_dim=18432`、`moe_inter_dim=2048`、`n_layers=61`、`n_dense_layers=3`、`n_heads=128`、`n_routed_experts=256`、`n_activated_experts=8`、`kv_lora_rank=512`、`q_lora_rank=1536`、`vocab_size=129280`。

先算一个专家。每个专家是一个标准的三矩阵前馈：

$$
N_{\text{expert}} = 3 \times d \times d_{\text{moe}} = 3 \times 7168 \times 2048 = 44{,}040{,}192 \approx 0.044\text{ B}
$$

再算一层 MLA。它是四个投影加一个输出投影（第 5 期会拆开看），合计约 0.187B。一层稠密 MLP 是 $3 \times 7168 \times 18432 \approx 0.396$ B，所以一个稠密层总共 0.583B，三层就是 1.750B。

难点在 MoE 层：它包含一层 MLA（0.187B）、一个共享专家（0.044B）、256 个路由专家（$256 \times 0.044 = 11.274$ B）、以及一个 7168×256 的门控（0.0018B），合计 11.507B。这样的层有 58 个。

| 组成 | 数量 | 小计 |
|---|---|---|
| 词嵌入 | 129280 × 7168 | 0.927 B |
| 输出头 | 129280 × 7168 | 0.927 B |
| 稠密层 | 3 × 0.5835 B | 1.750 B |
| MoE 层 | 58 × 11.507 B | 667.42 B |
| **合计** | | **671.0 B** |

和官方公布的 671B 对上了，一位小数都不差。

但真正有意思的是下一行：**每个 token 只激活 37.4B**。算法是每层只算 8 个路由专家加 1 个共享专家，58 层加起来 33.84B，再加稠密层和嵌入输出头，得到 37.4B——占总量 5.6%。

这里藏着一个设计上的巧合，或者说不是巧合。稠密层的中间维是 18432，MoE 的专家中间维是 2048，而

$$
2048 \times (8 + 1) = 18432
$$

**八个路由专家加一个共享专家，宽度刚好等于一个稠密前馈。** 也就是说，MoE 层每个 token 的实际计算量和稠密层完全一样，变的只是「这 18432 的宽度由谁来填」——稠密层用一组固定的权重，MoE 用 256 组里挑 9 组。参数涨了 19 倍，算力一点没涨。这就是 MoE 的全部秘密，也是为什么它能把 671B 塞进一张卡的算力预算里（显存另说）。

### 5. 三个常见误解

**误解一：`ModelArgs` 里的默认值就是 V3 的配置。**

打开 `ModelArgs`，你会看到 `dim: int = 2048`、`n_layers: int = 27`、`n_routed_experts: int = 64`、`vocab_size: int = 102400`。按这组默认值算出来是个 2B 左右的小模型。

真实配置在别处。`generate.py` {% include src.html path="inference/generate.py" lines="113" text="第 113 行" %}只有一句：

```python
args = ModelArgs(**json.load(f))   # f 指向 --config 指定的 json
```

`config_671B.json` 把所有字段覆盖成 7168 / 61 / 256 / 129280。如果谁照着默认值去理解代码，会得出完全错误的结论——这是我在这个仓库里见过最容易被踩的坑。

**误解二：671B 就是每个 token 的计算量。**

不是，是 37.4B。参数量和浮点运算量在 MoE 里是两件事：参数决定显存，激活决定算力。把这两者混为一谈，会得出「671B 模型必须 671B 的算力」这种明显不成立的结论。

**误解三：809 行意味着模型简单。**

恰恰相反。809 行省掉的是「怎么把 61 层高效地摊到几千张卡上」——那部分在 `kernel.py`、在 DeepEP、在 FlashMLA 里，不在这个文件。这个文件只负责说清楚「数学上发生什么」，不负责「工程上怎么快」。两者合起来才是完整的模型。

### 6. 在 CPU 上把这笔账重算一遍

下面这段脚本不需要 torch，不需要 GPU，只用标准库，四十行。把它存成 `params.py`，放在 `inference/` 目录下运行，就能复现上面那张表。

```python
import json

cfg = json.load(open("configs/config_671B.json"))
d, inter, moe_i = cfg["dim"], cfg["inter_dim"], cfg["moe_inter_dim"]
L, Ld = cfg["n_layers"], cfg["n_dense_layers"]
H, V = cfg["n_heads"], cfg["vocab_size"]
qk_n, qk_r, vh = cfg["qk_nope_head_dim"], cfg["qk_rope_head_dim"], cfg["v_head_dim"]
kvr, qkr = cfg["kv_lora_rank"], cfg["q_lora_rank"]
E, topk = cfg["n_routed_experts"], cfg["n_activated_experts"]

def mla():                       # 四个投影 + 输出投影
    return (d * qkr + qkr) + (qkr * H * (qk_n + qk_r)) \
         + (d * (kvr + qk_r) + kvr) + (kvr * H * (qk_n + vh)) + (H * vh) * d

def expert():                    # 一个专家的三个矩阵
    return 3 * d * moe_i

dense = Ld * (mla() + 3 * d * inter)
moe   = (L - Ld) * (mla() + expert() + E * expert() + d * E)
emb   = V * d
active = dense + (L - Ld) * (mla() + expert() + topk * expert()) + 2 * emb

print(f"单层 MLA {mla()/1e9:.4f} B")
print(f"稠密层   {dense/1e9:.3f} B")
print(f"MoE 层   {moe/1e9:.2f} B")
print(f"总参数   {(dense+moe+2*emb)/1e9:.1f} B")
print(f"每 token {active/1e9:.1f} B")
```

实际输出：

```
单层 MLA 0.1871 B
稠密层   1.750 B
MoE 层   667.42 B
总参数   671.0 B
每 token 37.4 B
```

如果你把这五行对上官方数字，会有一个小小的成就感——**你没有相信任何人，你自己算出来的。**

顺便说一句，`n_dense_layers` 这个字段的用法在 `Block.__init__` {% include src.html path="inference/model.py" lines="716" text="第 716 行" %}：

```python
self.ffn = MLP(args.dim, args.inter_dim) if layer_id < args.n_dense_layers else MoE(args)
```

前 3 层是稠密前馈，第 4 层开始换成 MoE。为什么要留三层稠密？因为最底层的表示还没成形，路由在噪声上做选择会很不稳定。这是一个工程判断，不是理论推导——代码里不会写理由，这也是读源码和读论文的差别之一。

#### 三个自测问题

地图画完了，用三个问题检查一下读没读懂。答得上来，这一期就没白读：

1. `Transformer.forward` {% include src.html path="inference/model.py" lines="792" text="第 792 行" %}为什么是 `self.norm(h)[:, -1]` 而不是 `self.norm(h)`？如果去掉 `[:, -1]`，输出的形状会变成什么，后续哪一步会崩？
2. 解码阶段 `mask` 传的是 `None`。如果误传了一个 `1 × 1` 的掩码矩阵，结果会错吗？为什么？
3. 671B 里，哪一部分占了 99.4%？如果要把模型压到 100B 以内，最直接的一刀砍在哪？

第三个问题的答案在表格里：MoE 层 667.42B，占 99.4%。把专家数从 256 砍到 128，参数量从 671B 掉到 344B，几乎腰斩；而每 token 的激活量**一点没变，还是 37.4B**——因为激活量由 `topk=8` 决定，跟专家总数无关。

这就是 MoE 最反直觉的地方：**决定显存的是专家总数，决定算力的是 topk。** 两者可以独立调。也正是因为可以独立调，MoE 才值得那么多工程投入去解决显存和通信问题。

### 7. 下期预告

地图画完了，下一期从最小的零件开始拆：`linear()` 那 35 行里有 fp8 分支，`ColumnParallelLinear` 和 `RowParallelLinear` 各三十行左右，两个加起来构成张量并行的地基。

真正要回答的问题是：**为什么把权重切一刀，就必然多出一次通信？** 切开的是哪一维、通信发生在哪一步、all-reduce 能不能省——这些在论文里通常只有一句话，在代码里是二三十行，而且能自己算清楚。

---

## 小结

- 809 行里真正要先读的是入口层 114 行：`Block` 42 行说明一层长什么样，`Transformer` 72 行说明 61 层怎么串起来。算子层留到需要时再查。
- `Transformer.forward` 只返回最后一个位置的 logits（{% include src.html path="inference/model.py" lines="792" text="第 792 行" %}），这个细节决定了整个仓库是推理实现而非训练实现；`Linear.dtype` 是类属性，一个进程只能有一种精度。
- 671.0B 与 37.4B 都能从一份 config 手算出来，误差在小数点后一位。MoE 的部分之所以划算，是因为 8 个路由专家加 1 个共享专家的宽度恰好等于一个稠密前馈（2048 × 9 = 18432）——参数涨 19 倍，算力不涨。
