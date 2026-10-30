---
layout:     post
title:      PostgreSQL 月度 RANGE 分区：踩过的坑
subtitle:   一张八千万行的订单表改造实录，从唯一约束报错到 DETACH 归档
date:       2026-10-28
author:     WY
header-img: img/post-bg-unix-linux.jpg
catalog: true
series:     engineering
chapter:    3
description: 月度 RANGE 分区的完整落地记录：唯一约束必须含分区键的连锁反应、DEFAULT 分区的锁陷阱、whereDate 让裁剪失效、DETACH 归档与不停机迁移步骤。
last_modified_at: 2026-10-29
errata:
  - date: 2026-10-29
    text: 解法一的单号示例原先对雪花 ID 取模截成 6 位，会必然碰撞，已改为直接使用完整 ID；并补充说明单号生成器解决不了商户单号的幂等，幂等要靠解法二的登记表
  - date: 2026-10-29
    text: 原文最终方案里的 `UNIQUE (merchant_id, user_order_no, created_day)` 不含分区键，会触发第二节的同一条报错，已改写解法三与最终方案
  - date: 2026-10-29
    text: 迁移第 2 步原先调用的函数只会建当前月起的 4 个分区，回填历史数据会报找不到分区；函数已增加起始月份参数
  - date: 2026-10-29
    text: 换名一步补上子分区改名，否则之后的定时任务会重复创建分区并报区间重叠
  - date: 2026-10-29
    text: 新表 `id` 为应用侧生成的 `bigint`，不存在序列，原文的 `setval` 会报错，已改为按是否使用 `bigserial` 分别说明
  - date: 2026-10-29
    text: 执行期裁剪在 `EXPLAIN (ANALYZE)` 中显示为 `Subplans Removed`，原文写的 `Partitions scanned` 不存在；父表统计信息一节改为「autovacuum 不分析分区父表」
  - date: 2026-10-29
    text: 表名、类名与数据规模统一脱敏，文中代码均为按原理重写的示意代码
tags:
    - PostgreSQL
    - 性能优化
    - 支付系统
---

## 开篇：一张表长到八千万行之后

去年冬天，一张订单表长到了八千万行。

月增三百万行，索引加起来四十多 GB，单个 `orders` 占了整个实例七成空间。真正让人难受的不是查询慢——事实上线上查询一直是好的，因为九成的请求都带 `WHERE id = ?` 或者走 `(merchant_id, user_order_no)` 的唯一索引。难受的是三件事：

第一，归档一次要跑六个小时。`DELETE FROM ... WHERE created_at < '2023-01-01'`，两千万行，WAL 打满磁盘两次，跑完还要等 autovacuum 慢慢把空间吐出来，而这期间磁盘水位一直悬在 90%。

第二，`VACUUM FULL` 或者 `REINDEX` 想都别想。四十 GB 的表，锁表窗口以小时计，业务不可能停。

第三，备份和恢复越来越像赌博。一次误删恢复演练，从快照拉起来花了四十分钟——只为了捞回某一天的几百条数据。

上分区的决定就是在这三件事上做出来的。但我要先把结论摆在这儿：**分区解决的是删除与归档，不是查询变快。** 如果你的痛点是“某个查询慢”，分区大概率不是答案，索引和 SQL 才是。分区是拿“能不能低成本地扔掉一整块历史数据”来换“表结构复杂度上升”。

这篇把这次改造里真正卡住人的地方按顺序写下来，每条都给出可抄的 SQL。表名、字段与数据规模都做过脱敏，文中代码是按原理重写的示意代码，不是生产源码。

---

## 一、先算清楚分区买到了什么

很多人对分区的第一印象是“性能优化”，这个印象会把你带进坑里。分区表本质上是把一张大表拆成 N 张物理表，加一层路由。路由本身有成本，查询不会因为分区而自动变快。

