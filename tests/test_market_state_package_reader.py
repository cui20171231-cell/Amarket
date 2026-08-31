from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.hithink.schedule import build_daily_schedule
from app.market_state_package_reader import compact_package_for_ai, get_market_state_package

TRADE_DATE = date(2026, 8, 28)


def test_ai_transport_omits_only_the_all_concept_trajectory_matrix() -> None:
    original = {
        "status": "OK",
        "data": {
            "market": {"current_state": {"up_count": 1}},
            "core_sectors": {"concept": [{"trajectory_15m_last_5": [1, 2]}]},
            "core_sector_intraday": {
                "concept_universe": [{"sector_code": "A"}],
                "concept_trajectories": [{"sector_code": "A", "points": [1, 2]}],
            },
        },
    }

    compact = compact_package_for_ai(original)

    assert compact["data"]["market"] == original["data"]["market"]
    assert compact["data"]["core_sectors"] == original["data"]["core_sectors"]
    assert compact["data"]["core_sector_intraday"]["concept_universe"] == [
        {"sector_code": "A"}
    ]
    assert "concept_trajectories" not in compact["data"]["core_sector_intraday"]
    assert compact["data"]["core_sector_intraday"]["concept_trajectory_count"] == 1
    assert original["data"]["core_sector_intraday"]["concept_trajectories"]


