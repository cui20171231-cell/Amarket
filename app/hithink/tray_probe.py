from __future__ import annotations

import json

from app.hithink.status import check_status


def main() -> None:
    try:
        report = check_status()
        snapshot = next(
            row
            for row in report["details"]
            if row.get("group") == "盘中快照" and row.get("item") == "行情快照"
        )
        expected = int(snapshot["expected"])
        collected = int(snapshot["success"])

        print(
            json.dumps(
                {
                    "overall_health": "Normal",
                    "tooltip": f"个股快照行情：应采集{expected}，已采集{collected}",
                    "snapshot": {
                        "expected": expected,
                        "collected": collected,
                    },
                    "clickhouse": {
                        "normal": True,
                        "text": "ClickHouse：正常",
                    },
                },
                ensure_ascii=True,
            )
        )
    except Exception as exc:  # noqa: BLE001 - tray must always receive one JSON record.
        print(
            json.dumps(
                {
                    "overall_health": "Unknown",
                    "tooltip": f"Amarket状态读取失败：{type(exc).__name__}",
                    "error": f"{type(exc).__name__}: {exc}"[-1000:],
                    "clickhouse": {"normal": False, "text": "ClickHouse：状态未知"},
                },
                ensure_ascii=True,
            )
        )


if __name__ == "__main__":
    main()