| 场景 | 分区后 | 说明 |
|---|---|---|
| 删除整月历史 | 毫秒级 | `DETACH` 是元数据操作，不产生 WAL 行 |
| 单条主键查询 | 基本持平，可能略慢 | 多了一层分区路由与计划开销 |
| 带时间范围的查询 | 明显变快 | 分区裁剪只扫 1 个分区，索引也小了一个量级 |
| 跨月聚合（按月统计） | 基本持平 | 还是要把所有分区扫一遍 |
| 单个分区 `REINDEX` / `VACUUM` | 快很多 | 可以逐分区在线做，锁的范围小 |
| 写入吞吐 | 无变化 | 分区不解决写放大 |

判断要不要分区，我后来只问一个问题：**这张表有没有“按时间整块丢弃”的需求？**

有，就分。没有，别分。纯粹为了让查询快而上分区，最后往往发现计划时间变长了、索引数量翻了几倍、唯一约束还全部失效——净亏。

---

## 二、第一个坑：唯一约束必须包含分区键

建表语句写完，第一条就报错。

```sql
CREATE TABLE orders (
    id            bigserial   NOT NULL,
    merchant_id   bigint      NOT NULL,
    user_order_no varchar(64) NOT NULL,
    amount        bigint      NOT NULL,
    status        smallint    NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (merchant_id, user_order_no)
) PARTITION BY RANGE (created_at);
```

```
ERROR:  unique constraint on partitioned table must include all partitioning columns
DETAIL:  UNIQUE constraint on table "orders" lacks column "created_at" which is part of the partition key.
```

原因很直白：唯一索引在 PostgreSQL 里是“每个分区各建一份本地索引”。分区 A 里没有重复、分区 B 里没有重复，不代表跨分区没有重复。数据库没有全局索引这个东西，所以它干脆拒绝你建这种约束。

这条限制的连锁反应比想象的大：

**主键从单列变成复合列。** `PRIMARY KEY (id, created_at)`。这意味着任何指向这张表的外键，都得引用两列。PG 12 起虽然允许外键指向分区表，但被引用的列必须有唯一约束，而唯一约束又必须含分区键——于是外键也得带上 `created_at`。实践中基本没法用，只能靠应用层保证引用完整性。

**`id` 不再全局唯一。** 复合主键 `(id, created_at)` 只能保证“同一分区内 `id` 不重复”。两个分区里出现同一个 `id` 是完全合法的。所以我最后把 `id` 换成了应用侧生成的雪花 ID，数据库的唯一约束只当形式上的兜底。

**最隐蔽的一层：`UNIQUE (merchant_id, user_order_no, created_at)` 不等于你以为的那个唯一。**

很多人被报错逼急了，就把分区键塞进唯一约束交差：

```sql
-- 看起来解决了，实际上是错的
UNIQUE (merchant_id, user_order_no, created_at)
```

`created_at` 是 `timestamptz`，精度到微秒。同一个商户号、同一个订单号，只要时间戳差一微秒，就是两条不同的记录——约束照样放行。你等于把“业务幂等”换成了“字面去重”，而且换得毫无声息。

### 真正能用的三种解法

先把两件经常被混为一谈的事拆开：**主键 `id` 要全局唯一**，和**商户传来的单号要幂等**。前者是我们自己生成的号码，后者是外部输入，它们的解法不一样。

**解法一：主键由应用层生成，解决 `id` 的全局唯一。**

```php
// 雪花 ID 或 ULID 都可以：全局唯一、趋势递增，原样入库，不要截断或取模
$id = $snowflake->nextId();
```

这里有一个很容易犯的错：为了让单号“好看”，把 ID 取模截成固定位数，或者拼上商户号后只留后几位。截断之后的号码空间只剩百万量级，碰撞是必然的，全局唯一也就不复存在。

注意它**解决不了幂等**：商户重复提交同一个 `user_order_no`，我们会给它生成两个不同的 `id`，两条记录都能写进去。幂等要靠下面的解法二。

**解法二（推荐）：单独开一张不分区的登记表，负责商户单号的幂等。**

```sql
CREATE TABLE order_no_registry (
    merchant_id   bigint      NOT NULL,
    user_order_no varchar(64) NOT NULL,
    order_id      bigint      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, user_order_no)
);
```

