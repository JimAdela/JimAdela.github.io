---
layout:     post
title:      OwlAdmin + amis：低代码后台的真实取舍
subtitle:   两周搭起 19 张表、11 个模块的后台，也在第五个页面撞上低代码的天花板
date:       2026-10-26
author:     WY
header-img: img/post-bg-coffee.jpeg
catalog: true
series:     engineering
chapter:    2
description: 一份踩过坑的低代码后台复盘：四条安装命令、可直接照抄的 CRUD 控制器、金额与状态列的复用封装，以及联动表单、导出、权限、性能这五处真正卡住的地方和绕法。
tags:
    - Laravel
    - PHP
    - 架构设计
    - 性能优化
---

## 开篇：两周、十九张表、没有前端

去年接过一个后台：业务侧给了十九张表，拆成十一个管理模块——渠道、商户、订单、钱包、流水、结算、广告观看记录、语音素材、礼包商品。上线时间两周，团队里没有前端，只有我一个写 Laravel 的。

正常做法是 Vue + Element Plus 起一个 SPA，光登录、路由、权限、菜单、请求封装这套壳子就要三天，然后每个模块的列表页、表单页、详情页再各写一遍。两周交不出来。

所以我选了 OwlAdmin + amis：后端在 PHP 里用链式调用把页面拼成 amis 的 JSON schema，前端什么都不写，amis 负责把它渲染成表格和表单。

这篇不是推荐文。低代码这东西，坑不在于它做不到，而在于你**在第四个页面之前以为自己占了便宜，在第五个页面才发现便宜早已还回去**。下面把这笔账算清楚：哪一段真的省了工时，哪一段反而更贵，以及怎么在选型时就判断出某个页面该不该交给它。

---

## 一、先算清楚低代码省的是哪一段工时

很多人对比低代码是拿“一个列表页”去比的，这没有意义。真实的成本曲线长这样：

| 页面类型 | 手写 Vue + API | OwlAdmin + amis | 结论 |
|---|---|---|---|
| 第一个列表页（含登录、菜单、权限壳） | 约 3 天 | 约 4 天 | 前期是负收益 |
| 第 2 到第 10 个列表页 | 每个约 1.5 天 | 每个约 0.5 天 | 收益全部在这里 |
| 带两三层联动的表单 | 约 2 天 | 约 1 天，外加 1 天调表达式 | 打平 |
| 拖拽排序、画布、实时图表 | 约 3 天 | 不该交给它 | 明显负收益 |
| 复杂导出（跨表聚合、百万行） | 约 1 天 | 内置导出不够用，约 2 天 | 负收益 |

这张表里最重要的一行是第一行。**低代码的收益来自“第 N 个长得差不多的页面”，不来自“第一个页面”。** 如果你的后台只有三五个页面，或者每个页面都长得完全不一样，那这套东西从头到尾都是亏的。

判断标准可以压成一句话：**页面之间的相似度超过 70%，且没有强交互，才值得上低代码。**

十九张表、十一个模块，相似度是够的——它们本质都是“带六到十个筛选条件的表格 + 一个表单 + 一个详情”。所以这笔账在我这里是划得来的：粗算省下二十天左右，代价是后面要花三四天处理它的边界。

---

## 二、装起来：四条命令和一个控制器

安装本身没什么玄机，但顺序错一次要重来：

```bash
# 1. 拉包（OwlAdmin 是 Laravel 扩展包，不是独立项目）
composer require slowlyo/owl-admin

# 2. 先发布资源——config/admin.php 会在这步生成
php artisan admin:publish

# 3. 改 config/admin.php（路由前缀、guard、数据库连接），必须在 install 之前改
#    改完再执行安装
php artisan admin:install

# 4. 自检：数据库、目录权限、前端资源版本一次看全
php artisan admin:doctor
```

顺序的关键在于 `admin:install` 会读 `config/admin.php` 落库菜单和权限，你如果先装后改，改的就只是一份没人读的配置文件。这一点文档里写得不算显眼，我第一次就是装完才发现前缀不对，只能重跑一遍。

`config/admin.php` 里真正需要动的只有三处：

