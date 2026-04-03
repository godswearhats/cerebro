"""cerebro language plugin — GDScript."""

from __future__ import annotations

from cerebro import SHARED_OPERATORS, OperatorDef


# ---------------------------------------------------------------------------
# GDScript-specific mutation operators
# ---------------------------------------------------------------------------

_GDS_STANDARD: list[OperatorDef] = [
    # Logical — C-style (preferred in this project per AGENTS.md)
    OperatorDef("log_and_to_or", "logical", r"&&", "||", "&& → ||"),
    OperatorDef("log_or_to_and", "logical", r"\|\|", "&&", "|| → &&"),
    OperatorDef("unary_neg_remove", "unary", r"!(?![!=])", "", "!x → x"),
    # Logical — keyword-style (GDScript also supports these)
    OperatorDef("log_and_word_to_or", "logical", r"\band\b", "or", "and → or"),
    OperatorDef("log_or_word_to_and", "logical", r"\bor\b", "and", "or → and"),
    OperatorDef("unary_not_word_remove", "unary", r"\bnot\b ", "", "not x → x"),
    # Void call removal (GDScript: replace with pass)
    OperatorDef(
        "void_call_remove", "void_call",
        r"^\s*(?:[a-zA-Z_]\w*\.)*[a-zA-Z_]\w*\s*\([^)]*\)",
        "pass", "foo() → pass",
    ),
    # Null return (promoted from STRONGER to align with PITest default tier)
    OperatorDef(
        "return_null", "return_value",
        r"return\s+(?!null\b).+",
        "return null", "return expr → return null",
    ),
]

_GDS_STRONGER: list[OperatorDef] = [
    # Condition removal — GDScript uses : not ) at end
    OperatorDef(
        "cond_to_true",
        "condition",
        r"if\s+(?!true\b).+:",
        "if true:",
        "if cond: → if true:",
    ),
    OperatorDef(
        "cond_to_false",
        "condition",
        r"if\s+(?!false\b).+:",
        "if false:",
        "if cond: → if false:",
    ),
    # Statement removal (assignments — no ++/-- in GDScript)
    OperatorDef(
        "stmt_remove", "statement",
        r"^\s*(?:[a-zA-Z_]\w*\.)*[a-zA-Z_]\w*\s*(?:[+\-*/%]?=).+",
        "pass", "statement → pass",
    ),
]

_GDS_ALL: list[OperatorDef] = [
    OperatorDef("const_zero_to_one", "constant", r"\b0\b", "1", "0 → 1"),
    OperatorDef("const_one_to_zero", "constant", r"(?<!\.)(?<!\d)\b1\b(?!\.)", "0", "1 → 0"),
    # Match: pattern → wildcard (GDScript match/when)
    OperatorDef("match_pattern_wildcard", "match", r"^\s*(?!_:)\S+\s*:", "_:", "pattern: → _: (wildcard)"),
]


def _build_presets() -> dict[str, list[OperatorDef]]:
    shared = SHARED_OPERATORS["STANDARD"]
    shared_stronger = SHARED_OPERATORS["STRONGER_EXTRA"]

    standard = shared + _GDS_STANDARD
    stronger = standard + shared_stronger + _GDS_STRONGER
    all_ops = stronger + _GDS_ALL
    return {"STANDARD": standard, "STRONGER": stronger, "ALL": all_ops}


# ---------------------------------------------------------------------------
# GDScript code masking
# ---------------------------------------------------------------------------


def compute_code_masks_gdscript(lines: list[str]) -> tuple[list[list[bool]], list[bool]]:
    """Return per-character masks (True = mutable code) and per-line skip flags.

    GDScript has:
    - # line comments (no block comments)
    - "..." string literals with \\ escapes
    - @annotations (we skip annotation lines as non-mutable)
    """
    masks: list[list[bool]] = []
    skip_line: list[bool] = []

    for line in lines:
        mask = [True] * len(line)
        stripped = line.lstrip()

        # Skip annotation lines (@export, @onready, etc.)
        is_annotation = stripped.startswith("@")
        skip_line.append(is_annotation)
        if is_annotation:
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

            # String literal (double-quoted)
            if ch == '"':
                # Check for triple-quote (multi-line string start/end)
                if i + 2 < len(line) and line[i + 1] == '"' and line[i + 2] == '"':
                    # Mark rest of line as non-mutable (multi-line strings are rare
                    # and complex; conservatively skip)
                    for j in range(i, len(line)):
                        mask[j] = False
                    break

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
                    if line[i] == '"':
                        mask[i] = False
                        i += 1
                        break
                    mask[i] = False
                    i += 1
                continue

            # Single-quoted string (GDScript supports these too)
            if ch == "'":
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
                    if line[i] == "'":
                        mask[i] = False
                        i += 1
                        break
                    mask[i] = False
                    i += 1
                continue

            i += 1

        masks.append(mask)

    return masks, skip_line


# ---------------------------------------------------------------------------
# Language definition
# ---------------------------------------------------------------------------

LANGUAGE = {
    "name": "gdscript",
    "source_globs": ["*.gd"],
    "build_required": False,
    "compute_code_masks": compute_code_masks_gdscript,
    "operators": _build_presets(),
}
