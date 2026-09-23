# 市场约15分钟差值表派生逻辑

表：`market.hithink_market_delta_15m`

## 1. 用途与唯一来源

本表只保存固定盘中检查点相对前一张真实检查点的确定性差值。唯一来源是：

```text
market.hithink_market_state
```

每个完整交易日有19个目标检查点。目标节点的市场状态缺失时，该目标行仍然保留，并整体向前
选择同一交易时段内最近一条可用市场状态。是否生成当日数据服从现有交易日判断；非交易日不生成。
本表不保存市场强弱、阶段、扩散、收缩或风险评分等AI结论。

逻辑唯一键为 `trade_date + collection_id`。`node_seq` 由 `collection_id` 后3位自动生成，
只用于身份、排序、缺号检查和范围查询，禁止用于计算15分钟跨度。

## 2. 固定检查点及源编号

表内 `collection_id` 和 `node_seq` 直接沿用目标节点在调度表中的编号。正常完整交易日的
19个目标点固定为：

| 时间 | `node_seq` | 时间 | `node_seq` |
|---|---:|---|---:|
| 09:25:08 | 011 | 11:30:08 | 132 |
| 09:30:08 | 012 | 13:00:08 | 133 |
| 09:45:08 | 027 | 13:15:08 | 148 |
| 10:00:08 | 042 | 13:30:08 | 163 |
| 10:15:08 | 057 | 13:45:08 | 178 |
| 10:30:08 | 072 | 14:00:08 | 193 |
| 10:45:08 | 087 | 14:15:08 | 208 |
| 11:00:08 | 102 | 14:30:08 | 223 |
| 11:15:08 | 117 | 14:45:08 | 238 |
|  |  | 15:30:08（自2026-09-14起） | 254 |

## 3. 基准选择

每个目标点先在同一交易日、同一上午或下午段内选择不晚于目标时间的最近一条可用
`hithink_market_state`。目标节点自身可用时直接使用；自身缺失时整体向前回退，并记录实际来源。
上午与下午分别选择来源，绝不跨午休或交易日。

特殊规则：

```text
09:25：上午原始基线，比较字段为NULL
09:30：与09:25比较，正常跨度300秒
13:00：下午原始基线，比较字段为NULL
15:30：与14:45:08比较，实际跨度2700秒
```

`delta_type` 固定区分三种业务含义：

| `delta_type` | 节点 | 含义 |
|---|---|---|
| `SESSION_BASE` | 09:25、13:00 | 上午竞价最终基线、下午开盘基线，不计算前序差值 |
| `AUCTION_TO_OPEN` | 09:30 | 09:30状态减09:25集合竞价最终状态，不解释成15分钟变化 |
| `NORMAL_15M` | 其余16个节点 | 当前检查点与上一检查点实际采用状态的正常约15分钟差值 |

如果某个目标源节点缺失，该行使用此前最近可用状态。例如10:30节点072缺失时，使用10:29:15
节点071，`state_source_age_seconds = 60`。10:45再与10:30目标行实际采用的10:29:15状态比较，
`base_scheduled_time = 10:29:15`，`base_age_seconds = 960`。

## 4. 状态

| `delta_status` | 含义 |
|---|---|
| `VALID` | 当前目标和上一目标都有实际可用状态，可以计算差值 |
| `NO_BASE` | 09:25或13:00基线行，本身不计算比较 |
| `NO_SOURCE` | 当前或上一目标在本时段内找不到任何可用状态，不能计算差值 |

`NO_BASE` 的三个基准字段及全部差值字段必须原生为 `NULL`。

## 5. 字段来源与公式

基础及基准字段：