```php
return [
    'route' => [
        'prefix' => 'admin',      // 后台路径前缀，建议改掉默认值
        'domain' => null,
    ],
    'auth' => [
        'guard' => 'admin',       // 独立 guard，别和前台用户共用
        'guards' => [
            'admin' => [
                'driver'   => 'session',
                'provider' => 'admin',
            ],
        ],
    ],
    'database' => [
        'connection' => '',       // 留空用默认连接；多库项目在这里指定
    ],
];
```

装完之后，一个完整的列表页就是一个控制器方法。下面这段是渠道列表，可以直接抄：

```php
<?php

namespace App\Admin\Controllers;

use App\Admin\Services\ChannelService;
use Slowlyo\OwlAdmin\Controllers\AdminController;

final class ChannelController extends AdminController
{
    protected string $serviceName = ChannelService::class;

    public function list()
    {
        $crud = $this->baseCRUD()
            ->filter($this->baseFilter()->body([
                amis()->TextControl('name', '通道名')->clearable(),
                amis()->SelectControl('status', '状态')
                    ->options([
                        ['label' => '启用', 'value' => 1],
                        ['label' => '停用', 'value' => 0],
                    ])
                    ->clearable(),
                amis()->DateRangeControl('created_range', '创建时间')->format('YYYY-MM-DD'),
            ]))
            ->headerToolbar([
                $this->createButton('dialog'),
                ...$this->baseHeaderToolBar(),
            ])
            ->columns([
                amis()->TableColumn('id', 'ID')->sortable(),
                amis()->TableColumn('name', '通道名'),
                amis()->TableColumn('amount_text', '单笔限额'),
                amis()->TableColumn('status', '状态')
                    ->type('mapping')
                    ->map(['1' => '启用', '0' => '停用']),
                amis()->TableColumn('created_at', '创建时间')->type('datetime')->sortable(),
                $this->rowActions('dialog'),
            ]);

        return $this->baseList($crud);
    }

    public function form($isEdit = false)
    {
        return $this->baseForm()->body([
            amis()->TextControl('name', '通道名')->required()->maxLength(50),
            amis()->NumberControl('min_amount', '最小金额（分）')->min(1)->required(),
            amis()->NumberControl('max_amount', '最大金额（分）')->min(1)->required(),
            amis()->SwitchControl('status', '状态')->value(1)->onText('启用')->offText('停用'),
        ]);
    }
}
```

一个列表 + 新增/编辑弹窗 + 详情 + 删除，四十行 PHP，没有一行 JavaScript。这就是它真正值钱的地方。

---

## 三、把重复的三件事封装掉

写到第三个模块你会发现自己在重复写同样的列。这时候该停下来封装，否则十一个模块写完，改一个展示规则要动十一个文件。

**第一件：金额。** 数据库里存的是整数分，前端不应该做除法。做法是在模型上挂一个读取器，让后端返回已经格式化好的字符串，前端只负责显示：

```php
// app/Models/Channel.php
public function getAmountTextAttribute(): string
{
    return sprintf('%.2f ~ %.2f 元', $this->min_amount / 100, $this->max_amount / 100);
}
```

为什么不让前端算？因为 amis 的表达式过滤器在不同小版本里行为有过变化，`${amount / 100}` 这种写法一旦除出 `12.299999`，运营会截图来问你账是不是错了。**能落在后端算的数字，就不要交给模板语言。**

**第二件：状态列。** 用 `type('mapping')` 加 `map()` 比在数据库里存中文状态名要好——状态值是要参与判断的，展示文案是要改的，两者生命周期不同。

**第三件：日期范围筛选。** 这里有一个几乎每个人都会踩的坑：amis 的 `DateRangeControl` 提交上来的是**逗号拼接的字符串**，不是数组。后端要这么接：

```php
public function list(array $filters)
{
    [$start, $end] = array_pad(explode(',', (string) ($filters['created_range'] ?? '')), 2, null);

    return Order::query()
        ->when($start, fn ($q, $v) => $q->where('created_at', '>=', $v.' 00:00:00'))
        ->when($end, fn ($q, $v) => $q->where('created_at', '<=', $v.' 23:59:59'))
        ->paginate((int) ($filters['perPage'] ?? 20));
}
```

