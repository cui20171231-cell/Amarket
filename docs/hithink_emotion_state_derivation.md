# 市场情绪梯队状态表派生逻辑

表：`market.hithink_emotion_state`

## 1. 节点身份与时间语义

每个实际执行或已有派生结果的盘中节点，按自己的
`trade_date + collection_id + scheduled_time` 生成一行。

`node_seq` 是数据库从 `collection_id` 后三位自动生成的 `UInt16`，只用于节点身份、排序、
缺失检查和范围查询。分钟差、5分钟、30分钟、60分钟比较必须使用 `scheduled_time` 查找目标时间
附近最近有效节点，禁止用 `node_seq` 或 `collection_id` 后缀做时间减法。

## 2. 三池可用状态与共同来源

`pool_data_status` 有四种业务状态：

| 状态 | 含义 |
|---|---|
| `CURRENT` | 当前节点应采且三张池全部成功，使用当前节点 |
| `FALLBACK` | 当前节点应采但三池未完整成功，整体使用此前最近共同成功节点 |
| `NOT_APPLICABLE` | 当前节点按数据源规则不采三池，不回退，不产生当前池指标 |
| `NO_SOURCE` | 当前节点应采但失败，且此前没有三池共同成功来源 |

天然不可采时间为09:15:00～09:24:59和14:57:00～14:59:59。这些节点固定为
`NOT_APPLICABLE`，来源、年龄和所有依赖三池的指标均为 `NULL`，不能把空返回解释成真实0。

只在应采阶段，涨停池、跌停池、炸板池共同选择一个来源节点：

1. 当前节点三张池状态全部为 `SUCCESS`，三张池都使用当前节点。
2. 当前节点任意一张池不是 `SUCCESS`，三张池整体向前回退。
3. 按计划时间向前查找最近一个三张池全部为 `SUCCESS` 的节点。
4. 如果历史上没有三池同时成功的节点，状态为 `NO_SOURCE`，来源和依赖指标全部写 `NULL`。

`pool_source_collection_id` 和 `pool_source_scheduled_time` 记录共同来源；
`pool_source_age_seconds` 是共同来源距离当前节点的秒数；
`pool_is_fallback` 表示是否发生整体回退。

不设置最大允许回退秒数。数据库忠实保存 `pool_source_age_seconds`，由查询层和上层分析决定
是否接受这份滞后数据。返回 `FALLBACK` 数据时必须同时返回来源时间和来源年龄。

为兼容已有读取，原来的三组 `limit_*_source_*` 字段继续保留，但三组值现在必须完全相同。
真实空池仍有成功状态，因此计数为 0；没有共同成功来源时才写空值。

### 254号收盘最终节点

`node_seq = 254`、`scheduled_time = 15:00:08`（自2026-09-04起）是每天唯一的正式收盘定论节点，执行规则与普通节点不同：

1. 254号禁止整体回退，不能使用14:56、14:59或上一交易日的数据。
2. 三池首次未全部成功时，情绪任务保持阻塞；采集器每60秒继续重采缺失的池。
3. 只有三池全部成功并保存到254号自身编号后，才生成 `CURRENT` 情绪状态。
4. 最终行必须同时满足：共同来源等于自身编号、来源时间等于15:00计划时间、来源年龄为0、回退标记为0。
5. 数据库表约束 `ck_emotion_closing_current` 会拒绝任何不满足上述条件的254号情绪行。

如果三池持续无法成功，254号保留明确的采集失败和情绪阻塞状态，不生成 `FALLBACK` 收盘情绪。

## 3. 字段来源

