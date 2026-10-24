---
layout:     post
title:      Laravel 12 分层：Controller 之外该放什么
subtitle:   用一条“谁会改它”的判据，把 300 行控制器拆成可测的六层
date:       2026-10-24
author:     WY
header-img: img/post-bg-desk.jpg
catalog: true
description: 从一段真实的臃肿控制器出发，给出 Laravel 12 项目的分层判据、六层目录职责、完整拆分对照代码，以及用 PHPStan 与架构测试把边界钉死的可照抄配置。
tags:
    - Laravel
    - PHP
    - 架构设计
---

## 开篇：Controller 变胖的那天

几乎每个 Laravel 项目都会经历同一个下午：一个原本只有 `index` 和 `show` 的控制器，在三个月里被加了渠道判断、费率计算、幂等校验、流水写入、回调推送、审计日志，最后变成 300 行、七个 `if` 嵌套、没有一行能被单测覆盖的巨物。

这时候大多数人做的第一件事是“抽出 Service”。抽完之后体积确实小了，但问题没解决：Service 里开始出现 `Request`、出现 `auth()->user()`、出现 `response()->json()`，业务逻辑和 HTTP 世界依然纠缠在一起。下次需求变更，你还是要打开 Postman 才能验证一段纯计算。

这篇要给的不是“Controller 要薄、Service 要厚”这种正确的废话，而是一套**可以直接落地的判据 + 目录 + 代码 + 强制手段**。读完你应该能回答三个问题：一段代码到底该放在哪一层、为什么要放在那里、以及怎么让团队里的人不把它放回去。

---

## 一、先定判据：按“谁会改它”切层

分层最常见的误区是**按代码形态切**（这段是数组处理、那段是数据库操作），结果切出来的层没有稳定边界。正确做法是按**变更原因**切：一个文件里的所有代码，应该因为同一个理由被修改。

把一次完整请求拆开看，它会依次穿过四个“变更原因”完全不同的世界：

| 世界 | 关心什么 | 谁会让它变 | 变更频率 |
|---|---|---|---|
| 传输层 | HTTP 方法、状态码、序列化、鉴权 | 前端/客户端/网关 | 高 |
| 校验层 | 字段是否合法、格式、范围 | 接口文档 | 高 |
| 用例层 | 业务规则、事务边界、顺序 | 产品/风控/财务 | 中 |
| 持久层 | 表结构、索引、SQL | DBA/迁移 | 低 |

四个世界的变更节奏差一个数量级。把它们塞进一个文件，等于让“改一个字段名”这种最轻的变更，也要重新验证最重的业务逻辑——这就是臃肿控制器的根本成本。

所以判据只有一句：**如果这段代码会因为一个跟前文无关的理由而改动，它就不该和当前代码待在同一个文件里。**

---

## 二、六层目录与职责边界

下面这套结构在 Laravel 12 里不需要任何扩展包，纯 PSR-4 自动加载即可：

```
app/
├── Actions/            # 用例层：一个业务动作一个类
│   └── Payment/
│       ├── CreateCollectionOrder.php
│       └── ResolveChannel.php
├── Data/               # DTO：跨层传递的只读数据对象
│   └── Payment/CreateOrderData.php
├── Enums/              # 枚举：状态、类型，替代魔法字符串
├── Http/
│   ├── Controllers/Api/
│   ├── Requests/
│   └── Resources/
├── Models/             # 持久化模型 + 轻量关系
├── Repositories/       # 数据访问接口
│   ├── Contracts/OrderRepository.php
│   └── EloquentOrderRepository.php
└── Support/            # 无业务语义的工具（Money、Snowflake）
```

### 1. Controller：只做“翻译”

Controller 的唯一职责是把 HTTP 输入翻译成用例调用，再把用例结果翻译回 HTTP。它不应该知道费率怎么算，也不应该知道事务开在哪。

一条硬指标：**Controller 方法不应出现 `DB::`、不应出现 `->save()`、不应超过 20 行。**

### 2. FormRequest：把校验从用例里赶出去

校验是传输层的关切，不是业务规则。`exists:merchants,id` 这种规则属于“客户端传参是否合理”，而“该商户是否被冻结”属于业务规则，两者必须在不同的地方。