写入时先 `INSERT ... ON CONFLICT DO NOTHING` 到登记表，`rowCount()` 为 0 说明重复，直接返回幂等结果；为 1 才往分区表写。**两次写入必须放在同一个事务里**，否则订单写失败时登记表里会留下一条孤儿记录，之后同一单号永远被当成重复。代价是多一次写入，以及按幂等窗口（比如 90 天）定期清理登记表；好处是唯一性语义完全正确，而且这张表只有几列窄字段，索引很容易常驻内存。

**解法三：把分区键本身换成业务日期，让“按日唯一”变得合法。**

一个常见的直觉是加一列 `created_day`，然后建 `UNIQUE (merchant_id, user_order_no, created_day)`。但只要表还是按 `created_at` 分区，这条约束一样会触发上面那条报错，因为它不含分区键。要让它合法，分区键就得是 `created_day`：

```sql
CREATE TABLE orders (
    id            bigint      NOT NULL,
    merchant_id   bigint      NOT NULL,
    user_order_no varchar(64) NOT NULL,
    created_day   date        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_day),
    UNIQUE (merchant_id, user_order_no, created_day)
) PARTITION BY RANGE (created_day);
```

`created_day` 由应用层按业务时区算好再写入，不要依赖数据库会话时区（原因见下一节的时区坑）。这个方案有两个代价：所有查询的时间条件都要改成对 `created_day` 过滤才能裁剪；而且它假设“同一个单号不会跨日复用”。绝大多数支付业务成立，但它是个假设，不是定理——如果上游会在跨日时重放昨天的单号，这条约束就漏了。用之前先跟业务确认。

我最后选的是解法一 + 解法二：主键用应用侧生成的 ID，商户单号的幂等交给登记表。分区表上**不再建任何和 `user_order_no` 相关的唯一约束**——按 `created_at` 分区时它要么不合法，要么只能做字面去重，留着反而让人误以为有保护。

---

## 三、第二个坑：别用 DEFAULT 分区偷懒

建完父表，第一反应是“先挂一个 DEFAULT 分区，省得忘了建分区导致写入失败”。这个想法非常自然，也非常危险。

```sql
-- 强烈不建议
CREATE TABLE orders_default PARTITION OF orders DEFAULT;
```

问题出在之后。等你某天想给 11 月建一个正式分区时：

```sql
CREATE TABLE orders_p202611
    PARTITION OF orders
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
```

PostgreSQL 必须确认 DEFAULT 分区里没有属于 11 月的数据，否则那些数据就“无家可归”。这个确认是一次**全表扫描**，而且全程持有父表的 `ACCESS EXCLUSIVE` 锁。DEFAULT 分区越大，锁得越久——本来是个毫秒级的 DDL，硬生生变成一个锁表事故。

正确做法只有一条：**提前把分区建好，用定时任务滚动补齐。** 我自己写了个函数，比装扩展省事：

```sql
CREATE OR REPLACE FUNCTION ensure_monthly_partitions(
    p_parent text,
    p_months int  DEFAULT 3,
    p_start  date DEFAULT NULL   -- 起始月份；不传就从当前月开始
) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    v_base  date := date_trunc('month', coalesce(p_start, now()::date))::date;
    v_from  date;
    v_to    date;
    v_child text;
    v_count int := 0;
BEGIN
    FOR i IN 0..p_months LOOP
        v_from  := (v_base + make_interval(months => i))::date;
        v_to    := (v_from + interval '1 month')::date;
        v_child := p_parent || '_p' || to_char(v_from, 'YYYYMM');

        IF to_regclass(v_child) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                v_child, p_parent, v_from, v_to
            );
            v_count := v_count + 1;
        END IF;
    END LOOP;
    RETURN v_count;
END;
$$;
```

日常调用就是一句话，当前月加未来三个月，幂等，重复跑不会报错（`p_start` 留到后面迁移存量数据时再用）：

