# 板块资金迁移状态表派生逻辑

表：`market.hithink_sector_capital_migration`

## 1. 数据含义与来源

本表保存的是板块成交份额迁移、排名变化和内部扩散结构，不表示净资金流入或净资金流出。
唯一业务来源为三张已经按对应交易日成员关系生成的板块状态表：

```text
market.hithink_concept_state
market.hithink_industry_state
market.hithink_style_state
```

三类板块统一写入一张表，`sector_type` 固定为 `concept`、`industry`、`style`。
逻辑唯一键为：

```text
trade_date + collection_id + sector_type + sector_code
```

本表属于B类盘中派生表。

## 2. 固定目标节点和回退

每天使用与 `hithink_market_delta_15m` 完全相同的19个目标节点：

```text
011 09:25:08    012 09:30:08    027 09:45:08
042 10:00:08    057 10:15:08    072 10:30:08
087 10:45:08    102 11:00:08    117 11:15:08
132 11:30:08    133 13:00:08    148 13:15:08
163 13:30:08    178 13:45:08    193 14:00:08
208 14:15:08    223 14:30:08    238 14:45:08
254 15:00:00
```

每个 `sector_type` 独立选择来源节点：

- 目标节点完整存在：`CURRENT`，来源年龄为0。
- 目标节点缺失：同一交易日、同一上午或下午时段整体向前选择最近可用节点，`FALLBACK`。
- 本时段没有任何可用节点：`NO_SOURCE`，当前值和差值为空。
- 不跨交易日，不跨午休。
- 254号必须使用15:00自身来源，禁止回退。

回退按整类板块节点执行。一个来源节点只有在该分类的板块行数达到当天完整板块全集时才算可用，
避免部分写入或同一分类排名混用不同时间截面的板块。

## 3. 比较类型和基准

| `delta_type` | 节点 | 解释 |
|---|---|---|
| `SESSION_BASE` | 09:25、13:00 | 上午和下午基线，基准及全部差值为空 |
| `AUCTION_TO_OPEN` | 09:30 | 09:30状态减09:25集合竞价最终状态，不解释为15分钟变化 |
| `NORMAL_15M` | 其余16个节点 | 当前目标实际来源减上一目标实际来源 |

`base_collection_id` 和 `base_scheduled_time` 保存上一目标节点实际采用的来源，而不是机械保存
上一目标编号。`base_age_seconds` 等于当前目标时间减基准实际来源时间。

某板块当前存在、基准来源中不存在时，基准字段和全部差值为空，禁止把当前值当作全部新增。

## 4. 当前状态、份额和排名

当前字段直接来自对应板块状态：

```text
turnover_total
turnover_delta_1m_total
turnover_market_share_pct
turnover_1m_market_share_pct
up_ratio
down_ratio
limit_up_count
limit_break_count
new_high_ratio
new_low_ratio
turnover_1m_top1_share_pct
turnover_1m_top3_share_pct
turnover_1m_top5_share_pct
```

累计成交份额和一分钟成交份额排名均在同一目标、同一 `sector_type` 内独立计算。
概念、行业、风格绝不混排。排名1表示该分类最高；份额相同时按 `sector_code` 升序固定次序，
保证历史重算结果稳定。

排名变化固定为：

```text
turnover_share_rank_delta = 上一目标排名 - 当前排名
turnover_1m_share_rank_delta = 上一目标排名 - 当前排名
```

正数表示排名上升，负数表示排名下降。

## 5. 确定性差值

以下字段均为当前实际来源值减基准实际来源值：

```text
turnover_market_share_delta_15m
turnover_1m_market_share_delta_15m
up_ratio_delta_15m
down_ratio_delta_15m
limit_up_count_delta_15m
limit_break_count_delta_15m
new_high_ratio_delta_15m
new_low_ratio_delta_15m
top1_share_delta_15m
top3_share_delta_15m
top5_share_delta_15m
```

板块新增成交额：

```text
turnover_increment_15m = 当前turnover_total - 基准turnover_total
```

三类板块状态没有单独保存全市场累计成交额，但已经保存：

```text
turnover_market_share_pct
= 板块turnover_total / 全市场turnover_total * 100
```

因此仅使用现有板块状态反推当前和基准的全市场成交额，再计算：

```text
turnover_increment_market_share_pct
= 板块新增成交额 / 全市场同期新增成交额 * 100
```

当前或基准累计份额为空、为0，或反推的全市场新增成交额为0时，该字段为空。

## 6. 概念重叠与正确解释

概念板块成员高度重叠，同一只股票可以属于多个概念。因此所有概念板块的
`turnover_increment_market_share_pct` 可以远大于100%，不能相加解释为全市场资金分配。

单个概念的该字段只能解释为：该概念成员股票最近一段成交额占全市场同期新增成交额的比例。
行业和风格也按现有成员定义独立解释。数据库不建立“A板块流向B板块”的成对迁移关系。

## 7. 空值、引擎和链路

当前值或基准值任一为空，对应差值为空，禁止把空值当0。表引擎为：

```text
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id, sector_type, sector_code)
```

调度表预留：

```text
capital_migration_status
capital_migration_row_count
capital_migration_duration_ms
capital_migration_error_code
capital_migration_error_message
```

本轮只建立表、历史回算和验证，尚未接入常驻派生程序。后续链路必须等待三类板块状态完成后生成。
