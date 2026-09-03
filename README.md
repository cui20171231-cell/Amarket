# A-market 全 A 股市场数据基础设施

当前版本：`2.0.0`。历史 `v1.0.0` 标签和 [V1.0版本范围](docs/V1.0_SCOPE.md)完整保留；
当前范围及与1.0的逐项差异见 [V2.0范围与差异](docs/V2.0_SCOPE.md)。

这是在 Intel N100 设备上独立运行的项目，使用独立 ClickHouse 数据库 `market`，不依赖旧采集项目。

项目范围和交接基线见 [总体架构](ARCHITECTURE.md)、[产品基线](docs/PRODUCT_BASELINE.md)、
[2.0 版本范围](docs/V2.0_SCOPE.md)、[1.0 历史范围](docs/V1.0_SCOPE.md)、[数据库草案](docs/DATABASE_DRAFT.md)和
[接口草案](docs/API_DRAFT.md)。当前现场状态、重启恢复约定、长期无人值守目标和剩余建设项见
[当前运行基线与长期目标](docs/CURRENT_STATE_AND_LONG_TERM_GOAL.md)；数据库的 Linux 启动、看护、
迁移核对和回退方法见 [Linux数据库基础服务](docs/LINUX_INFRASTRUCTURE.md)。整机损坏后的代码、
依赖、数据库、凭据和管理员任务恢复顺序见 [灾难恢复手册](docs/DISASTER_RECOVERY.md)。

## 快照主链

- `market.hithink_snapshot_schedule`：交易日固定 254 个计划节点和执行状态。
- `market.hithink_snapshot_raw`：同花顺全市场原始快照。
- `market.hithink_auction_snapshot`：交易日09:15:00—09:25:00的全A集合竞价原始快照。
- `market.hithink_snapshot_derived`：个股一分钟派生数据。
- `market.hithink_limit_up_pool`：每个采集节点的官方涨停池。
- `market.hithink_limit_down_pool`：每个采集节点的官方跌停池。
- `market.hithink_market_state`：每个成功节点聚合成一行的全市场状态。
- `market.hithink_market_delta_15m`：每天19个固定市场检查点；09:30单独标记为竞价到开盘转换，目标状态缺失时整体向前取最近可用状态并记录来源年龄。
- `market.hithink_emotion_state`：每个盘中节点的情绪梯队状态；普通应采节点任一池失败时三池整体回退并保留来源年龄，254号15:00收盘节点禁止回退，必须持续重采至三池全部成功。
- `market.trading_calendar`：同花顺交易日历的本地事实缓存。
- `market.hithink_sector_index_snapshot`：同编号板块指数的外部事实快照。
- `market.hithink_concept_state`、`market.hithink_industry_state`、`market.hithink_style_state`：按同编号聚合的三类板块状态。
- `market.hithink_sector_capital_migration`：三类板块统一的19节点成交份额迁移、分类排名和内部扩散状态，不表示净资金流入流出。
- `market.hithink_core_sector_candidate`、`market.hithink_core_stock_candidate`：V1核心观察候选；只保存可解释证据，不判断主线、龙头或买卖点，也不使用股本、市值和换手字段。
- `market.strategic_sector_watchlist`：配置/知识层长期战略观察名单，不随当天行情自动增删；AI战略主线V1映射见[战略观察板块说明](docs/strategic_sector_watchlist_ai_v1.md)。

所有快照层统一使用 `collection_id`：

```text
YYYYMMDD001 ... YYYYMMDD254
```

编号与计划时间永久一一对应。节点失败时，计划表保留失败编号，数据表不补空行，下一节点仍使用自己的编号。跨表聚合只按同一个 `collection_id` 关联。

完整字段和派生口径见 [全市场状态表派生逻辑](docs/hithink_market_state_derivation.md)，DDL 见 [ClickHouse 结构](sql/hithink_snapshot.sql)。

情绪梯队字段、三池整体回退和晋级口径见 [市场情绪梯队状态表派生逻辑](docs/hithink_emotion_state_derivation.md)。

市场15分钟差值字段、业务时段和基准选择口径见
[市场约15分钟差值表派生逻辑](docs/hithink_market_delta_15m_derivation.md)。

板块成交份额迁移、独立排名和重叠板块解释规则见
[板块资金迁移状态表派生逻辑](docs/hithink_sector_capital_migration_derivation.md)。

