# Hithink 全 A 股市场采集基础设施

这是 N100 上独立运行的新项目，使用独立 ClickHouse 数据库 `market`，不依赖旧采集项目。

## 快照主链

- `market.hithink_snapshot_schedule`：交易日固定 254 个计划节点和执行状态。
- `market.hithink_snapshot_raw`：同花顺全市场原始快照。
- `market.hithink_snapshot_derived`：个股一分钟派生数据。
- `market.hithink_limit_up_pool`：每个采集节点的官方涨停池。
- `market.hithink_limit_down_pool`：每个采集节点的官方跌停池。
- `market.hithink_market_state`：每个成功节点聚合成一行的全市场状态。
- `market.trading_calendar`：同花顺交易日历的本地事实缓存。

所有快照层统一使用 `collection_id`：

```text
YYYYMMDD001 ... YYYYMMDD254
```

编号与计划时间永久一一对应。节点失败时，计划表保留失败编号，数据表不补空行，下一节点仍使用自己的编号。跨表聚合只按同一个 `collection_id` 关联。

完整字段和派生口径见 [全市场状态表派生逻辑](docs/hithink_market_state_derivation.md)，DDL 见 [ClickHouse 结构](sql/hithink_snapshot.sql)。

## 常驻运行

Windows 计划任务名：

```text
HithinkSnapshotCollector
```

N100 开机后服务自动启动并持续在线。每天 08:50 刷新同花顺交易日历：

- 明确为交易日：生成当日 254 个节点并按绝对时间执行；
- 明确为非交易日：不生成节点；
- 接口失败但本地已有今日缓存：使用缓存；
- 接口失败且无今日缓存：进入 `CALENDAR_UNKNOWN`，每 5 分钟重试，不猜测、不采集。

每个节点并行获取全市场快照、涨停池和跌停池，完成 raw、derived、market_state 后才把计划节点标记为 `SUCCESS`。服务崩溃由 Windows 自动重启；同一节点使用固定编号和批次编号防止重复写入。

## 密钥

程序从 N100 本机外部配置读取密钥：

```text
C:\Users\DmarketosOS\.config\hithink\collector.env
```

密钥不写入仓库和日志。

## 初始化与维护

```powershell
py -m pip install -e ".[dev]"
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
