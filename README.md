# Hithink 全 A 股快照采集器

这是 N100 上的独立新项目。它只依赖一个新的 ClickHouse 数据库 `market`，不连接或复用任何 PostgreSQL、Redis、MinIO 或旧采集项目。

## 新建对象

执行 `hithink-snapshot init-db` 后，ClickHouse 中只会创建：

- `market` 数据库
- `market.hithink_snapshot_schedule`：每天 314 个计划节点及状态
- `market.hithink_snapshot_raw`：同花顺 API 的 11 个原始字段
- `market.hithink_snapshot_derived`：原始字段和固定 8 个派生字段

完整 DDL 在 [sql/hithink_snapshot.sql](sql/hithink_snapshot.sql)。DDL 不由容器自动执行，只由 `init-db` 显式执行一次。

## 密钥

程序从当前 Windows 用户的下列文件读取密钥：

```text
C:\Users\DmarketosOS\.config\hithink\collector.env
```

文件只包含：

```text
HITHINK_FINANCE_API_KEY=...
```

请求头固定为 `X-api-key`；密钥不会写入日志或项目文件。

## 初始化与采集

```powershell
py -m pip install -e ".[dev]"
hithink-snapshot init-db
hithink-snapshot run --not-before 13:00
```

`run --not-before 13:00` 会生成完整 314 节点计划，13:00 前节点标记 `MISSED`，从下午起按绝对计划时间执行。每节点是一次 API 请求、一次 raw 批量写入、一次上一合法批读取、内存批量派生、一次 derived 批量写入。

只有“同交易日、同 session、计划时间精确相差 60 秒”时，8 个派生字段才计算。首次、跨午休、09:30:15、15:56:55、15:57:00 等边界节点均为 `NULL`；15:58:00 恢复计算。

## 开机与每日自动运行

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_collector_task.ps1
```

任务名为 `HithinkSnapshotCollector`，在 Windows 启动 30 秒后及每日 09:10 启动。它使用固定绝对时间节点，不使用累计 `sleep(60)`。

## 验收 SQL

```sql
SELECT status, count()
FROM market.hithink_snapshot_schedule
WHERE trade_date = today()
GROUP BY status;

SELECT
    session,
    count() AS planned,
    countIf(status = 'SUCCESS') AS success,
    countIf(status = 'FAILED') AS failed,
    countIf(status = 'MISSED') AS missed
FROM market.hithink_snapshot_schedule
WHERE trade_date = today()
GROUP BY session
ORDER BY session;
```