```sql
SELECT ensure_monthly_partitions('orders', 3);
```

用 `pg_cron` 每月 1 号凌晨跑一次：

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT cron.schedule(
    'ensure-monthly-partitions',
    '15 3 1 * *',
    $$SELECT ensure_monthly_partitions('orders', 3)$$
);
```

`pg_cron` 需要在 `postgresql.conf` 里加 `shared_preload_libraries = 'pg_cron'` 然后重启，且任务只在其所属数据库内生效。不想装扩展的话，Laravel 侧挂个命令同样可以，我实际用的是后者——因为它能进版本管理，也能在任务失败时走既有告警：

```php
// app/Console/Commands/EnsurePartitions.php
public function handle(): int
{
    $created = DB::selectOne(
        "SELECT ensure_monthly_partitions(?, ?) AS n",
        ['orders', 3]
    )->n;

    $this->info("新建分区 {$created} 个");
    return self::SUCCESS;
}
```

```php
// routes/console.php
Schedule::command('partitions:ensure')->monthlyOn(1, '03:15');
```

### 一个和月份边界有关的坑

函数里的 `now()::date` 会按**当前会话的 TimeZone** 换算。如果数据库是 UTC、业务在东八区，那么 UTC 时间 2026-11-01 00:00 之前的 8 小时里，`now()` 换算出的日期还是 10 月 31 日——分区边界和业务口径差了一整天。

更麻烦的是 DDL 里的分区边界字面量：

```sql
FOR VALUES FROM ('2026-11-01') TO ('2026-12-01')
```

这两个字符串转成 `timestamptz` 时用的也是会话时区。建分区的那一刻会话是什么时区，边界就定成什么时区，之后不会跟着变。所以要么统一在 UTC 下建分区，要么统一在东八区下建，**不能让 DDL 的时区跟着连接池随机漂**。我吃过一次亏：某个脚本用了另一套连接配置，建出来的分区边界偏移了 8 小时，那 8 小时的数据全落进了相邻分区，直到对账时按天聚合出现缺口才被发现。

---

## 四、第三个坑：`whereDate` 会让分区裁剪直接失效

分区建好，最爽的是带时间范围的查询从三秒变成三十毫秒。但这份爽快非常脆弱，一行写法不对就全部还回去。

**能裁剪的写法**——边界是明确的区间：

```sql
SELECT count(*), sum(amount)
FROM orders
WHERE created_at >= '2026-10-01 00:00:00+08'
  AND created_at <  '2026-10-02 00:00:00+08';
```

**不能裁剪的写法**——对分区键做了函数或类型转换：

```sql
-- 全分区扫描
SELECT * FROM orders WHERE created_at::date = '2026-10-15';

-- 全分区扫描
SELECT * FROM orders WHERE to_char(created_at, 'YYYY-MM') = '2026-10';

-- 全分区扫描
SELECT * FROM orders WHERE date_trunc('day', created_at) = '2026-10-15';
```

规则只有一条：**分区键必须裸露在比较运算符的一侧，不能被任何函数包住。** 一旦包住，优化器拿不到常量边界，只能把每个分区都打开看一眼。

这条规则在 Laravel 里最容易被违反，因为 `whereDate` / `whereMonth` / `whereYear` 在 PostgreSQL 上生成的就是带 `::date` 的 SQL：

```php
// 生成的 SQL: where "created_at"::date = '2026-10-15'  → 全分区扫描
Order::whereDate('created_at', '2026-10-15')->get();

// 正确写法：显式给出左闭右开区间
$day = Carbon::parse('2026-10-15', 'Asia/Shanghai');
Order::where('created_at', '>=', $day->copy()->startOfDay())
     ->where('created_at', '<',  $day->copy()->addDay()->startOfDay())
     ->get();
