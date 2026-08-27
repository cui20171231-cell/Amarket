# 全市场状态表派生逻辑

表：`market.hithink_market_state`

## 1. 一行代表什么

一行代表一个**实际成功采到的全市场快照节点**，键为：

```text
trade_date + scheduled_time
```

固定计划的完整交易日有 314 个节点：

```text
auction_open     09:15:00 - 09:25:00，每分钟，11 行
continuous_am    09:30:15 - 11:30:15，每分钟，121 行
continuous_pm    13:00:15 - 15:56:15，每分钟，177 行
auction_close    15:56:55、15:57、15:58、15:59、16:00，5 行
```

因此，完整交易日目标为 314 行；某个快照节点未成功采到，就不创建该状态行，绝不补造。

## 2. 数据来源与计算顺序

```text
hithink_snapshot_raw
  └─ 同花顺全市场原始快照事实
        ↓
hithink_snapshot_derived
  └─ 每只股票与合法上一分钟快照的个股派生指标
        ↓
hithink_market_state
  └─ 对同一 scheduled_time 的全部个股派生快照聚合为一行

hithink_limit_up_pool / hithink_limit_down_pool
  └─ 提供真实涨停、真实跌停证券名单
```

采集服务每个节点的写入顺序固定为：

```text
调用快照接口
→ 写入 raw
→ 写入 derived
→ 写入 market_state
→ 写入 schedule SUCCESS
```

历史回算命令只会遍历 `hithink_snapshot_derived` 中已经实际存在的节点。

## 3. 时间与覆盖字段

| 字段 | 派生逻辑 |
|---|---|
| `trade_date` | 当前快照交易日。 |
| `scheduled_time` | 固定计划节点时间，不是临时运行时间。 |
| `source_time` | 同一节点所有个股快照的最大实际源时间；正常情况下所有个股相同。 |
| `session` | 计划节点所属阶段：`auction_open`、`continuous_am`、`continuous_pm`、`auction_close`。 |
| `stock_total` | 该节点 `derived` 实际行数，即采到的全市场证券总数。 |
| `valid_stock_count` | `price_change_ratio_pct IS NOT NULL` 的证券数。 |

## 4. 涨跌广度与互斥涨跌区间

涨跌幅字段全部使用个股 `price_change_ratio_pct`，单位为百分数。

| 字段 | 公式 |
|---|---|
| `up_count` | `pct > 0` 的证券数。 |
| `down_count` | `pct < 0` 的证券数。 |
| `flat_count` | `pct = 0` 的证券数。 |
| `up_ratio` | `up_count / valid_stock_count`；有效证券为 0 时写 `0.0`。 |
| `down_ratio` | `down_count / valid_stock_count`；有效证券为 0 时写 `0.0`。 |
| `limit_up_count` | 当日官方 `hithink_limit_up_pool` 中、且当前快照存在的证券数。绝不以 ±9% 推断。 |
| `limit_down_count` | 当日官方 `hithink_limit_down_pool` 中、且当前快照存在的证券数。绝不以 ±9% 推断。 |
| `up_0_to_1_count` | `0 < pct < 1`。 |
| `up_1_to_5_count` | `1 <= pct < 5`。 |
| `up_5_to_limit_count` | `pct >= 5` 且不在官方涨停池。 |
| `down_0_to_1_count` | `-1 < pct < 0`。 |
| `down_1_to_5_count` | `-5 < pct <= -1`。 |
| `down_5_to_limit_count` | `pct <= -5` 且不在官方跌停池。 |

上述区间互斥；涨停、跌停从官方池单独计数。

## 5. 全市场成交额与成交量

| 字段 | 公式 |
|---|---|
| `turnover_total` | 当前节点全部可用个股 `turnover` 之和。 |
| `volume_total` | 当前节点全部可用个股 `volume` 之和。 |
| `turnover_delta_1m_total` | 合法上一节点存在时：`当前 turnover_total - 上一状态 turnover_total`。只有结果非负才写入，否则 `NULL`。 |
| `prev_turnover_delta_1m_total` | 合法上一节点的 `turnover_delta_1m_total`；上一节点本身无该值则 `NULL`。 |
| `turnover_growth_1m_market` | 当前一分钟成交额增量 / 上一分钟成交额增量 - 1。上分钟增量必须大于 0，否则 `NULL`。 |
| `volume_delta_1m_total` | 合法上一节点存在时：`当前 volume_total - 上一状态 volume_total`。只有结果非负才写入，否则 `NULL`。 |

“合法上一节点”必须同时满足：

```text
同一 trade_date
同一 session
scheduled_time 恰好相差 60 秒
```

所以 09:15、09:30:15、13:00:15、15:56:55、15:57 的一分钟字段必为 `NULL`；15:58、15:59、16:00 可以正常计算。

