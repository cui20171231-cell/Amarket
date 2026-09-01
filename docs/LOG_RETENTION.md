# 日志无人值守规则

## 目标

- 所有运行环节都保留可查询日志。
- 日志正常总量控制在 20GB 内，25GB 自动提前收缩，30GB 为硬上限。
- 行情数据、市场聚合包、每日计划和数据库迁移备份不属于日志，维护任务不得清理。

## 自动时间

- 每天 08:30 执行完整维护。
- Linux 没有在 08:30 运行时，下一次启动自动补执行一次。
- 每小时检查一次总量，防止两次每日维护之间发生异常膨胀。
- 维护失败不阻塞 08:50 交易日确认和后续采集。

## 保留期限

| 类型 | 保留规则 |
|---|---|
| 采集、每日任务、板块映射、网关审计日志 | 30 天并限制单卷与总卷数 |
| ClickHouse 查询、数据分区、普通文本日志 | 30 天 |
| ClickHouse 追踪、性能和指标日志 | 7 天 |
| ClickHouse 错误日志 | 90 天 |
| Linux 系统日志 | 14 天且最多 300MB |
| Docker 容器输出 | 单卷 20MB，最多 5 卷 |

## 容量保护

- 20GB 是正常目标。
- 达到 25GB 时，先淘汰三天以前的详细追踪、性能和指标日志。
- 达到 30GB 时，清空可再生的详细追踪、性能和指标日志，保留查询、错误和业务程序日志。
- 每次检查、删除和异常都会写入 `logs/log_maintenance.jsonl`，最新结果写入
  `.runtime/log_maintenance_state.json`。

## 安装与验证

Linux 中执行：

```bash
bash /mnt/d/Amarket/infra/linux/install_log_maintenance.sh
systemctl list-timers amarket-log-maintenance.timer amarket-log-guard.timer --all
```

安装脚本可以重复执行。它不会重启 Windows 或 ClickHouse，只会重新载入日志配置并更新系统诊断日志表的到期规则。