```

同样的坑还有 `whereBetween('created_at', [$a, $b])` 传进去的是字符串日期——这个反而是安全的，因为 Laravel 会原样传给数据库，不套函数。**判断标准不是用哪个方法，而是最终 SQL 长什么样。** 上线前把 `toSql()` 打出来看一眼，比读十篇文档都管用。

还有一个介于两者之间的情形：

```sql
WHERE created_at >= now() - interval '7 days'
```

这个**能**裁剪，因为 `now()` 是 stable 函数，优化器会在执行器启动时求值一次，然后做“初始裁剪”。但正因为裁剪发生在执行期而不是计划期，光看 `EXPLAIN` 容易误判——一定要用 `EXPLAIN (ANALYZE)`：执行期被裁掉的分区会在 Append 节点下显示为一行 `Subplans Removed: N`，剩下挂着的子计划才是真正扫描的分区。

所以我在 Service 层加了一道强制：**所有列表查询必须带时间范围，不带就默认近 7 天并记一条 warning 日志。** 没有这道闸，某个新同事随手写一个全表 count，第二天早上数据库就挂了。

---

## 五、索引：每个分区一套，加索引要绕路

在父表上建索引，PostgreSQL 会自动在每一个分区上建一份对应的本地索引，之后新建的分区也会自动继承：

```sql
CREATE INDEX idx_orders_merchant_created
    ON orders (merchant_id, created_at DESC);
```

查看某一分区的实际索引：

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'orders_p202610';
```

**坑在于加索引的锁。** 父表上的 `CREATE INDEX` 会递归到所有分区，并且在整个过程中持锁。线上给一张八千万行的分区表加索引，这条路走不通。

官方给的办法是分三步：先在父表上建一个“空壳”索引（标记为 invalid），再逐个分区用 `CONCURRENTLY` 建，最后把子索引挂到父索引上。

```sql
-- 1. 父表上只建定义，不建数据，会标记为 invalid
CREATE INDEX idx_orders_status ON ONLY orders (status);

-- 2. 每个分区单独在线建，不锁写
CREATE INDEX CONCURRENTLY idx_orders_status_p202610
    ON orders_p202610 (status);
CREATE INDEX CONCURRENTLY idx_orders_status_p202609
    ON orders_p202609 (status);

-- 3. 逐个挂上去，每挂一次只短暂持有该分区的锁
ALTER INDEX idx_orders_status
    ATTACH PARTITION idx_orders_status_p202610;
ALTER INDEX idx_orders_status
    ATTACH PARTITION idx_orders_status_p202609;
```

全部挂完后父索引才变为 valid。中间状态不影响查询，只是新索引还没被用上。

顺带说一个分区带来的额外好处：`REINDEX` 可以逐个分区做。四十 GB 的表整体重建要几小时，拆成三十个分区，每次重建一个千万行的分区，几分钟完事，而且业务无感。这是我当时没预料到的收益。

---

## 六、归档：`DETACH` 才是分区真正的回报

回到开篇那六个小时的 `DELETE`。分区之后，同一件事变成两行：

```sql
-- 摘下来，毫秒级，不产生任何行级 WAL
ALTER TABLE orders
    DETACH PARTITION orders_p202301 CONCURRENTLY;

-- 移进归档 schema，仍然在同一个实例里，随时可查
ALTER TABLE orders_p202301 SET SCHEMA archive;
```

几点必须注意：

**`DETACH ... CONCURRENTLY` 是 PG 14 才有的**，不允许在事务块里执行，而且**父表上存在 DEFAULT 分区时根本不能用**——这是第三节反对挂 DEFAULT 分区的又一个理由。老版本只能普通 `DETACH`，会短暂持有父表的 `ACCESS EXCLUSIVE` 锁——如果你的父表上有长事务，这个锁可能等很久，进而把后面所有查询都堵住。

**`CONCURRENTLY` 不是完全无锁**，它要在最后阶段短暂拿一次排他锁做元数据切换。所以还是应该在低峰期跑。

**摘下来的表保留原名和原主键 `(id, created_at)`**，可以直接当普通表查。想彻底删除时再 `DROP TABLE`，秒级完成。我的做法是先进 `archive` schema 冷放三个月，过了对账追溯期再 drop 掉——这比任何“软删除”方案都干净。