```php
<?php

namespace App\Http\Requests\Api;

use Illuminate\Foundation\Http\FormRequest;

final class CreateOrderRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'user_order_no' => ['required', 'string', 'max:64', 'regex:/^[A-Za-z0-9_-]+$/'],
            'amount'        => ['required', 'integer', 'min:1', 'max:50000000'],
            'notify_url'    => ['required', 'url', 'max:255'],
        ];
    }
}
```

金额用 `integer`、单位是“分”，这是后面所有金额计算的前提。不要用 `numeric` 接一个 `12.30`，浮点误差会一路带进账目。

### 3. Action：用例层的主角

Action 是“一个业务动作 = 一个类 = 一个 `handle()`”。它接收 DTO，返回领域对象，不接收 `Request`，不返回 `Response`。事务边界就画在 Action 里，因为“这次操作要原子完成”是业务语义，不是 HTTP 语义。

### 4. Model：持久化模型，不是业务垃圾桶

Eloquent 模型天然适合放关系、作用域、类型转换。但它不适合放“跨多张表、需要外部服务、需要事务”的逻辑。判断方法很简单：模型上的方法如果能被一个 HTTP 请求和一条队列任务同时调用，就说明它其实是用例，应该上移到 Action。

### 5. Repository：只为一件事存在

Repository 不是为了“将来可能换数据库”，而是为了**让用例层可以在没有数据库的情况下被测试**，以及**把复杂查询集中到一处便于优化**。如果项目里只有 CRUD，Repository 是纯负担，可以不要——这一点后面会专门说。

### 6. DTO：跨层不传数组

数组在跨层传递时没有任何契约，`$data['amonut']` 拼错要到运行时才发现。用一个 `readonly` 类承载，PHP 8.2 之后几乎零成本：

```php
<?php

namespace App\Data\Payment;

use App\Http\Requests\Api\CreateOrderRequest;

final readonly class CreateOrderData
{
    public function __construct(
        public int $merchantId,
        public string $userOrderNo,
        public int $amount,      // 单位：分
        public string $notifyUrl,
    ) {}

    public static function fromRequest(CreateOrderRequest $request, int $merchantId): self
    {
        return new self(
            merchantId:  $merchantId,
            userOrderNo: $request->string('user_order_no')->toString(),
            amount:      $request->integer('amount'),
            notifyUrl:   $request->string('notify_url')->toString(),
        );
    }
}
```

---

## 三、把一段 300 行的 Controller 拆开

下面是拆分前的典型形态（为可读性做了压缩，真实版本更长）：

```php
public function store(Request $request)
{
    $data = $request->validate([
        'user_order_no' => 'required|string',
        'amount'        => 'required|numeric',
    ]);

    $merchant = Merchant::findOrFail($request->input('merchant_id'));
    if ($merchant->status !== 1) {
        return response()->json(['code' => 403, 'msg' => '商户不可用'], 403);
    }

    $channel = Channel::where('status', 1)
        ->where('min_amount', '<=', $data['amount'])
        ->where('max_amount', '>=', $data['amount'])
        ->orderByDesc('weight')
        ->first();
    if (! $channel) {
        return response()->json(['code' => 400, 'msg' => '无可用通道'], 400);
    }

    $exists = Order::where('merchant_id', $merchant->id)
        ->where('user_order_no', $data['user_order_no'])->exists();
    if ($exists) {
        return response()->json(['code' => 400, 'msg' => '订单号重复'], 400);
    }

    DB::beginTransaction();
    try {
        $order = Order::create([...]);
        Flow::create(['order_id' => $order->id, 'type' => 'freeze', ...]);
        DB::commit();
    } catch (\Throwable $e) {
        DB::rollBack();
        return response()->json(['code' => 500, 'msg' => '系统繁忙'], 500);
    }

    return response()->json(['code' => 0, 'data' => $order], 201);
}
```

这段代码有五个独立问题：金额用浮点、幂等用“先查再插”（并发下失效）、`beginTransaction` 手写漏了异常重抛、HTTP 响应散落在业务判断里、通道选择规则无法单独测试。

