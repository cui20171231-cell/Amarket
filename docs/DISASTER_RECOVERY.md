# A-market 整机灾难恢复手册

更新日期：2026-09-02（北京时间）

## 先说结论

Git 仓库负责保存并恢复：程序代码、采集时间表、派生与聚合逻辑、数据库建表语句、固定依赖版本、Windows 管理员计划任务安装方法、Linux 数据库启动方法和操作手册。

Git 仓库不能保存密码、接口密钥和不断增长的历史行情数据库。历史数据必须另存到另一块物理硬盘、网络存储或云端。只有“Git 仓库 + 机器外的数据备份 + 另行保管的凭据”三者都存在，才能达到整块硬盘损坏后完整恢复的标准。

2026-09-02 的现场检查结果：

- 代码和数据库结构可以由仓库重建；
- 当前业务库共有 26 张表，其中 25 张正式表都已写入 `sql/hithink_snapshot.sql`；多出的旧表 `hithink_auction_snapshot` 为空，不属于当前运行链路；
- 当前业务库约 12,818,111 行、761,899,376 字节；
- 本机已有一个 1,606,673,359 字节的数据库归档，但它位于 `D:\Amarket\.runtime`，与项目在同一块硬盘，不能算灾难备份；
- 本机没有发现可用于保存完整数据备份的第二块物理硬盘；因此历史数据的异机备份目前仍未达标。

## 一条节点的实际执行顺序

常驻入口是 `py -3.11 -m app.hithink.cli serve`。交易日 08:50 开始确认交易日并建立 254 个固定节点。每个节点按下面顺序执行：

```text
同花顺接口采集原始事实
  → 写入原始快照、涨停池、跌停池、炸板池、板块指数
  → 派生个股状态、全市场状态、三类板块状态
  → 派生情绪、15分钟变化、板块成交额迁移、核心候选
  → 仅在19个固定观察节点生成本地聚合包
```

任一前置环节失败，后面的环节会明确标记失败或阻塞，不会把缺失数据当成功。第 254 个收盘节点会每分钟重试，直到完整成功。

主要代码位置：

- 总调度和执行先后：`app/hithink/runner.py`
- 254 个节点和 19 个聚合节点：`app/hithink/schedule.py`
- 原始采集：`app/hithink/raw_collector.py`、`app/hithink/api.py`
- 基础派生：`app/hithink/state_deriver.py`、`app/hithink/derive.py`
- 后续派生：`app/hithink/post_derivation.py`
- 聚合触发和原子写文件：`app/hithink/aggregation.py`
- 聚合包字段内容：`app/market_state_package.py`
- 所有正式表结构：`sql/hithink_snapshot.sql`

数据库表的业务含义、字段和派生公式分别记录在 `README.md`、`docs/DATABASE_DRAFT.md` 以及 `docs/*_derivation.md` 中。

## 当前必须恢复的后台服务

| 名称 | 身份 | 作用 | 仓库内恢复入口 |
|---|---|---|---|
| `AmarketLinuxInfrastructure` | 当前 Windows 用户，最高权限、无需登录 | 启动 Ubuntu、Docker 和数据库看护 | `scripts/bootstrap_linux_infrastructure.ps1` |
| `HithinkSnapshotCollector` | `SYSTEM` | 常驻采集、派生和聚合 | `scripts/install_collector_task.ps1` |
| `MarketOSStatus-TrayIcons` | 当前登录用户，最高权限 | 桌面状态图标 | `scripts/install_tray_task.ps1` |
| `MarketOS-Gateway` | `SYSTEM`，按需 | 本机人工智能读取网关 | `INSTALL_AI_GATEWAY.cmd` 和 `scripts/install_gateway_tasks.ps1` |
| `MarketOS-Tunnel` | `SYSTEM`，按需 | 外部安全通道 | `scripts/install_gateway_tasks.ps1` |
| `AmarketEmailNotifier` | `SYSTEM`，按需 | 独立邮件告警 | `scripts/configure_email_notifications.ps1` |

`HithinkDailyPipeline` 和 `HithinkSectorMapping` 不应单独安装；它们已经由主采集服务内部调度。测试和一次性核对任务不属于正式恢复范围。

## 固定的软件版本

本次检查过的运行组合是：

- Windows Python 3.11.9；
- `clickhouse-connect` 0.15.1；
- `httpx` 0.28.1；
- `pyarrow` 25.0.1；
- `tzdata` 2026.3；
- `pytest` 8.4.2；
- `ruff` 0.16.4；
- 人工智能网关 `mcp` 1.28.1；
- WSL 2.7.12.0、Ubuntu 26.04、Docker 29.7.2、Docker Compose 5.5.0；
- ClickHouse 镜像版本 26.3。