对比一下两种归档的成本：

| 方式 | 2000 万行耗时 | WAL 量 | 空间回收 | 可回滚 |
|---|---|---|---|---|
| `DELETE` + `VACUUM` | 约 6 小时 | 数十 GB | 需等 autovacuum，可能永不回收 | 需要备份还原 |
| `DETACH PARTITION` | 毫秒级 | 几乎为零 | 立即（文件已独立） | 一条 `ATTACH` 命令 |

第二行的“可回滚”是决定性的。`DETACH` 错了，`ATTACH` 回去就行，数据一直在；`DELETE` 错了，只能上备份。

---

## 七、存量表改造：不能原地转，只能换表

PostgreSQL 到目前为止**不支持把普通表原地改成分区表**。没有 `ALTER TABLE ... PARTITION BY`。所以只能新建一张分区表，把数据搬过去，再换名字。

迁移的完整动作，按顺序：

```sql
-- 1. 建新父表（注意主键要含分区键）
CREATE TABLE orders_new (
    id            bigint      NOT NULL,
    merchant_id   bigint      NOT NULL,
    user_order_no varchar(64) NOT NULL,
    amount        bigint      NOT NULL,
    status        smallint    NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- 2. 按月建好所有历史分区：从 2023-01 起往后 47 个月，共 48 个，覆盖到 2026-12
SELECT ensure_monthly_partitions('orders_new', 47, '2023-01-01');

-- 3. 按分区逐月回填，每批一个事务，避免一个巨大事务把 WAL 打爆
BEGIN;
INSERT INTO orders_new
SELECT * FROM orders
WHERE created_at >= '2026-09-01+08' AND created_at < '2026-10-01+08';
COMMIT;

-- 4. 逐月核对行数与金额合计
SELECT count(*), sum(amount) FROM orders
 WHERE created_at >= '2026-09-01+08' AND created_at < '2026-10-01+08';
SELECT count(*), sum(amount) FROM orders_new
 WHERE created_at >= '2026-09-01+08' AND created_at < '2026-10-01+08';
```

**为什么按分区逐月回填，而不是一条 `INSERT ... SELECT` 全量？** 因为出问题时你能立刻定位是哪个月的数据不对。全量回填跑到第 40 分钟报错，你得从头再来；按月回填，前面 11 个月的结果是有效的。

**校验只对 `count(*)` 是不够的。** 我第一次就只对了行数，结果发现有一批订单的 `amount` 在旧表里是分、在新表里被某个中间脚本按元处理了，行数完全一致。加上 `sum(amount)` 才能抓到这类问题。

**序列要不要管，取决于 `id` 从哪来。** 上面的新表按解法一由应用侧生成 `id`，列类型是普通 `bigint`，没有序列，这一步可以跳过。如果你的 `id` 仍然靠数据库自增，就把新表的 `id` 定义成 `bigserial`（会生成名为 `orders_new_id_seq` 的序列），并在切换前把它推到旧表最大值之后，否则切换后第一批插入就会主键冲突：

```sql
-- 仅当 orders_new.id 定义为 bigserial 时执行
SELECT setval('orders_new_id_seq', (SELECT max(id) FROM orders));
```

**最后换名，放在一个事务里，几十毫秒：**

```sql
BEGIN;
ALTER TABLE orders RENAME TO orders_old;
ALTER TABLE orders_new RENAME TO orders;

-- 子分区也要跟着改名：之后的定时任务按 orders_pYYYYMM 判断分区是否存在，
-- 名字不改，它会认为分区缺失并重复创建，然后报区间重叠
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = 'orders'::regclass
          AND c.relname LIKE 'orders\_new\_p%'
    LOOP
        EXECUTE format('ALTER TABLE %I RENAME TO %I',
                       r.relname, replace(r.relname, 'orders_new_', 'orders_'));
    END LOOP;
END $$;
COMMIT;
```

