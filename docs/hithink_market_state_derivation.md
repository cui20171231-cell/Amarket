# 全市场状态表派生逻辑

表：`market.hithink_market_state`

## 1. 采集编号与固定时间线

每个交易日固定生成 254 个计划节点。`collection_id` 是所有采集层统一使用的主关联键，格式为：

```text
YYYYMMDD + 三位采集序号
```

例如 2026-08-27：`20260827001` 至 `20260827254`。

| 序号 | 时间范围 | 节点数 | 阶段 |
|---:|---|---:|---|
| 001-011 | 09:15:00-09:25:00，每分钟 | 11 | `auction_open` |
| 012-132 | 09:30:15-11:30:15，每分钟 | 121 | `continuous_am` |
| 133-249 | 13:00:15-14:56:15，每分钟 | 117 | `continuous_pm` |
| 250 | 14:56:55 | 1 | `auction_close` |
| 251-254 | 14:57:00-15:00:00，每分钟 | 4 | `auction_close` |

关键映射：

```text
20260827001 = 09:15:00
20260827011 = 09:25:00
20260827012 = 09:30:15
20260827132 = 11:30:15
20260827133 = 13:00:15
20260827249 = 14:56:15
20260827250 = 14:56:55
20260827254 = 15:00:00
```

序号只由计划决定，绝不由采集结果决定。某次采集失败时：

- `schedule` 保留该 `collection_id` 并记录 `FAILED` 或 `MISSED`；
- `raw`、`derived`、`market_state` 不伪造数据行；
- 下一节点继续使用它自己的编号，不前移、不补号；
- 所有跨表读取按 `collection_id` 精确匹配。

因此，一天最多 254 行市场状态；失败一个节点就少一行，但其编号仍永久属于原计划时间。

## 2. 单节点处理顺序

每个节点同时获取三组同节点事实：

```text
全市场快照 + 涨停池 + 跌停池
        ↓
写 raw、同节点涨停池、同节点跌停池
        ↓
写 derived
        ↓
按 collection_id 聚合 market_state
        ↓
schedule 标记 SUCCESS
```

任何必需接口或写入失败，该节点标记 `FAILED`，不生成市场状态行。

## 3. 状态表一行的来源

```text
hithink_snapshot_raw
        ↓ 个股分钟派生
hithink_snapshot_derived
        ↓ 同 collection_id 聚合
hithink_market_state

hithink_limit_up_pool / hithink_limit_down_pool
        ↓ 同 collection_id 精确关联
涨跌停相关字段
```

| 字段 | 含义 |
|---|---|
| `trade_date` | 交易日 |
| `collection_id` | `YYYYMMDD001-YYYYMMDD254` 的固定采集编号 |
| `scheduled_time` | 编号对应的计划时间，不是程序实际启动时间 |
| `source_time` | 全市场快照的实际数据时间 |
| `session` | 所属交易阶段 |
| `stock_total` | 同编号 `derived` 实际证券行数 |
| `valid_stock_count` | 涨跌幅非空的证券数 |

## 4. 涨跌广度与九档分布

`pct` 表示个股 `price_change_ratio_pct`，单位为百分点。

| 字段 | 公式 |
|---|---|
| `up_count` | `pct > 0` |
| `down_count` | `pct < 0` |
| `flat_count` | `pct = 0` |
| `up_ratio` | `up_count / valid_stock_count` |
| `down_ratio` | `down_count / valid_stock_count` |
| `limit_up_count` | 同 `collection_id` 官方涨停池且快照中存在的证券数 |
| `up_5_to_limit_count` | `pct >= 5` 且不在同编号涨停池 |
| `up_1_to_5_count` | `1 <= pct < 5` |
| `up_0_to_1_count` | `0 < pct < 1` |
| `down_0_to_1_count` | `-1 < pct < 0` |
| `down_1_to_5_count` | `-5 < pct <= -1` |
| `down_5_to_limit_count` | `pct <= -5` 且不在同编号跌停池 |
| `limit_down_count` | 同 `collection_id` 官方跌停池且快照中存在的证券数 |

严禁用收盘最终涨跌停名单回填盘中历史。旧的盘后一次性池记录没有 `collection_id`，不会参与分钟状态计算。若某历史节点没有同编号池证据，上述四个依赖池的字段写 `NULL`。

## 5. 成交额和成交量