| 字段 | 来源与口径 |
|---|---|
| `trade_date`、`collection_id`、`scheduled_time`、`session` | 当前计划节点 |
| `node_seq` | 数据库从 `collection_id` 后三位自动生成，不由业务程序写入 |
| `pool_data_status` | 当前、回退、天然不可采或无来源四态 |
| `pool_source_collection_id` | 三张池实际共同采用的节点编号 |
| `pool_source_scheduled_time` | 三张池实际共同采用的计划时间 |
| `pool_source_age_seconds` | 当前计划时间减共同来源计划时间，单位秒 |
| `pool_is_fallback` | 共同来源不是当前节点时为 1 |
| `limit_*_source_*` | 兼容字段，全部复制共同来源及共同回退标记 |
| `limit_up_count` | 共同来源节点涨停池股票数 |
| `limit_down_count` | 共同来源节点跌停池股票数 |
| `limit_break_count` | 共同来源节点炸板池股票数 |
| `limit_attempt_count` | 同一共同来源节点的涨停数 + 炸板数 |
| `limit_success_rate` | 同一共同来源节点的涨停数 / 涨停尝试数；分母为 0 时为空 |
| `limit_break_rate` | 同一共同来源节点的炸板数 / 涨停尝试数；分母为 0 时为空 |
| `first_board_count` 至 `fifth_plus_board_count` | 共同来源涨停池的 `continue_day_cnt` 分层 |
| `max_board_height` | 共同来源涨停池最高连板高度 |
| `promotion_base_count` | 上一交易日254号 `CURRENT` 正式收盘涨停池股票数；正式收盘基准缺失时为空 |
| `promotion_success_count` | 按股票代码匹配，当前高度至少达到上一日高度 + 1 的股票数 |
| `promotion_fail_count` | 晋级基数 - 晋级成功数 |
| `promotion_rate` | 晋级成功数 / 晋级基数；基数为 0 或无上一日来源时为空 |
| `high_board_count` | 共同来源涨停池中连板高度大于等于 3 的股票数 |
| `high_board_break_count` | 上一交易日高度大于等于 3 的股票中，出现在共同来源炸板池的股票数 |
| `high_board_fail_count` | 上一交易日高度大于等于 3、当前未成功晋级的股票数 |
| `calculated_at`、`version_time` | 实际计算时间、重复执行版本时间 |

`continue_day_cnt` 为空时，才从 `continue_day_text` 提取“首板”或数字。

当 `pool_data_status` 为 `NOT_APPLICABLE` 或 `NO_SOURCE` 时，表内所有依赖三池的指标均为
`NULL`，包括晋级和高位风险字段。正常应采节点三池成功但真实返回空池时，状态为 `CURRENT`，
对应计数为0。

## 4. 晋级与高位炸板

晋级和高位炸板都按股票代码逐只匹配上一交易日状态。

上一交易日基准只允许读取 `node_seq = 254` 且 `pool_data_status = CURRENT` 的正式收盘行，
并同时核对计划表中三池均为成功。上一交易日254号缺失或失败时，晋级和昨日高位基准相关字段为
`NULL`，绝不静默使用14:56等盘中节点代替。

晋级成功条件：

```text
当前共同来源涨停池中存在该股票
并且当前连板高度 >= 上一交易日连板高度 + 1
```

高位炸板条件：

```text
上一交易日连板高度 >= 3
并且该股票出现在当前共同来源炸板池
```

高位炸板不要求该股票今天此前进入过涨停池。因此，昨天 3 板、今天第一次冲 4 板即炸板，
会直接计入 `high_board_break_count`。

## 5. 查询返回要求

查询一条情绪状态至少同时返回：

```text
scheduled_time
node_seq
pool_data_status
pool_source_scheduled_time
pool_source_age_seconds
pool_is_fallback
```

然后再返回情绪指标，保证调用方能判断当前值、回退值、天然不可采和无来源。

## 6. 链路、状态与幂等

```text
行情及三张池分别完成或失败
        → 持久化各自任务状态
        → 判断三池是否天然不可采
        → 应采节点查找最近三池同时成功节点
        → hithink_emotion_state
        → 其他个股、市场和板块派生继续执行
```

情绪任务通过计划表中的 `emotion_state_status`、行数、耗时和错误字段单独记录。
情绪派生失败不会删除原始池，也不会阻止其他派生结果保存。

表使用 `ReplacingMergeTree(version_time)`，排序键为
`trade_date + collection_id`。同一节点重复执行会产生新版本，使用 `FINAL` 读取时只有一行。