拆分后，Controller 只剩翻译：

```php
<?php

namespace App\Http\Controllers\Api;

use App\Actions\Payment\CreateCollectionOrder;
use App\Data\Payment\CreateOrderData;
use App\Http\Requests\Api\CreateOrderRequest;
use App\Http\Resources\OrderResource;

final class CollectionOrderController extends Controller
{
    public function __construct(private readonly CreateCollectionOrder $action) {}

    public function store(CreateOrderRequest $request): OrderResource
    {
        $order = $this->action->handle(
            CreateOrderData::fromRequest($request, $request->user()->merchant_id)
        );

        return OrderResource::make($order)->response()->setStatusCode(201);
    }
}
```

用例层承担规则与事务：

```php
<?php

namespace App\Actions\Payment;

use App\Data\Payment\CreateOrderData;
use App\Enums\OrderStatus;
use App\Exceptions\BusinessException;
use App\Models\Order;
use App\Repositories\Contracts\OrderRepository;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;

final class CreateCollectionOrder
{
    public function __construct(
        private readonly OrderRepository $orders,
        private readonly ResolveChannel $resolveChannel,
        private readonly RecordOrderFlow $recordFlow,
    ) {}

    public function handle(CreateOrderData $data): Order
    {
        try {
            return DB::transaction(function () use ($data) {
                $channel = $this->resolveChannel->handle($data->amount);

                $order = $this->orders->create([
                    'merchant_id'   => $data->merchantId,
                    'user_order_no' => $data->userOrderNo,
                    'channel_id'    => $channel->id,
                    'amount'        => $data->amount,
                    'fee'           => $channel->feeFor($data->amount),
                    'status'        => OrderStatus::Pending,
                ]);

                $this->recordFlow->handle($order);

                return $order;
            });
        } catch (UniqueConstraintViolationException) {
            // 唯一索引兜底：并发重复提交时，返回已存在的那一单
            return $this->orders->findByUserOrderNo($data->merchantId, $data->userOrderNo)
                ?? throw new BusinessException('订单号重复');
        }
    }
}
```

注意幂等的处理方式变了：不再“先查再插”，而是**直接插，靠数据库唯一索引裁决**，捕获冲突后回查。这是并发下唯一可靠的做法——“先查”和“再插”之间永远有窗口。

通道选择被抽成独立的可测单元：

```php
<?php

namespace App\Actions\Payment;

use App\Exceptions\BusinessException;
use App\Models\Channel;

final class ResolveChannel
{
    public function handle(int $amount): Channel
    {
        return Channel::query()
            ->where('status', 1)
            ->where('min_amount', '<=', $amount)
            ->where('max_amount', '>=', $amount)
            ->orderByDesc('weight')
            ->first()
            ?? throw new BusinessException('无可用通道');
    }
}
```

现在 `ResolveChannel` 可以对着内存 SQLite 写三条测试：金额落在区间内、落在区间外、多个候选时按权重取。这在拆分前是不可能的。

---

## 四、判断表：这行代码该放哪

落地时最常卡住的不是“要不要分层”，而是具体某一行该去哪。做一张表贴在 Code Review 模板里，争议会少 80%：

| 你可能想写的东西 | 该放 | 理由 |
|---|---|---|
| `$request->validate()` | FormRequest | 传输层关切 |
| `return response()->json()` | Controller / Resource | 序列化是传输层 |
| `auth()->user()` | Controller → 传 DTO 下去 | 用例层不该依赖会话 |
| `if ($order->status === ...)` 状态流转 | Model 或 Enum 方法 | 领域规则 |
| `DB::transaction()` | Action | 事务边界＝业务语义 |
| 复杂 `where` 组合 | Repository | 集中优化点 |
| 调用第三方接口 | 独立 Client 类 + Action 编排 | 可 mock |
| 发通知、写审计 | Event / Listener | 副作用与主流程解耦 |
| 金额四则运算 | `Support\Money`（整数分） | 统一精度 |

最后一行值得单独强调：金额一律用整数“分”存储和运算，只在展示层除以 100。把这条写进团队规范，能消灭一整类对不上账的工单。

