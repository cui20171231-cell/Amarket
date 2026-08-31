# A-market 数据库草案

## 1. 定位

当前数据库使用 ClickHouse，默认库名为 `market`。本文提供便于交接的结构总览；字段、类型、
排序键和兼容升级语句以 `sql/hithink_snapshot.sql` 为准。

本文不确认数据库实例当前是否启动，也不执行建表或数据检查。

## 2. 统一标识

盘中主链使用固定采集编号：

```text
collection_id = YYYYMMDD + 三位计划序号
```

每个交易日范围为 `001` 至 `254`。主链跨表关联优先使用同一 `collection_id`，同时保留：

- `trade_date`：交易日。
- `scheduled_time`：该编号对应的计划时间。
- `source_time`：外部接口返回数据所代表的真实时间。
- `batch_id`：一次实际采集或写入批次的标识。

## 3. 当前表清单

### 调度与状态

| 表 | 作用 | 主要唯一业务范围 |
|---|---|---|
| `market.trading_calendar` | 本地交易日历事实缓存 | 交易日期 |
| `market.hithink_snapshot_schedule` | 254 节点计划及各采集、派生阶段状态 | 交易日 + 计划时间 |
| `market.hithink_daily_sync_status` | 日线流程各任务状态 | 交易日 + 任务名 |
| `market.sector_mapping_sync_status` | 板块映射每次同步状态 | 日期 + 同步批次 + 板块类型 |

### 原始事实

| 表 | 作用 |
|---|---|
| `market.hithink_snapshot_raw` | 每个采集节点的全 A 原始快照 |
| `market.hithink_limit_up_pool` | 同节点官方涨停池 |
| `market.hithink_limit_down_pool` | 同节点官方跌停池 |
| `market.hithink_limit_break_pool` | 同节点炸板池 |
| `market.hithink_sector_index_snapshot` | 概念、行业和风格板块指数事实 |
| `market.hithink_daily_k_raw` | 个股原始日线 |
| `market.hithink_adjustment_events` | 除权除息等复权事件 |

### 映射事实

| 表 | 作用 |
|---|---|
| `market.sector_catalog` | 板块代码、名称、类型和启用状态 |
| `market.sector_membership_history` | 成分股成员及其历史有效区间 |

### 派生结果

| 表 | 作用 |
|---|---|
| `market.hithink_snapshot_derived` | 个股一分钟变化、量价和涨跌停标记 |
| `market.hithink_market_state` | 每个成功节点的一行全市场状态 |
| `market.hithink_concept_state` | 概念板块状态 |
| `market.hithink_industry_state` | 行业板块状态 |
| `market.hithink_style_state` | 风格板块状态 |
| `market.hithink_emotion_state` | 每个盘中节点的情绪梯队状态 |
| `market.hithink_market_delta_15m` | 19个固定检查点的市场变化 |
| `market.hithink_sector_capital_migration` | 三类板块的成交份额迁移 |
| `market.hithink_core_sector_candidate` | V1核心板块注意力候选 |
| `market.hithink_core_stock_candidate` | V1核心个股注意力候选 |
| `market.hithink_daily_k_forward` | 前复权日线结果 |

### 配置/知识层

| 表 | 作用 |
|---|---|
| `market.strategic_sector_watchlist` | 长期战略观察板块配置，不随盘中强弱自动增删 |

当前建表文件共定义 19 张表。

## 4. 数据关系

```text
trading_calendar
        ├─ hithink_snapshot_schedule
        └─ hithink_daily_sync_status

hithink_snapshot_schedule
        ├─ hithink_snapshot_raw
        ├─ hithink_limit_up_pool
        ├─ hithink_limit_down_pool
        ├─ hithink_limit_break_pool
        └─ hithink_sector_index_snapshot

hithink_snapshot_raw
        └─ hithink_snapshot_derived
                ├─ hithink_market_state
                ├─ hithink_concept_state
                ├─ hithink_industry_state
                └─ hithink_style_state

hithink_market_state + 三池
        └─ hithink_emotion_state

hithink_market_state
        └─ hithink_market_delta_15m

三类板块状态 + hithink_market_delta_15m
        └─ hithink_sector_capital_migration
                └─ hithink_core_sector_candidate
                        └─ hithink_core_stock_candidate

以上状态/候选 + strategic_sector_watchlist
        └─ 本地聚合数据包（文件，不是数据库表）
                └─ 本地聚合包

sector_catalog + sector_membership_history
        └─ 三类板块状态

hithink_daily_k_raw + hithink_adjustment_events
        └─ hithink_daily_k_forward
```

## 5. 保存和更新方式

- 高频原始快照和个股派生使用按月分区的普通合并表，按交易日、时间和证券排序。
- 状态、映射、板块指数和日线等允许同一业务键出现新版记录的表，使用版本时间保留最新结果。
- 查询此类表的权威最新状态时通常需要使用 `FINAL`；具体查询应根据性能和一致性要求审核。
- 表结构文件包含 `ALTER TABLE ... IF NOT EXISTS` 等兼容语句，用于已有环境逐步补齐字段。
- 任何清表、回填、重算或结构升级都属于数据变更，必须单独获得用户授权。

## 6. 空值和失败语义

- `NULL` 表示来源缺失或当前条件下不可计算，不应自动改成 `0`。
- 采集失败时，计划表保留状态；事实表和派生表不生成虚假空行。
- 依赖同节点官方池的指标，在缺少同编号池事实时保持空值。
- 上一交易日对比缺少官方上一交易日同编号数据时保持空值，不跨日向前猜测。

## 7. 安全边界

- ClickHouse 容器端口在现有配置中只绑定 `127.0.0.1`。
- 数据库账号和密码从本地密钥配置读取，不写入本文。
- AI 查询入口强制只读，并限制最大返回 10,000 行、最长 120 秒；默认限制更小。
- 结构总览不能作为直接执行数据库变更的授权。

## 8. 结构变更检查清单

1. 明确新增或修改的业务口径。
2. 修改 `sql/hithink_snapshot.sql`，保证新环境和已有环境都能处理。
3. 同步修改写入、读取和派生代码。
4. 增加相应测试。
5. 更新本文及相关派生文档。
6. 在获得明确授权后，才对实际数据库执行结构变更或历史重算。