这个窗口内写入会失败，所以要停写或者让应用重试。想做到真正零停机，就得走“双写 + 逻辑复制追平 + 灰度切读”那一套，复杂度直接翻三倍。我当时选了一个十分钟的维护窗口——八千万行的表，回填加校验跑了四十分钟，真正锁写的只有最后那一下。**先把“十分钟窗口”这个成本摆到台面上，再决定要不要为省它多花两周。**

---

## 八、分区数量：不是越多越好

一个月一个分区，三年就是三十六个。听着不多，但每个分区上有四五个索引，规划器要考虑的对象就上百个。

PG 11、12 两个版本大幅改进了分区裁剪和规划性能，几百个分区也能扛，但**计划时间仍然随分区数增长**。简单查询的计划时间从 0.1 毫秒涨到 1 毫秒，单看不多，QPS 上千的时候就是实打实的 CPU。

我的经验区间：

- 单个分区 **500 万到 2000 万行**是最舒服的。低于 100 万行说明切得太碎，路由开销压过了收益。
- 在线分区总数**控制在 100 个以内**。超过就改成按周切，或者把更早的整年合并成一个大分区。
- 超过保留期的分区**一定摘走**，不要留着凑数。

查一下各分区大小，一眼就能看出分布是否健康：

```sql
SELECT c.relname,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       c.reltuples::bigint                            AS est_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname LIKE 'orders_p%'
ORDER BY c.relname DESC
LIMIT 12;
```

`reltuples` 是估算值，依赖 `ANALYZE`。分区表有个容易忽略的点：**autovacuum 只会分析各个分区，从不分析分区表的父表**。父表级别的统计信息（跨分区的整体分布）只能手动收集，不收集时规划器在需要整体估算的查询上容易估偏。建议把它和建分区的定时任务放在一起，并且在发现某个查询的计划突然变差时先跑一遍：

```sql
ANALYZE orders;
```

---

## 九、一张决策表

改造做完，我把要不要分区的判断压成一张表。新表设计时先过一遍：

| 特征 | 分区 | 不分区 |
|---|---|---|
| 有按时间整块归档/删除的需求 | 是，首选 | — |
| 月增百万行以上，且只查近期数据 | 是 | — |
| 查询几乎全是 `WHERE id = ?` | — | 是 |
| 需要跨全表的唯一约束（如全局订单号） | 谨慎，得配登记表 | 是 |
| 表里有大量外键指向它 | 谨慎，外键会变得难用 | 是 |
| 数据量不到 500 万行 | — | 是，索引足够 |
| 有强事务跨月写入（一个事务写多个月份） | 谨慎 | 是 |

最后一行值得单独说。分区表的跨分区事务仍然有 ACID 保证，但两阶段提交、锁的顺序、死锁概率都会变化。如果一个业务事务要同时写本月和上月的分区，先想清楚死锁检测的代价。

---

## 小结

- 分区买的是“整块丢弃历史数据”的能力，不是查询速度。没有归档需求就别分。
- 唯一约束必须包含分区键，这条限制会把主键变复合、外键变难用、`id` 变非全局唯一——设计阶段就要认下来。
- 别用 DEFAULT 分区兜底，它会让你之后每次建分区都全表扫。用 `ensure_monthly_partitions` 提前三个月滚动建。
- 分区键不能被函数包住。Laravel 的 `whereDate` / `whereMonth` 会生成 `created_at::date`，直接让裁剪失效，改写成左闭右开区间。
- 加索引用“父表 `ONLY` + 分区 `CONCURRENTLY` + `ATTACH PARTITION`”三步走，别在父表上直接建。
- 归档用 `DETACH PARTITION ... CONCURRENTLY`（PG 14+），毫秒级、可回滚；`DELETE` 六小时且不可逆。
- 存量表不能原地转分区，只能新建 + 逐月回填 + 事务内换名。先把维护窗口的成本摆上桌，再判断值不值得做零停机。

> 下一篇回到 AI 数学直觉系列：《生成对抗网络：一场零和博弈的数学》，从“两个网络互相逼近”讲起，看看那个 minimax 目标到底在优化什么。
