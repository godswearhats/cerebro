"""Core data types and outcome constants."""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

KILLED = "killed"
SURVIVED = "survived"
BUILD_ERROR = "build_error"
TIMEOUT = "timeout"
SKIPPED = "skipped"

# ---------------------------------------------------------------------------
# Mutation data structures
# ---------------------------------------------------------------------------


@dataclass
class Mutation:
    file: str
    line: int  # 1-based
    col: int  # 0-based
    end_col: int  # 0-based, exclusive
    original: str
    replacement: str
    operator: str
    description: str


@dataclass
class MutationResult:
    mutation: Mutation
    status: str  # KILLED, SURVIVED, BUILD_ERROR, TIMEOUT, SKIPPED
    duration_seconds: float = 0.0
    detail: str = ""