---

## 五、用工具把边界钉死

制度靠文档会退化，靠工具才稳定。三个可照抄的动作：

**第一步，装静态分析。**

```bash
composer require --dev "larastan/larastan:^3.0"
```

`phpstan.neon`：

```neon
includes:
    - vendor/larastan/larastan/extension.neon

parameters:
    paths:
        - app
    level: 6
    treatPhpDocTypesAsCertain: false
```

**第二步，把分层写成会自动失败的测试。** 下面这段不需要任何扩展包，`php artisan test` 就会跑：

```php
<?php

namespace Tests\Arch;

use PHPUnit\Framework\TestCase;
use RecursiveDirectoryIterator;
use RecursiveIteratorIterator;
use SplFileInfo;

final class LayerBoundaryTest extends TestCase
{
    private const FORBIDDEN_IN_ACTIONS = [
        'Illuminate\Http\Request',
        'Illuminate\Support\Facades\Auth',
        'Illuminate\Support\Facades\Response',
    ];

    public function test_actions_stay_out_of_http_layer(): void
    {
        $violations = [];

        foreach ($this->phpFiles(app_path('Actions')) as $file) {
            $code = file_get_contents($file);

            foreach (self::FORBIDDEN_IN_ACTIONS as $needle) {
                if (str_contains($code, $needle)) {
                    $violations[] = $this->relative($file).' 引用了 '.$needle;
                }
            }
        }

        $this->assertSame([], $violations, "用例层出现 HTTP 依赖：\n".implode("\n", $violations));
    }

    /** @return list<string> */
    private function phpFiles(string $dir): array
    {
        if (! is_dir($dir)) {
            return [];
        }

        $files = [];
        $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($dir));

        /** @var SplFileInfo $file */
        foreach ($iterator as $file) {
            if ($file->isFile() && $file->getExtension() === 'php') {
                $files[] = $file->getPathname();
            }
        }

        return $files;
    }

    private function relative(string $path): string
    {
        return str_replace(base_path().DIRECTORY_SEPARATOR, '', $path);
    }
}
```

**第三步，把两道门禁串进 `composer.json`：**

```json
"scripts": {
    "check": [
        "@php artisan test --testsuite=Arch",
        "vendor/bin/phpstan analyse --no-progress --memory-limit=1G"
    ]
}
```

之后 `composer check` 就是一次成本极低的分层回归。新人第一次提交如果让 `Actions` 里出现 `Request`，会在 CI 里直接红掉，而不是在三周后的 Code Review 里被口头提醒。

---

## 六、三种常见反模式

**反模式一：为分层而分层。** 一个只有五张表的内部工具，硬套 Action + Repository + DTO，会让“改一个字段”要动四个文件。判断标准是团队规模与变更频率：单人维护、需求两周一次的项目，Controller 里写 60 行是合理的；多人在同一模块并行提交，才需要严格分层。

**反模式二：Service 变上帝类。** 把 `OrderService` 做成一个 1200 行、二十个方法的类，本质只是把臃肿从 Controller 搬了个家。一个业务动作一个类，类是廉价的，改一个类时的心智负担才是贵的。

**反模式三：Repository 包成一比一转发。** `public function find($id) { return Order::find($id); }` 这种方法是纯噪音——它没有抽象任何东西，只是多了一层跳转。Repository 值得存在的信号是：方法里有多表 join、有复杂条件组合、或者需要被 mock 掉以便测试。

---

## 小结

- 分层的判据不是代码形态，而是**变更原因**：会因为不同理由改动的代码，不该待在同一个文件里。
- Controller 只做翻译（≤20 行）、FormRequest 管校验、Action 管用例与事务、Repository 管查询、DTO 管跨层契约；金额一律用整数分。
- 边界靠 `PHPStan level 6` + 一个架构测试 + `composer check` 钉死，而不是靠文档和自觉。
- 小项目不要过早上 Repository；等出现了“复杂查询”和“需要 mock”这两个信号再加。

> 下一篇回到 AI 数学直觉系列：《强化学习：马尔可夫决策与贝尔曼方程》，从“状态值函数为什么能递归地定义自己”讲起。
