"""Mutation operator definitions and shared (language-agnostic) operator sets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatorDef:
    """A single mutation operator."""

    name: str
    category: str
    pattern: str
    replacement: str
    description: str


# ---------------------------------------------------------------------------
# Shared mutation operators (language-agnostic)
#
# These are used by language plugins to compose their preset tiers.
# Plugins import SHARED_OPERATORS and combine with language-specific extras.
# ---------------------------------------------------------------------------

SHARED_OPERATORS: dict[str, list[OperatorDef]] = {
    "STANDARD": [
        # --- Relational ---
        OperatorDef("rel_lt_to_lte", "relational", r"(?<![<\-])< (?![<=<])", "<=", "< → <="),
        OperatorDef("rel_lte_to_lt", "relational", r"(?<![<])<=", "<", "<= → <"),
        OperatorDef("rel_gt_to_gte", "relational", r"(?<![>\-=])>(?![>=])", ">=", "> → >="),
        OperatorDef("rel_gte_to_gt", "relational", r"(?<![>])>=", ">", ">= → >"),
        OperatorDef("rel_eq_to_neq", "relational", r"(?<![!<>=])== ", "!=", "== → !="),
        OperatorDef("rel_neq_to_eq", "relational", r"(?<![!])!=(?!=)", "==", "!= → =="),
        # --- Arithmetic ---
        OperatorDef("arith_add_to_sub", "arithmetic", r"(?<![+]) *\+(?![+=])", "-", "+ → -"),
        OperatorDef("arith_sub_to_add", "arithmetic", r"(?<![->]) *\-(?![->=-])", "+", "- → +"),
        OperatorDef("arith_mul_to_div", "arithmetic", r"(?<![/]) *\*(?![*=])", "/", "* → /"),
        OperatorDef("arith_div_to_mul", "arithmetic", r"(?<![*/]) */(?![/=*])", "*", "/ → *"),
        # --- Boolean literal ---
        OperatorDef("bool_true_to_false", "boolean", r"\btrue\b", "false", "true → false"),
        OperatorDef("bool_false_to_true", "boolean", r"\bfalse\b", "true", "false → true"),
        # --- Invert negatives ---
        OperatorDef("unary_minus_remove", "unary", r"(?<![a-zA-Z_0-9)\]])-(?=[a-zA-Z_\d(])", "", "-x → x"),
    ],
    "STRONGER_EXTRA": [
        # --- Assignment operators ---
        OperatorDef("assign_add_to_sub", "assignment", r"\+=", "-=", "+= → -="),
        OperatorDef("assign_sub_to_add", "assignment", r"(?<![->])-=", "+=", "-= → +="),
        OperatorDef("assign_mul_to_div", "assignment", r"\*=", "/=", "*= → /="),
        OperatorDef("assign_div_to_mul", "assignment", r"/=", "*=", "/= → *="),
    ],
}
