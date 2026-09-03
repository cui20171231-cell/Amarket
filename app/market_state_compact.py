"""Compose the formal AI-callable Compact V3 market-state package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.simulate_compact_market_state_package import (
    build_compact,
    build_key_core_trajectory,
)


def build_market_state_compact_v3(
    full_package: dict[str, Any], builder: Any
) -> dict[str, Any]:
    trajectory = build_key_core_trajectory(builder, full_package)
    return build_compact(
        full_package,
        Path("PERSISTED_FULL_PACKAGE"),
        core_trajectory=trajectory,
    )