| 字段 | 公式 |
|---|---|
| `turnover_total` | 同编号全部个股累计成交额之和 |
| `volume_total` | 同编号全部个股累计成交量之和 |
| `turnover_delta_1m_total` | `SUM(derived.turnover_delta_1m)` |
| `volume_delta_1m_total` | `SUM(derived.volume_delta_1m)` |
| `prev_turnover_delta_1m_total` | 上一编号状态行的有效一分钟成交额增量 |
| `turnover_growth_1m_market` | `当前一分钟成交额增量 / 上一分钟成交额增量 - 1` |

市场一分钟增量以个股 `derived` 的有效增量求和为权威口径，不再用两个市场累计总值直接相减。这样个股计数器倒退、重置和缺失值会先在个股层被拦截。

## 6. 一阶与二阶一分钟指标

一阶指标只需要 `t` 与 `t-1`：同一交易日、同一 session、相邻计划编号、计划时间恰好相差 60 秒。

```text
turnover_delta_1m
volume_delta_1m
new_high_flag / new_low_flag
price_delta_1m / price_change_1m_pct
```

二阶指标需要 `t`、`t-1`、`t-2` 形成连续分钟链，因为 `t-1` 自身必须已有有效增量：

```text
turnover_growth_1m
volume_ratio_1m
turnover_growth_1m_market
资金加减速计数
量能扩张收缩计数
量价组合计数
```

例：若序号 209 失败，则 210 的一阶和二阶指标均为 `NULL`；211 的一阶恢复、二阶仍为 `NULL`；212 的二阶恢复。

## 7. 上一交易日同序号

先从 `market.trading_calendar` 找到严格的上一交易日，再构造该日期的相同三位序号 `collection_id`。不从状态表向前猜最近日期，也不跳到上上个交易日。

| 字段 | 公式 |
|---|---|
| `yesterday_same_time_turnover` | 官方上一交易日相同采集序号的 `turnover_total` |
| `turnover_prev_day_delta` | 当前 `turnover_total - yesterday_same_time_turnover` |
| `turnover_prev_day_pct` | `turnover_prev_day_delta / yesterday_same_time_turnover` |

上一交易日相同编号缺行时，这三个字段全部为 `NULL`。

## 8. 个股派生聚合

| 市场字段 | 聚合逻辑 |
|---|---|
| `turnover_accel_count` | `turnover_growth_1m > 0` 的证券数 |
| `turnover_decel_count` | `turnover_growth_1m < 0` 的证券数 |
| `turnover_accel_50_count` | `turnover_growth_1m >= 0.5` 的证券数 |
| `turnover_accel_100_count` | `turnover_growth_1m >= 1` 的证券数 |
| `volume_expand_count` | `volume_ratio_1m > 1` 的证券数 |
| `volume_contract_count` | `volume_ratio_1m < 1` 的证券数 |
| `volume_ratio_1_5_count` | `volume_ratio_1m > 1.5` 的证券数 |
| `volume_ratio_2_count` | `volume_ratio_1m > 2` 的证券数 |
| `volume_ratio_3_count` | `volume_ratio_1m > 3` 的证券数 |
| `new_high_count` | `new_high_flag = 1` 的证券数 |
| `new_low_count` | `new_low_flag = 1` 的证券数 |
| `price_up_1m_count` | `price_delta_1m > 0` 的证券数 |
| `price_down_1m_count` | `price_delta_1m < 0` 的证券数 |
| `price_flat_1m_count` | `price_delta_1m = 0` 的证券数 |

若整个节点没有任何可计算样本，对应市场字段写 `NULL`，不能用 0 代替“不可计算”。

## 9. 量价组合与相邻变化

| 字段 | 公式 |
|---|---|
| `volume_price_up_count` | `volume_ratio_1m > 1 AND price_delta_1m > 0` |
| `volume_price_down_count` | `volume_ratio_1m > 1 AND price_delta_1m < 0` |
| `contract_price_up_count` | `volume_ratio_1m < 1 AND price_delta_1m > 0` |
| `contract_price_down_count` | `volume_ratio_1m < 1 AND price_delta_1m < 0` |

以下字段等于当前值减上一连续分钟值：`up_count_delta_1m`、`down_count_delta_1m`、`new_high_count_delta_1m`、`new_low_count_delta_1m`、`volume_price_up_delta_1m`、`volume_price_down_delta_1m`。

上一编号缺行、跨 session、时间不相差 60 秒，或者两端依赖值为空时，结果为 `NULL`。

## 10. 查询与重算

状态表使用 `ReplacingMergeTree(version_time)`。同一交易日、同一计划时间重新计算时，以最新版本为准：

```sql
SELECT *
FROM market.hithink_market_state FINAL
WHERE trade_date = 'YYYY-MM-DD'
ORDER BY collection_id;
```