核心板块、核心个股候选的V1正式公式及V2股本、市值、换手升级边界见
[核心候选V1/V2版本说明](docs/hithink_core_candidates_v1_v2.md)。

前6张状态/候选表的第7层临时拼装、排名轨迹、30/45/60分钟比较和确定性交叉关系见
[市场状态恢复聚合数据包](docs/market_state_package.md)。该层不新增持久表；MCP只提供已有JSON包的
定位、校验和原样读取工具，不在调用时重新查询数据库或生成聚合包。
19个固定节点完成全部前置派生后，数据包会原子写入本地文件，不发送外部通知。
该步骤由独立聚合模块 `app/hithink/aggregation.py` 执行；派生模块只负责生成表数据。

所有市场表统一按 A、A2、B、B2 分类，带 `collection_id` 的表统一自动生成 `node_seq`；完整清单见
[市场表分类与节点编号规范](docs/market_table_classification.md)。

板块体系同样使用既有 `collection_id`，不另建时间轴。完整口径见 [板块状态派生逻辑](docs/hithink_sector_state_derivation.md)。

## 常驻运行

Windows 计划任务名：

```text
HithinkSnapshotCollector
```

集合竞价采集已并入 `HithinkSnapshotCollector`，使用内部独立执行通道。交易日从09:15:00到09:25:00按整数分钟采集11张全A集合竞价快照，不使用08秒延迟。08:50先申请一次当天全A名单，09:13:50重新申请并以新名单为准；重新申请失败时使用上一交易日收盘名单。09:15—09:24请求实时阶段，09:25请求终态，结果写入 `market.hithink_auction_snapshot`。

N100 开机后服务自动启动并持续在线。每天 08:50 刷新同花顺交易日历：

- 明确为交易日：先校验并补齐当日 254 个节点，再执行板块映射，最后按绝对时间采集；
- 明确为非交易日：不生成节点；
- 接口失败但本地已有今日缓存：使用缓存；
- 接口失败且无今日缓存：进入 `CALENDAR_UNKNOWN`，每 5 分钟重试，不猜测、不采集。

盘中计划必须正好包含 254 个正确编号和时间。已有残缺计划会自动补齐；存在多余节点或编号、时间
不一致时会明确报错，不会把任意一条记录误认为整日计划已经完成。板块映射最晚运行到 09:10，
失败或超时只记录映射异常，不阻断盘中节点。

第254号收盘节点从15:00开始失败后每分钟重试，最晚重试到北京时间16:00。16:00 日K使用同一常驻服务里的独立执行通道。首次失败后每分钟重试，最多重试15次，最晚到16:15；服务在16:00至16:15重启会检查并补跑未成功的当日日K，16:15后不再补跑。复权事件仍只在周一随日K任务执行。

服务在08:55、09:00、09:10、09:16、15:05、16:05和16:20检查交易日确认、两张计划表、
首节点、第254号和日K状态；未达到预期时写入 `CHECKPOINT_ALERT` 明确报警日志。

每个节点并行获取全市场快照、三张池和板块指数。原有个股、市场、三类板块状态完成后生成情绪状态；
19个固定检查点再依次生成市场变化、板块迁移、核心板块候选、核心个股候选；全部派生成功后，
独立聚合模块只生成本地数据包。
任一步失败都会记录并阻断后续步骤，不会把缺失当成功。服务崩溃由 Windows 自动重启；同一节点使用固定编号和批次编号防止重复写入。

## 密钥

程序从 N100 本机外部配置读取密钥：

```text
D:\Amarket\secrets\collector.env
```

密钥不写入仓库和日志。

## 初始化与维护

```powershell
py -3.11 -m pip install -r .\requirements-recovery.txt
py -3.11 -m pip install --no-deps -e .
hithink-snapshot init-db
powershell -ExecutionPolicy Bypass -File .\scripts\install_collector_task.ps1
```

历史结构升级后可按交易日回填固定编号和重算实际存在的状态节点：

```powershell
hithink-snapshot migrate-collection-ids --trade-date 2026-08-27
hithink-snapshot market-state-backfill --trade-date 2026-08-27
```

## 基本验收

```sql
SELECT sequence_no, collection_id, scheduled_time, status
FROM market.hithink_snapshot_schedule FINAL
WHERE trade_date = today()
ORDER BY sequence_no;

SELECT
    count() AS state_rows,
    min(collection_id),
    max(collection_id),
    uniqExact(collection_id)
FROM market.hithink_market_state FINAL
WHERE trade_date = today();
```