**顺带说一个更隐蔽的坑：状态筛选选“停用”（值为 0）时失效。**

```php
// 错误写法：0 在 PHP 里是 falsy，when 直接跳过
->when($filters['status'] ?? null, fn ($q, $v) => $q->where('status', $v))

// 正确写法：用 filled() 判断"有没有传"，而不是"是不是真"
->when(filled($filters['status'] ?? null), fn ($q) => $q->where('status', (int) $filters['status']))
```

这个 bug 不会报错，只会让运营觉得“筛选按钮有时候不灵”。我在渠道统计页上被它坑了半天，最后靠打日志才发现 SQL 里根本没带这个条件。

---

## 四、低代码真正卡住的五个地方

前面都是顺利的部分。下面是那三四天还债的明细。

### 1. 联动表单：表达式能撑到第三层

`visibleOn('${pay_type === "bank"}')` 这种单层显隐很好用，两层条件组合也还行：

```php
amis()->TextControl('bank_account', '银行卡号')
    ->visibleOn('${pay_type === "bank" && amount > 10000}')
```

但到第三层——“选 A 且金额大于阈值时，某个字段的选项要从另一个接口按已填内容重新拉取”——就进入表达式和 `source` 的泥潭了。我的建议是**在第三层之前停手，把它变成一个独立页面**。独立页面可以整页重写，联动的表单改起来会牵一发动全身。

### 2. 自定义交互：写 React 等于放弃“无前端”

amis 有 150 多个组件，覆盖了绝大部分后台场景。但总有一两个需求它没有：比如一个需要拖拽排序的字段顺序编辑器，或者一个实时刷新的监控面板。

amis 支持自定义组件，代价是你要写 React、打包、注册、再跟框架的资源版本对齐。**一旦你开始写自定义组件，“没有前端”这个前提就不成立了**——你只是把前端工作量从“写页面”压缩成“写组件”，而且是在一个你不熟的技术栈里写。这种页面应该单独做成一个静态页或者独立路由，不要硬塞进 amis 体系。

### 3. 导出：内置的只导当前这几列

`$this->exportAction()` 很好用，但它导的是当前 CRUD 的列和当前数据源。真实的导出需求往往不是这样：财务要的是“按渠道按天聚合后的结算表”，列和列表页完全不是一套。

我的做法是导出走独立接口 + 队列：前端只触发，后端生成 CSV 落到临时目录，完成后再给下载链接。

```php
// 生成百万行也不该卡住页面
ExportSettlementJob::dispatch($filters, $adminId);
```

这条属于“低代码不负责”的部分，选型时就要认下来。

### 4. 列表性能：三处一定要收紧

第一版上线后，订单列表打开要四秒。三个原因，都很典型：

- **`perPage` 默认给了 100**，还带全表 `count(*)`。改成 20 到 50 之间，超过一百行人不看。
- **列里用了关联属性**，比如 `merchant.name`。Eloquent 不自动预加载，二十行数据打出二十条查询。在 Service 里加 `->with('merchant:id,name')`。
- **排序字段没索引**。`->sortable()` 打开了前端排序，但数据库上没索引，点一下就是全表扫。

```php
return Order::query()
    ->with(['merchant:id,name', 'channel:id,name'])
    ->when(...)
    ->orderByDesc('id')
    ->paginate(min((int) ($filters['perPage'] ?? 20), 50));
```

`min(..., 50)` 这一个小钳制，能挡掉前端传 `perPage=10000` 把数据库拖死的情况。

### 5. 升级：schema 会漂

amis 是大版本迭代很快的框架，字段名和默认行为会变。OwlAdmin 也一直在跟进。我在一个小版本升级后遇到过一次 `DateRangeControl` 的默认格式变化，结果筛选条件全部失效，而页面没有任何报错。

对策只有一条：**锁版本，读 changelog，升级前在预发环境把十一个模块点一遍。** 这件事本身不花时间，不做的代价很大。

---

## 五、四条纪律，把省下的时间换成可维护性

