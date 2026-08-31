"""Fixed V1 boundaries for core-sector and core-stock candidates."""

from __future__ import annotations

CAPITAL_SECTOR_TYPES = ("concept", "industry", "style")
CORE_SECTOR_TYPES = ("concept", "industry")

# V1 upper bounds.  These are caps after eligibility filtering, not quotas.
CORE_SECTOR_CANDIDATE_LIMITS = {
    "concept": 20,
    "industry": 15,
}

CORE_SECTOR_CANDIDATE_MAX_ROWS = sum(CORE_SECTOR_CANDIDATE_LIMITS.values())