主程序固定版本见 `requirements-recovery.txt`；人工智能网关和飞书分别使用各自目录中的固定依赖文件。

## 不能上传 Git 的内容

以下内容已由 `.gitignore` 排除：

- `secrets/`：同花顺接口密钥和数据库密码；
- `.env`：飞书等本机凭据；
- `.runtime/`：运行状态和本机数据库迁移归档；
- `data/`：历史聚合包和运行数据；
- `logs/`：日志；
- `backups/`、`temp/`：备份和一次性核对文件。

恢复新机器时必须重新提供：

1. 同花顺接口密钥；
2. ClickHouse 密码；
3. 如需外部通道，提供通道编号和通道接口密钥；
4. 如需邮件告警，重新输入 Gmail 应用密码。旧机器的邮件密码使用 Windows 本机加密，复制文件到新机器也无法解密；
5. 如需飞书，重新提供飞书应用凭据。

## 平时制作机器外备份

将移动硬盘或另一块物理硬盘挂为例如 `E:`，确认当前没有节点正在采集，然后在管理员 PowerShell 中执行：

```powershell
Set-Location D:\Amarket
powershell -ExecutionPolicy Bypass -File .\scripts\backup_clickhouse_off_machine.ps1 `
  -BackupDirectory E:\Amarket-backups `
  -ConfirmServicePause
```

脚本会短暂停止数据库，制作带时间的压缩包，重新启动数据库，检查压缩包能否读取，并生成 SHA-256 校验文件。备份目录必须与 `D:\Amarket` 位于不同盘符；更稳妥的做法是备份完成后再同步到云端或另一台机器。

聚合包位于 `data\market_state_packages`，它可以从完整数据库重新生成。如果要求保留当时生成的原文件，也应把该目录定期复制到同一个机器外备份位置。

## 硬盘完全损坏后的恢复顺序

### 1. 准备空白系统

安装 Windows、Git、Python 3.11、WSL2 和 Ubuntu。使用原来的 GitHub 账号取得私有仓库读取权限。

### 2. 克隆正确分支

```powershell
git clone --branch clickhouse-database https://github.com/cui20171231-cell/Amarket.git D:\Amarket
Set-Location D:\Amarket
```

当前 Linux 看护和日志服务明确要求项目位于 `D:\Amarket`，不要随意改目录。

### 3. 重新输入采集凭据

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure_collector_credentials.ps1
```

凭据只写入本机 `secrets\collector.env`，不会显示，也不会加入 Git。数据库密码必须与备份制作时使用的密码一致。

### 4. 恢复核心系统和历史数据库

在管理员 PowerShell 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\restore_amarket_host.ps1 `
  -ClickHouseBackupFile E:\Amarket-backups\amarket-clickhouse-YYYYMMDD-HHMMSS.tgz
```

这个入口会安装固定 Python 依赖、安装项目、准备 Ubuntu Docker、启动 ClickHouse、恢复历史库、核对建表语句，并安装主采集任务和状态图标任务。

没有历史备份时可以不传 `-ClickHouseBackupFile`，系统能重新开始采集，但旧历史数据不会回来。

### 5. 按需恢复外部通道

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure_tunnel_credentials.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\restore_amarket_host.ps1 -InstallGatewayAndTunnel
```

### 6. 按需恢复邮件和飞书

邮件：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure_email_notifications.ps1
```

飞书按照 `feishu_amarket_bot/README.md` 重新建立环境并输入凭据。

### 7. 最终验收

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_disaster_recovery.ps1 `
  -OffMachineBackupPath E:\Amarket-backups\amarket-clickhouse-YYYYMMDD-HHMMSS.tgz
py -3.11 -m pytest -q
```

验收至少要看到：Git 没有只留在本机的修改、Python 3.11 正常、数据库健康、两个核心开机任务存在、机器外数据库备份存在。恢复后的第一个真实交易日还要检查 08:50 交易日确认、254 个采集节点、16:00 日线和 19 个聚合包。

## 每次重要改动后的固定动作

1. 运行全部测试；
2. 运行 `scripts\verify_disaster_recovery.ps1`；
3. 检查 Git 中没有密钥、密码、数据库和临时文件；
4. 提交并推送 `clickhouse-database` 分支；
5. 核对远端提交编号与本机一致；
6. 制作新的机器外数据库备份，并核对 SHA-256。

只完成前五步，可以恢复程序和空数据库；六步全部完成，才可以恢复当前历史数据。