| 字段 | 来源或公式 |
|---|---|
| `trade_date`、`collection_id`、`scheduled_time`、`session` | 当前目标节点 |
| `node_seq` | 数据库从 `collection_id` 后3位自动生成 |
| `state_data_status` | `CURRENT`、`FALLBACK`或`NO_SOURCE` |
| `state_source_collection_id` | 当前目标实际采用的市场状态编号 |
| `state_source_scheduled_time` | 当前目标实际采用的市场状态时间 |
| `state_source_age_seconds` | 当前目标时间减实际来源时间，单位秒 |
| `state_is_fallback` | 使用自身为0，向前回退为1 |
| `base_collection_id`、`base_scheduled_time` | 上一目标行实际采用的市场状态来源 |
| `base_age_seconds` | 当前目标时间减基准实际来源时间，单位秒 |
| `delta_status` | 按第4节判断 |
| `delta_type` | 固定标记基线、竞价到开盘转换或正常15分钟比较 |

市场广度和强度分布字段均为“当前值减基准值”：

```text
up_count_delta_15m
down_count_delta_15m
flat_count_delta_15m
up_ratio_delta_15m
down_ratio_delta_15m
limit_up_count_delta_15m
up_5_to_limit_count_delta_15m
up_1_to_5_count_delta_15m
up_0_to_1_count_delta_15m
down_0_to_1_count_delta_15m
down_1_to_5_count_delta_15m
down_5_to_limit_count_delta_15m
limit_down_count_delta_15m
limit_break_count_delta_15m
```

相对前一张检查点的新增成交额：

```text
turnover_increment_15m = 当前 turnover_total - 基准 turnover_total
```

成交速度：

```text
turnover_speed_current = 当前 turnover_delta_1m_total
turnover_speed_base = 基准 turnover_delta_1m_total
turnover_speed_delta_15m = 当前一分钟成交额 - 基准一分钟成交额
turnover_speed_change_pct = 当前一分钟成交额 / 基准一分钟成交额 - 1
```

基准一分钟成交额为0或任一方为空时，速度变化比例为空。

其余结构字段也统一为当前值减基准值：

```text
turnover_accel_count_delta_15m
turnover_decel_count_delta_15m
turnover_accel_50_count_delta_15m
turnover_accel_100_count_delta_15m
volume_expand_count_delta_15m
volume_contract_count_delta_15m
volume_ratio_1_5_count_delta_15m
volume_ratio_2_count_delta_15m
volume_ratio_3_count_delta_15m
new_high_count_delta_15m
new_low_count_delta_15m
price_up_1m_count_delta_15m
price_down_1m_count_delta_15m
price_flat_1m_count_delta_15m
volume_price_up_count_delta_15m
volume_price_down_count_delta_15m
contract_price_up_count_delta_15m
contract_price_down_count_delta_15m
```

所有差值及速度字段都是 `Nullable`。当前值或基准值任一为空，对应结果为空，禁止把空值当0。

## 6. 254号收盘规则

254号必须满足：

```text
scheduled_time = 当天15:30:08（自2026-09-14起）
delta_status = VALID
state_data_status = CURRENT
state_source_collection_id = 当前254号collection_id
state_source_scheduled_time = 当天15:30:08（自2026-09-14起）
state_source_age_seconds = 0
state_is_fallback = 0
base_collection_id必须属于当天下午此前真实检查点
正常完整数据下base_scheduled_time = 14:45:08
正常完整数据下base_age_seconds = 892
calculated_at >= scheduled_time
```

数据库约束会拒绝不满足这些条件的254号差值行。

## 7. 表引擎、任务字段和链路阶段

```text
ENGINE = ReplacingMergeTree(version_time)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, collection_id)
```

计划表已经预留：

```text
market_delta_15m_status
market_delta_15m_row_count
market_delta_15m_duration_ms
market_delta_15m_error_code
market_delta_15m_error_message
```

本轮只完成表结构和历史回算，尚未接入常驻派生链。后续接入时，目标市场状态失败应按第3节
整体向前选择最近可用状态，并同时返回来源时间和数据年龄；254号仍禁止回退。

本表只持久化15分钟差。30、45、60分钟及任意节点比较由MCP基于
`hithink_market_state` 临时计算，不再建立其他固定跨度表。