class FakeClock:
    def __init__(self, on_sleep=None) -> None:
        self.current = 0.0
        self.on_sleep = on_sleep

    def __call__(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds
        if self.on_sleep is not None:
            self.on_sleep(self.current)


def test_reader_has_no_clickhouse_or_file_mutation_dependency() -> None:
    source = Path("app/market_state_package_reader.py").read_text(encoding="utf-8")
    for forbidden in (
        "ClickHouseWriter",
        "MarketStatePackageBuilder",
        ".write_text(",
        ".unlink(",
        ".rename(",
    ):
        assert forbidden not in source


def _write_package(
    root: Path,
    sequence_no: int,
    *,
    trade_date: date = TRADE_DATE,
    package_id: str | None = None,
    internal_node_seq: int | None = None,
    raw_text: str | None = None,
) -> Path:
    node = build_daily_schedule(trade_date)[sequence_no - 1]
    directory = root / trade_date.strftime("%Y%m%d")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{node.collection_id}.json"
    if raw_text is not None:
        path.write_text(raw_text, encoding="utf-8")
        return path
    data = {
        "package_id": package_id or node.collection_id,
        "target": {
            "trade_date": trade_date.isoformat(),
            "resolved_collection_id": node.collection_id,
            "resolved_node_seq": internal_node_seq
            if internal_node_seq is not None
            else node.sequence_no,
            "resolved_scheduled_time": node.scheduled_time.isoformat(),
        },
        "payload": {"preserved": [1, None, {"text": "原样返回"}]},
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_reads_complete_package_by_minute_time_and_preserves_data(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 57)

    result = get_market_state_package(
        target_time="10:15",
        trade_date="20260828",
        package_root=root,
    )

    assert result["status"] == "OK"
    assert result["actual_time"] == "10:15:15"
    assert result["time_offset_seconds"] == 15
    assert result["node_seq"] == 57
    assert result["collection_id"] == "20260828057"
    assert result["data"]["payload"] == {"preserved": [1, None, {"text": "原样返回"}]}


def test_reads_by_node_and_finds_latest_valid_directory(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 178, trade_date=date(2026, 8, 27))
    _write_package(root, 178)

    result = get_market_state_package(node_seq=178, package_root=root)

    assert result["status"] == "OK"
    assert result["trade_date"] == "2026-08-28"
    assert result["requested_time"] is None
    assert result["requested_node_seq"] == 178
    assert result["node_seq"] == 178


def test_explicit_hyphenated_date_and_consistent_time_and_node(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 254)

    result = get_market_state_package(
        target_time="15:00",
        node_seq=254,
        trade_date="2026-08-28",
        package_root=root,
    )

    assert result["status"] == "OK"
    assert result["actual_time"] == "15:00:00"
    assert result["time_offset_seconds"] == 0


def test_missing_time_returns_nearest_nodes_without_substitution(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 57)
    _write_package(root, 72)

    result = get_market_state_package(
        target_time="10:20",
        trade_date="2026-08-28",
        package_root=root,
    )

    assert result["status"] == "NOT_FOUND"
    assert result["nearest_before"]["node_seq"] == 57
    assert result["nearest_after"]["node_seq"] == 72
    assert [item["node_seq"] for item in result["available_nodes"]] == [57, 72]
    assert "data" not in result


@pytest.mark.parametrize(
    ("arguments", "error_part"),
    [
        ({}, "至少提供一个"),
        ({"node_seq": 0}, "1到254"),
        ({"node_seq": 300}, "1到254"),
        ({"target_time": "abc"}, "HH:MM"),
        ({"target_time": r"..\..\x"}, "HH:MM"),
        ({"target_time": "10:15", "trade_date": "2026-99-99"}, "有效日期"),
    ],
)
def test_invalid_requests_are_rejected(
    tmp_path: Path, arguments: dict[str, object], error_part: str
) -> None:
    result = get_market_state_package(package_root=tmp_path / "packages", **arguments)

    assert result["status"] == "INVALID_REQUEST"
    assert error_part in result["error"]


def test_time_and_node_must_identify_the_same_package(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 57)
    _write_package(root, 178)

    result = get_market_state_package(
        target_time="10:15",
        node_seq=178,
        trade_date="2026-08-28",
        package_root=root,
    )

    assert result["status"] == "INVALID_REQUEST"


def test_filename_and_internal_node_mismatch_is_invalid(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 178, internal_node_seq=177)

    result = get_market_state_package(
        node_seq=178,
        trade_date="2026-08-28",
        package_root=root,
    )

    assert result["status"] == "INVALID_PACKAGE"
    assert "node_seq" in result["error"]
    assert "data" not in result


@pytest.mark.parametrize("raw_text", ["{broken", "", "{}"])
def test_damaged_empty_or_empty_object_json_is_invalid(tmp_path: Path, raw_text: str) -> None:
    root = tmp_path / "packages"
    _write_package(root, 254, raw_text=raw_text)

    result = get_market_state_package(
        node_seq=254,
        trade_date="20260828",
        package_root=root,
    )

    assert result["status"] == "INVALID_PACKAGE"
    assert "data" not in result


def test_os_read_failure_returns_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "packages"
    _write_package(root, 254)

    def fail_read_text(self, *args, **kwargs):
        raise OSError("simulated read failure")

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    result = get_market_state_package(
        node_seq=254,
        trade_date="20260828",
        package_root=root,
    )

    assert result["status"] == "READ_ERROR"
    assert "simulated read failure" in result["error"]


def test_wait_existing_package_returns_immediately(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 72)
    clock = FakeClock()

    result = get_market_state_package(
        target_time="10:30",
        trade_date="2026-08-28",
        wait_for_ready=True,
        package_root=root,
        _clock=clock,
        _sleep=clock.sleep,
    )

    assert result["status"] == "OK"
    assert result["retry_count"] == 0
    assert result["wait_elapsed_ms"] == 0


@pytest.mark.parametrize(("ready_after", "expected_retries"), [(10, 1), (30, 3)])
def test_wait_returns_when_package_is_generated_later(
    tmp_path: Path, ready_after: int, expected_retries: int
) -> None:
    root = tmp_path / "packages"
    generated = False

    def generate_when_ready(elapsed: float) -> None:
        nonlocal generated
        if elapsed >= ready_after and not generated:
            _write_package(root, 72)
            generated = True

    clock = FakeClock(generate_when_ready)
    result = get_market_state_package(
        target_time="10:30",
        trade_date="2026-08-28",
        wait_for_ready=True,
        retry_interval_seconds=10,
        max_wait_seconds=120,
        package_root=root,
        _clock=clock,
        _sleep=clock.sleep,
    )

    assert result["status"] == "OK"
    assert result["retry_count"] == expected_retries
    assert result["wait_elapsed_ms"] == ready_after * 1000


def test_regular_node_times_out_without_using_previous_package(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 57)
    clock = FakeClock()

    result = get_market_state_package(
        target_time="10:30",
        trade_date="2026-08-28",
        wait_for_ready=True,
        retry_interval_seconds=10,
        max_wait_seconds=120,
        package_root=root,
        _clock=clock,
        _sleep=clock.sleep,
    )

    assert result["status"] == "TIMEOUT"
    assert result["retry_count"] == 12
    assert result["wait_elapsed_ms"] == 120_000
    assert result["collection_id"] == "20260828072"
    assert "data" not in result


def test_closing_node_allows_five_minute_wait(tmp_path: Path) -> None:
    clock = FakeClock()

    result = get_market_state_package(
        node_seq=254,
        trade_date="2026-08-28",
        wait_for_ready=True,
        retry_interval_seconds=10,
        max_wait_seconds=300,
        package_root=tmp_path / "packages",
        _clock=clock,
        _sleep=clock.sleep,
    )

    assert result["status"] == "TIMEOUT"
    assert result["retry_count"] == 30
    assert result["wait_elapsed_ms"] == 300_000


@pytest.mark.parametrize("initial_text", ["", "{incomplete"])
def test_wait_retries_empty_or_incomplete_json_until_valid(
    tmp_path: Path, initial_text: str
) -> None:
    root = tmp_path / "packages"
    _write_package(root, 72, raw_text=initial_text)
    completed = False

    def complete_after_ten_seconds(elapsed: float) -> None:
        nonlocal completed
        if elapsed >= 10 and not completed:
            _write_package(root, 72)
            completed = True

    clock = FakeClock(complete_after_ten_seconds)
    result = get_market_state_package(
        node_seq=72,
        trade_date="2026-08-28",
        wait_for_ready=True,
        retry_interval_seconds=10,
        max_wait_seconds=20,
        package_root=root,
        _clock=clock,
        _sleep=clock.sleep,
    )

    assert result["status"] == "OK"
    assert result["retry_count"] == 1
    assert result["wait_elapsed_ms"] == 10_000


def test_wait_returns_invalid_package_when_file_stays_damaged(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    _write_package(root, 72, raw_text="{damaged")
    clock = FakeClock()

    result = get_market_state_package(
        node_seq=72,
        trade_date="2026-08-28",
        wait_for_ready=True,
        retry_interval_seconds=10,
        max_wait_seconds=20,
        package_root=root,
        _clock=clock,
        _sleep=clock.sleep,
    )

    assert result["status"] == "INVALID_PACKAGE"
    assert result["retry_count"] == 2
    assert result["wait_elapsed_ms"] == 20_000
    assert "last_validation_error" in result


def test_tmp_file_is_ignored_until_timeout(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    path = _write_package(root, 72)
    temporary = path.with_suffix(".json.tmp")
    path.replace(temporary)
    clock = FakeClock()

    result = get_market_state_package(
        node_seq=72,
        trade_date="2026-08-28",
        wait_for_ready=True,
        retry_interval_seconds=10,
        max_wait_seconds=20,
        package_root=root,
        _clock=clock,
        _sleep=clock.sleep,
    )

    assert result["status"] == "TIMEOUT"
    assert result["retry_count"] == 2
    assert "data" not in result


@pytest.mark.parametrize(
    "arguments",
    [
        {"wait_for_ready": "yes"},
        {"retry_interval_seconds": 4},
        {"retry_interval_seconds": 31},
        {"max_wait_seconds": 0},
        {"max_wait_seconds": 301},
    ],
)
def test_wait_parameter_limits_are_enforced(tmp_path: Path, arguments: dict[str, object]) -> None:
    result = get_market_state_package(
        node_seq=72,
        trade_date="2026-08-28",
        package_root=tmp_path / "packages",
        **arguments,
    )

    assert result["status"] == "INVALID_REQUEST"
