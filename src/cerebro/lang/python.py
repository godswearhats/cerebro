"""cerebro language plugin — Python."""

from __future__ import annotations

from cerebro.operators import SHARED_OPERATORS, OperatorDef


# ---------------------------------------------------------------------------
# Python-specific mutation operators
# ---------------------------------------------------------------------------

_PY_STANDARD: list[OperatorDef] = [
    # Boolean literals (Python uses capitalized True/False)
    OperatorDef("bool_True_to_False", "boolean", r"\bTrue\b", "False", "True → False"),
    OperatorDef("bool_False_to_True", "boolean", r"\bFalse\b", "True", "False → True"),
    # Keyword logical
    OperatorDef("log_and_to_or", "logical", r"\band\b", "or", "and → or"),
    OperatorDef("log_or_to_and", "logical", r"\bor\b", "and", "or → and"),
    OperatorDef("unary_not_remove", "unary", r"\bnot\b ", "", "not x → x"),
    # Identity (order matters: match "is not" before "is")
    OperatorDef("identity_isnot_to_is", "identity", r"\bis\s+not\b", "is", "is not → is"),
    OperatorDef("identity_is_to_isnot", "identity", r"\bis\b(?!\s+not)", "is not", "is → is not"),
    # Membership (order matters: match "not in" before "in")
    OperatorDef("member_notin_to_in", "membership", r"\bnot\s+in\b", "in", "not in → in"),
    OperatorDef("member_in_to_notin", "membership", r"\bin\b(?!\s*\()", "not in", "in → not in"),
    # Void call removal (replace bare function call with pass)
    OperatorDef(
        "void_call_remove", "void_call",
        r"^\s*(?:[a-zA-Z_]\w*\.)*[a-zA-Z_]\w*\s*\([^)]*\)",
        "pass", "foo() → pass",
    ),
    # None return
    OperatorDef(
        "return_null", "return_value",
        r"return\s+(?!None\b).+",
        "return None", "return expr → return None",
    ),
]

# Filter shared STANDARD to exclude lowercase boolean operators (Python uses True/False)
_SHARED_STANDARD = [
    op for op in SHARED_OPERATORS["STANDARD"]
    if op.name not in ("bool_true_to_false", "bool_false_to_true")
]

_PY_STRONGER: list[OperatorDef] = [
    # Condition removal
    OperatorDef(
        "cond_to_true", "condition",
        r"if\s+(?!True\b).+:", "if True:",
        "if cond: → if True:",
    ),
    OperatorDef(
        "cond_to_false", "condition",
        r"if\s+(?!False\b).+:", "if False:",
        "if cond: → if False:",
    ),
    # String literal emptying
    OperatorDef(
        "string_empty_double", "string_literal",
        r'"[^"\\](?:[^"\\]|\\.)*"', '""',
        '"str" → ""',
    ),
    OperatorDef(
        "string_empty_single", "string_literal",
        r"'[^'\\](?:[^'\\]|\\.)*'", "''",
        "'str' → ''",
    ),
    # Statement removal (assignments — no ++/-- in Python)
    OperatorDef(
        "stmt_remove", "statement",
        r"^\s*(?:[a-zA-Z_]\w*\.)*[a-zA-Z_]\w*\s*(?:[+\-*/%&|^]|<<|>>|\*\*|//)?=(?!=).+",
        "pass", "statement → pass",
    ),
    # Return zero (alternative to return None in STANDARD)
    OperatorDef(
        "return_zero", "return_value",
        r"return\s+(?!0\b).+",
        "return 0", "return expr → return 0",
    ),
]

_PY_ALL: list[OperatorDef] = [
    OperatorDef("const_zero_to_one", "constant", r"\b0\b", "1", "0 → 1"),
    OperatorDef("const_one_to_zero", "constant", r"(?<!\.)(?<!\d)\b1\b(?!\.)", "0", "1 → 0"),
    # Power → multiply
    OperatorDef("arith_pow_to_mul", "arithmetic", r"\*\*(?!=)", "*", "** → *"),
    # Floor division → true division
    OperatorDef("arith_floordiv_to_div", "arithmetic", r"//(?!=)", "/", "// → /"),
]


