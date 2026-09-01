#!/usr/bin/env bash
set -euo pipefail

project_root="/mnt/d/Amarket"

install -D -m 0644 \
    "${project_root}/infra/systemd/amarket-log-maintenance.service" \
    /etc/systemd/system/amarket-log-maintenance.service
install -D -m 0644 \
    "${project_root}/infra/systemd/amarket-log-maintenance.timer" \
    /etc/systemd/system/amarket-log-maintenance.timer
install -D -m 0644 \
    "${project_root}/infra/systemd/amarket-log-guard.service" \
    /etc/systemd/system/amarket-log-guard.service
install -D -m 0644 \
    "${project_root}/infra/systemd/amarket-log-guard.timer" \
    /etc/systemd/system/amarket-log-guard.timer
install -D -m 0644 \
    "${project_root}/infra/systemd/60-amarket-retention.conf" \
    /etc/systemd/journald.conf.d/60-amarket-retention.conf

systemctl daemon-reload
systemctl enable --now amarket-log-maintenance.timer amarket-log-guard.timer
systemctl kill --kill-whom=main --signal=HUP systemd-journald.service

docker cp \
    "${project_root}/infra/clickhouse/config.d/log_retention.xml" \
    amarket-clickhouse:/etc/clickhouse-server/config.d/log_retention.xml
docker exec amarket-clickhouse clickhouse-client --query "SYSTEM RELOAD CONFIG"
docker exec -i amarket-clickhouse clickhouse-client --multiquery \
    <"${project_root}/infra/clickhouse/apply_log_retention.sql"

echo "Amarket log maintenance timers and live ClickHouse log limits installed."