低代码最怕的不是做不到，是**逻辑慢慢从 Service 里漏进 Controller 的 schema 拼接里**。等你想换掉它的时候，发现业务规则和页面配置已经长在一起了。所以从第一天起就守住四条：

**第一，Controller 只拼 schema，不碰业务。** 判断方法很粗暴：Controller 里出现 `DB::`、出现 `->save()`、出现 `if ($order->status === ...)`，就是越界了。这些应该在 Service 里。

**第二，统一响应结构。** amis 认的是 `{"status":0,"msg":"","data":...}`。OwlAdmin 的 `$this->response()->success($data)` 帮你包好了。要注意的是**非 0 的 status 在前端表现为一条 toast，而不是异常**——如果你的接口自己 `return response()->json(['code'=>1])`，页面上会安静得像什么都没发生，排查起来很痛苦。所有错误都走框架的 response helper。

**第三，前端的隐藏不是权限。** `visibleOn` 只是不给按钮，接口还在那儿。真正的边界在 `admin_permissions` 表的 `http_method` + `http_path`，以及 Service 里对数据归属的校验。我见过最典型的漏洞就是“按钮藏了但 URL 能直接访问”。

**第四，给 schema 加冒烟测试。** 这是投入产出比最高的一件事，一个测试方法把十一个模块全点一遍：

```php
// tests/Feature/AdminPageTest.php
public function test_every_admin_page_renders(): void
{
    $admin = AdminUser::first();
    $this->actingAs($admin, 'admin');

    $pages = ['/admin/channel', '/admin/channel/create', '/admin/merchant', '/admin/order'];

    foreach ($pages as $page) {
        $this->get($page)->assertOk();
    }
}
```

上线前跑一次，十秒之内就能知道有没有哪个页面的 schema 拼错了。低代码的报错经常是“页面白屏”，没有这层保护，你只能靠人工点。

线上再补一条命令行的巡检：

```bash
for p in channel merchant order wallet; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -b /tmp/admin.cookie "https://admin.example.internal/admin/$p")
  printf '%-10s %s\n' "$p" "$code"
done
```

---

## 六、决策表：这个页面该不该交给低代码

做到第六个模块，我总结出一张表。新需求进来先过一遍，能省掉很多“做完才发现做错了”的时间。

| 页面特征 | 交给 amis | 自己写 |
|---|---|---|
| 列表 + 六到十个筛选 + 增删改查 | 是 | — |
| 表单项全是标量（文本、数字、下拉、开关、日期） | 是 | — |
| 需要两三层条件联动、按输入动态拉选项 | 勉强，超过三层就该拆 | 拆成独立页 |
| 需要拖拽、画布、实时图表、地图 | 否 | 是 |
| 导出涉及跨表聚合或百万行 | 否 | 队列 + 独立接口 |
| 面向外部客户、有品牌定制要求 | 否 | 是 |
| 团队里没有前端，且半年内会下线 | 是，别犹豫 | — |

最后一行值得单独说：**低代码特别适合“生命周期有限”的后台**。一个活动运营后台，用三个月就废，用它是最优解；一个要维护五年的核心系统，省下的那二十天迟早要还，而且还得更贵。

---

## 小结

- 低代码的收益来自“第 N 个长得差不多的页面”，第一个页面反而是负收益；相似度不到 70% 就别上。
- `composer require` → `admin:publish` → 改 `config/admin.php` → `admin:install`，顺序不能反；一个完整 CRUD 四十行 PHP 就够。
- 金额在后端算好再给前端，状态用 `mapping`，日期范围是逗号字符串，状态筛选用 `filled()` 而不是 `when()`——这四个坑一个都躲不掉。
- 联动超过三层、需要自定义组件、跨表聚合导出，这三类需求别硬塞，拆出去。
- 守住四条纪律：Controller 不碰业务、错误走框架 response、前端隐藏不是权限、给 schema 加冒烟测试。

> 下一篇回到 AI 数学直觉系列：《策略梯度与 PPO：为什么“奖励”能变成“方向”》，从“采样出来的轨迹怎么变成一个可以求导的目标”讲起。
