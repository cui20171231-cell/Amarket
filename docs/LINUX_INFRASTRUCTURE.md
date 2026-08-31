# Linux 数据库基础服务

更新日期：2026-08-31（北京时间）

## 当前正式架构

ClickHouse 已从 Docker Desktop 迁移到本机 Ubuntu 26.04 WSL2 中的独立 Docker Engine：

```text
Windows 开机
  → AmarketLinuxInfrastructure（DmarketosOS，S4U，无需交互登录）
  → Ubuntu WSL2
  → Linux Docker Engine（systemd 自动启动）
  → amarket-clickhouse（restart: unless-stopped）
  → Windows 127.0.0.1:8123 / 9000
  → HithinkSnapshotCollector（SYSTEM）
```

Windows 任务除开机触发外，每分钟还会尝试启动一次；任务已运行时会忽略重复启动。Ubuntu 内的 `amarket-wsl-host` 每30秒检查 Docker 和正式 ClickHouse 容器，容器停止时启动，健康检查连续失败时重启。

## 正式资源

- Windows 启动任务：`AmarketLinuxInfrastructure`
- Linux 容器：`amarket-clickhouse`
- Linux Compose 配置：`compose.linux.yaml`
- Linux 数据目录：`/var/lib/amarket/clickhouse`
- Linux 看护程序：`/usr/local/sbin/amarket-wsl-host`
- Windows 心跳证据：`.runtime/linux_infrastructure_heartbeat.json`

采集程序仍使用 `127.0.0.1:9000`，不需要感知数据库已经迁移到 Linux。

## 迁移核对结果

- 迁移前完整数据目录约 2.4GB。
- 离线备份文件约 1.496GB。
- 备份 SHA-256：`72BCF9692F4D22A15C6D580BB922CB7B63C954AC330570D6354D19AEE3ABCDA5`。
- 业务库 `market` 迁移前后均为 9,454,894 行、501.38MiB。
- 全部业务表逐表行数一致。
- Ubuntu 强制停止后，Windows 任务在 58.9 秒内自动恢复 Linux Docker 和测试 ClickHouse。
- 停止 Docker Desktop 后，正式 Linux ClickHouse、主采集任务和 Linux 基础服务任务仍正常运行。

## 回退保留

迁移完成后没有删除以下内容：

- Docker Desktop 中的旧容器 `hithink-snapshot-clickhouse-1`（保持停止）；
- Docker Desktop 旧数据卷 `hithink-snapshot_clickhouse_data`；
- 本机迁移备份 `.runtime/clickhouse-migration/clickhouse-data.tgz`。

Docker Desktop 的当前用户登录自启动项已删除，但软件仍保留。需要回退时必须先停止 Linux 正式容器，避免 8123/9000 端口冲突，再手动启动 Docker Desktop 和旧容器。

## 尚需现场验收

1. Windows 重启后在登录界面停留至少2分钟。
2. 登录后核对心跳时间晚于本次 Windows 开机时间。
3. 核对 Linux ClickHouse健康、主采集任务运行、Linux基础服务任务运行。
4. 下一个真实交易日完成08:50至16:30的全链路验收。
