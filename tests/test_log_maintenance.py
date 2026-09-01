from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.logging_utils import append_jsonl_bounded, bounded_log_handler
from scripts import log_maintenance


def test_bounded_log_handler_rotates(tmp_path: Path) -> None:
    path = tmp_path / "collector.log"
    logger = logging.getLogger(f"test-{id(path)}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = bounded_log_handler(path, max_bytes=80, backup_count=2)
    logger.addHandler(handler)
    try:
        for index in range(20):
            logger.info("record-%s-xxxxxxxxxxxxxxxx", index)
    finally:
        handler.close()
        logger.handlers.clear()

    assert path.exists()
    assert path.with_name("collector.log.1").exists()
    assert not path.with_name("collector.log.3").exists()


def test_jsonl_audit_rotation_keeps_valid_records(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    for index in range(20):
        append_jsonl_bounded(
            path,
            {"index": index, "message": "x" * 30},
            max_bytes=100,
            backup_count=3,
        )

    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["index"] == 19
    assert path.with_name("audit.jsonl.1").exists()
    assert not path.with_name("audit.jsonl.4").exists()


def test_expired_cleanup_only_touches_rotated_whitelist(
    tmp_path: Path, monkeypatch
) -> None:
    active = tmp_path / "collector.log"
    active.write_text("active", encoding="utf-8")
    expired = tmp_path / "collector.log.1"
    expired.write_text("old", encoding="utf-8")
    unrelated = tmp_path / "market_state.json"
    unrelated.write_text("business", encoding="utf-8")
    old_time = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    os.utime(expired, (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))

    monkeypatch.setattr(
        log_maintenance,
        "LOG_FAMILIES",
        (log_maintenance.LogFamily(active, 30, 100, 2),),
    )
    monkeypatch.setattr(log_maintenance, "BOUNDED_RUNTIME_LOGS", ())
    monkeypatch.setattr(log_maintenance, "ROOT", tmp_path)

    deleted = log_maintenance._prune_expired(datetime.now(UTC))

    assert "collector.log.1" in deleted
    assert not expired.exists()
    assert active.exists()
    assert unrelated.exists()
