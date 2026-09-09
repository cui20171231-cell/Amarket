from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = PROJECT_ROOT / "secrets" / "collector.env"


def _read_key_file() -> dict[str, str]:
    if not KEY_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Settings:
    api_key: str
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_database: str
    clickhouse_username: str
    clickhouse_password: str

    @classmethod
    def load(cls, *, require_api_key: bool = True) -> Settings:
        file_values = _read_key_file()
        api_key = os.environ.get("HITHINK_FINANCE_API_KEY") or file_values.get(
            "HITHINK_FINANCE_API_KEY"
        )
        if require_api_key and not api_key:
            raise RuntimeError(f"HITHINK_FINANCE_API_KEY is missing; expected {KEY_FILE}")
        return cls(
            api_key=api_key or "",
            clickhouse_host=os.environ.get("CLICKHOUSE_HOST")
            or file_values.get("CLICKHOUSE_HOST", "127.0.0.1"),
            clickhouse_port=int(
                os.environ.get("CLICKHOUSE_PORT")
                or file_values.get("CLICKHOUSE_PORT", "8123")
            ),
            clickhouse_database=os.environ.get("CLICKHOUSE_DATABASE")
            or file_values.get("CLICKHOUSE_DATABASE", "market"),
            clickhouse_username=os.environ.get("CLICKHOUSE_USERNAME")
            or file_values.get("CLICKHOUSE_USERNAME", "hithink_snapshot"),
            clickhouse_password=os.environ.get("CLICKHOUSE_PASSWORD")
            or file_values.get("CLICKHOUSE_PASSWORD", ""),
        )
