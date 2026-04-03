"""cerebro — Multi-language source-level mutation testing.

A reusable, zero-dependency mutation testing tool that patches source files,
optionally recompiles, runs tests, and reports which mutations survived.
Language support is provided by plugins in cerebro.lang.

Usage:
    cerebro run [--config cerebro.json] [--workers N] [--file path]
    cerebro dry-run [--config cerebro.json]
    cerebro list-operators [--preset STANDARD]
    cerebro report [--config cerebro.json] [--format text|json|html]
"""

# Public API — importable as `from cerebro import X`
from cerebro.operators import OperatorDef, SHARED_OPERATORS  # noqa: F401
from cerebro.types import (  # noqa: F401
    Mutation,
    MutationResult,
    KILLED,
    SURVIVED,
    BUILD_ERROR,
    TIMEOUT,
    SKIPPED,
)
from cerebro.mutation import generate_mutations, apply_mutation  # noqa: F401
from cerebro.config import (  # noqa: F401
    Config,
    builtin_cpp_code_masks,
    load_language,
    discover_sources,
    OPERATOR_PRESETS,
)
from cerebro.report import compute_score  # noqa: F401
from cerebro.cli import main  # noqa: F401