def _build_presets() -> dict[str, list[OperatorDef]]:
    shared_stronger = SHARED_OPERATORS["STRONGER_EXTRA"]

    standard = _SHARED_STANDARD + _PY_STANDARD
    stronger = standard + shared_stronger + _PY_STRONGER
    all_ops = stronger + _PY_ALL
    return {"STANDARD": standard, "STRONGER": stronger, "ALL": all_ops}


# ---------------------------------------------------------------------------
# Python code masking
# ---------------------------------------------------------------------------


def compute_code_masks_python(lines: list[str]) -> tuple[list[list[bool]], list[bool]]:
    """Return per-character masks (True = mutable code) and per-line skip flags.

    Handles:
    - # line comments
    - "..." and '...' string literals with \\ escapes
    - Triple-quoted strings (multi-line)
    - @decorator lines (skipped)
    """
    masks: list[list[bool]] = []
    skip_line: list[bool] = []
    in_triple: str | None = None  # None, or the quote char ('"' or "'")

    for line in lines:
        mask = [True] * len(line)
        stripped = line.lstrip()

        # Inside a multi-line triple-quoted string from a previous line
        if in_triple is not None:
            _mask_triple_continuation(line, mask, in_triple)
            # Check if the triple quote closes on this line
            close = in_triple * 3
            # Find closing triple-quote (skip escaped quotes)
            pos = 0
            closed = False
            while pos < len(line):
                if line[pos] == "\\" :
                    pos += 2
                    continue
                if line[pos:pos + 3] == close:
                    in_triple = None
                    closed = True
                    break
                pos += 1
            skip_line.append(True)
            masks.append(mask)
            continue

        # Skip decorator lines
        is_decorator = stripped.startswith("@")
        skip_line.append(is_decorator)
        if is_decorator:
            masks.append([False] * len(line))
            continue

        i = 0
        while i < len(line):
            ch = line[i]

            # Line comment
            if ch == "#":
                for j in range(i, len(line)):
                    mask[j] = False
                break

            # String literals
            if ch in ('"', "'"):
                # Check for triple quote
                if i + 2 < len(line) and line[i + 1] == ch and line[i + 2] == ch:
                    quote_char = ch
                    close = ch * 3
                    # Mark the opening triple quote
                    mask[i] = False
                    mask[i + 1] = False
                    mask[i + 2] = False
                    i += 3
                    # Scan for closing triple quote on same line
                    found_close = False
                    while i < len(line):
                        if line[i] == "\\":
                            mask[i] = False
                            i += 1
                            if i < len(line):
                                mask[i] = False
                                i += 1
                            continue
                        if line[i:i + 3] == close:
                            mask[i] = False
                            mask[i + 1] = False
                            mask[i + 2] = False
                            i += 3
                            found_close = True
                            break
                        mask[i] = False
                        i += 1
                    if not found_close:
                        # Multi-line triple string — mask rest of line, track state
                        in_triple = quote_char
                    continue

                # Regular single/double-quoted string
                end_ch = ch
                mask[i] = False
                i += 1
                while i < len(line):
                    if line[i] == "\\":
                        mask[i] = False
                        i += 1
                        if i < len(line):
                            mask[i] = False
                            i += 1
                        continue
                    if line[i] == end_ch:
                        mask[i] = False
                        i += 1
                        break
                    mask[i] = False
                    i += 1
                continue

            i += 1

        masks.append(mask)

    return masks, skip_line


def _mask_triple_continuation(line: str, mask: list[bool], quote_char: str) -> None:
    """Mark all characters in a triple-quote continuation line as non-mutable."""
    for j in range(len(line)):
        mask[j] = False


# ---------------------------------------------------------------------------
# Language definition
# ---------------------------------------------------------------------------

LANGUAGE = {
    "name": "python",
    "source_globs": ["*.py"],
    "build_required": False,
    "compute_code_masks": compute_code_masks_python,
    "operators": _build_presets(),
}
