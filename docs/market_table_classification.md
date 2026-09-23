# market数据库表分类与节点编号规范

这份目录是后续沟通使用的固定分类。业务数据表只使用四类名称：A、A2、B、B2。
计划表和任务状态表属于控制表，不混入业务数据分类。

## 配置/知识层

配置/知识层回答“长期必须关注什么”，不属于A、A2、B、B2，也不根据短期行情自动增删：

| 表 | 内容 |
|---|---|
| `market.strategic_sector_watchlist` | 战略观察板块、级别、原因和历史有效区间 |

## 1. 四类业务表

### A类：盘中原始采集表

正常交易日按各自固定节点采集，保存外部接口返回的原始事实，共6张：

| 表 | 内容 |
|---|---|
| `market.hithink_snapshot_raw` | 全A行情快照 |
| `market.hithink_auction_snapshot` | 09:15:00—09:25:00全A集合竞价快照 |
| `market.hithink_sector_index_snapshot` | 板块指数快照 |
| `market.hithink_limit_up_pool` | 涨停池 |
| `market.hithink_limit_down_pool` | 跌停池 |
| `market.hithink_limit_break_pool` | 炸板池 |

### A2类：日级或低频原始采集表

每天一次、每天少量次数或按低频计划采集，保存外部事实：

| 表 | 内容 |
|---|---|
| `market.hithink_daily_k_raw` | 原始日K |
| `market.hithink_adjustment_events` | 分红、送股、配股等复权事件 |
| `market.trading_calendar` | 交易日历 |
| `market.sector_catalog` | 板块目录 |
| `market.sector_membership_history` | 板块成员历史 |

### B类：盘中派生表

由A类原始事实按盘中节点计算。A与B是类别对应关系，不表示每张A表必须只对应一张B表：

| 表 | 内容 |
|---|---|
| `market.hithink_snapshot_derived` | 个股盘中派生状态 |
| `market.hithink_market_state` | 全市场状态 |
| `market.hithink_market_delta_15m` | 全市场19个固定检查点；缺失时向前取最近可用状态并记录来源年龄 |
| `market.hithink_emotion_state` | 市场情绪梯队状态 |
| `market.hithink_concept_state` | 概念板块状态 |
| `market.hithink_industry_state` | 行业板块状态 |
| `market.hithink_style_state` | 风格板块状态 |
| `market.hithink_sector_capital_migration` | 三类板块19个固定节点的成交份额迁移、分类排名及确定性差值 |
| `market.hithink_core_sector_candidate` | V1盘中核心板块注意力候选及可解释命中证据 |
| `market.hithink_core_stock_candidate` | V1盘中核心个股注意力候选及可解释命中证据 |

### B2类：日级或低频派生表

由A2类事实按日级或低频节奏计算：

| 表 | 内容 |
|---|---|
| `market.hithink_daily_k_forward` | 前复权日K |

以后新增表按同一规则归类：原始/派生决定A/B，盘中高频/日级低频决定是否带2。

## 2. 控制表

下列表只记录任务计划、执行状态和错误，不是市场事实或市场派生结果：

| 表 | 内容 |
|---|---|
| `market.hithink_snapshot_schedule` | 盘中001—254节点计划及执行状态 |
| `market.hithink_daily_sync_status` | 日级任务执行状态 |
| `market.sector_mapping_sync_status` | 板块映射同步状态 |

## 3. `collection_id`与`node_seq`

所有带11位 `collection_id` 的表统一保留原字段，并新增：

```text
node_seq UInt16 MATERIALIZED toUInt16(substring(toString(collection_id), 9, 3))
```

示例：

```text
20260828001 → node_seq = 1
20260828254 → node_seq = 254
```

固定规则：

1. `node_seq` 由数据库自动生成，业务程序不得写入。
2. 有效范围固定为1—254，数据库约束拒绝范围外编号。
3. 原 `collection_id`、日期字段、排序键和唯一语义全部保持不变。
4. `node_seq` 只用于身份、排序、缺号检查和范围查询，不能推算时间差。
5. 分钟比较仍使用 `scheduled_time`；节点可能缺失，禁止用编号相减代表分钟数。
6. A2、B2表即使现有 `collection_id` 以254结尾，类别仍由采集频率和数据角色决定。

当前带 `node_seq` 的19张表包括：5张A表、2张带编号的A2表、10张B表、1张B2表，
以及盘中控制表 `hithink_snapshot_schedule`。

## 4. A类与B类的254号收盘硬规则

自2026-09-14起，`node_seq = 254` 是15:30正式收盘节点，A类和B类表都禁止用此前节点冒充。

A类中使用254号收盘节点的表统一满足：

1. `scheduled_time` 自2026-09-14起必须是所属交易日15:30:08；更早历史保持原计划时间。
2. `ingest_time` 必须不早于该交易日的正式收盘计划时间，证明该行是在收盘节点到达后落库。
3. 接口 `source_time` 最多只能比15:30早31分钟，允许收盘后的接口继续返回15:00附近的最终时间戳，
   但拒绝更早的盘中来源。

B类6张表统一满足：

1. `scheduled_time` 自2026-09-14起必须是所属交易日15:30:08；更早历史保持原计划时间。
2. 来源时间必须属于收盘附近的新来源，不能使用14:56等旧节点。
3. `calculated_at` 或派生落库时间必须不早于该交易日的正式收盘计划时间。
4. 三类板块状态的板块指数来源不能为空。
5. 情绪状态继续执行更严格的规则：三池共同来源必须等于自身254号、来源年龄为0、
   `pool_data_status = CURRENT`、所有回退标记为0。

这些是数据库自身执行的写入约束，任何不符合条件的新254记录都会被拒绝。数据库单表约束不能跨表
判断采集任务是否成功；“A类254全部成功后才启动B类254派生”的执行顺序，归采集与派生链路负责，
在后续链路修改中处理。
