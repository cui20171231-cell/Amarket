#!/usr/bin/env bash
set -u

container_name="amarket-clickhouse"
heartbeat_path="/mnt/d/Amarket/.runtime/linux_infrastructure_heartbeat.json"

while true; do
    if ! systemctl is-active --quiet docker; then
        systemctl start docker || true
    fi

    if docker inspect "${container_name}" >/dev/null 2>&1; then
        running="$(docker inspect --format '{{.State.Running}}' "${container_name}" 2>/dev/null || echo false)"
        health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_name}" 2>/dev/null || echo unknown)"

        if [[ "${running}" != "true" ]]; then
            docker start "${container_name}" >/dev/null 2>&1 || true
        elif [[ "${health}" == "unhealthy" ]]; then
            docker restart "${container_name}" >/dev/null 2>&1 || true
        fi

        mkdir -p "$(dirname "${heartbeat_path}")"
        printf '{"checked_at":"%s","container":"%s","running":%s,"health":"%s"}\n' \
            "$(date --iso-8601=seconds)" "${container_name}" "${running}" "${health}" \
            >"${heartbeat_path}"
    fi

    sleep 30
done