## 6. 上一交易日同刻

| 字段 | 公式 |
|---|---|
| `yesterday_same_time_turnover` | 在已有状态行中寻找 `< trade_date` 的最近交易日，并严格匹配相同 `scheduled_time`，取得其 `turnover_total`。找不到即 `NULL`。 |
| `turnover_yoy_delta` | `turnover_total - yesterday_same_time_turnover`。没有昨日同刻即 `NULL`。 |
| `turnover_yoy_pct` | `turnover_yoy_delta / yesterday_same_time_turnover`。昨日同刻必须大于 0，否则 `NULL`。 |

这里不使用自然日猜测；只有存在上一交易日的真实状态行才计算。

## 7. 个股一分钟派生指标的前提

下列指标来自 `hithink_snapshot_derived`。每只股票只有在自身当前与上一节点满足“合法上一节点”，且所需原始字段都存在时才会有值。

```text
turnover_delta_1m = 当前 turnover - 上一节点 turnover
volume_delta_1m   = 当前 volume - 上一节点 volume
turnover_growth_1m = 当前 turnover_delta_1m / 上一 turnover_delta_1m - 1
volume_ratio_1m    = 当前 volume_delta_1m / 上一 volume_delta_1m
new_high_flag       = 当前 high_price > 上一 high_price
new_low_flag        = 当前 low_price < 上一 low_price
price_delta_1m      = 当前 last_price - 上一 last_price
```

若某类个股一分钟指标在整个市场节点都不存在，该类市场级字段写 `NULL`，不能以 0 代替“不可计算”。若已有合法个股指标，则相应计数可以为 0。

## 8. 资金加减速

| 字段 | 公式 |
|---|---|
| `turnover_accel_count` | `turnover_growth_1m > 0` 的个股数。 |
| `turnover_decel_count` | `turnover_growth_1m < 0` 的个股数。 |
| `turnover_accel_50_count` | `turnover_growth_1m >= 0.5` 的个股数。 |
| `turnover_accel_100_count` | `turnover_growth_1m >= 1` 的个股数。 |

## 9. 量能、新高新低与价格方向

| 字段 | 公式 |
|---|---|
| `volume_expand_count` | `volume_ratio_1m > 1` 的个股数。 |
| `volume_contract_count` | `volume_ratio_1m < 1` 的个股数。 |
| `volume_ratio_1_5_count` | `volume_ratio_1m > 1.5` 的个股数。 |
| `volume_ratio_2_count` | `volume_ratio_1m > 2` 的个股数。 |
| `volume_ratio_3_count` | `volume_ratio_1m > 3` 的个股数。 |
| `new_high_count` | `new_high_flag = 1` 的个股数。 |
| `new_low_count` | `new_low_flag = 1` 的个股数。 |
| `price_up_1m_count` | `price_delta_1m > 0` 的个股数。 |
| `price_down_1m_count` | `price_delta_1m < 0` 的个股数。 |
| `price_flat_1m_count` | `price_delta_1m = 0` 的个股数。 |

## 10. 量价组合

| 字段 | 公式 |
|---|---|
| `volume_price_up_count` | `volume_ratio_1m > 1 AND price_delta_1m > 0`。 |
| `volume_price_down_count` | `volume_ratio_1m > 1 AND price_delta_1m < 0`。 |
| `contract_price_up_count` | `volume_ratio_1m < 1 AND price_delta_1m > 0`。 |
| `contract_price_down_count` | `volume_ratio_1m < 1 AND price_delta_1m < 0`。 |

## 11. 相对上一分钟的市场变化

这六个字段均要求“合法上一节点”。对于依赖可空字段的项目，当前值和上一状态值都必须存在；否则写 `NULL`。

| 字段 | 公式 |
|---|---|
| `up_count_delta_1m` | `当前 up_count - 上一 up_count`。 |
| `down_count_delta_1m` | `当前 down_count - 上一 down_count`。 |
| `new_high_count_delta_1m` | `当前 new_high_count - 上一 new_high_count`。 |
| `new_low_count_delta_1m` | `当前 new_low_count - 上一 new_low_count`。 |
| `volume_price_up_delta_1m` | `当前 volume_price_up_count - 上一 volume_price_up_count`。 |
| `volume_price_down_delta_1m` | `当前 volume_price_down_count - 上一 volume_price_down_count`。 |

## 12. 版本与重算

表引擎为：

```sql
ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, scheduled_time)
```

同一交易日、同一计划节点重新计算时，以最新 `version_time` 的状态行作为有效版本。查询状态时使用：

```sql
SELECT *
FROM market.hithink_market_state FINAL
WHERE trade_date = 'YYYY-MM-DD'
ORDER BY scheduled_time;
```
