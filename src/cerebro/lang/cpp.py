"""cerebro language plugin — C/C++."""

from __future__ import annotations

from cerebro.operators import SHARED_OPERATORS, OperatorDef


# ---------------------------------------------------------------------------
# C++-specific mutation operators
# ---------------------------------------------------------------------------

_CPP_STANDARD: list[OperatorDef] = [
    # Logical (C-style)
    OperatorDef("log_and_to_or", "logical", r"&&", "||", "&& → ||"),
    OperatorDef("log_or_to_and", "logical", r"\|\|", "&&", "|| → &&"),
    # Unary
    OperatorDef("unary_neg_remove", "unary", r"!(?![!=])", "", "!x → x"),
    # Increment / Decrement
    OperatorDef("inc_to_dec", "increment", r"\+\+", "--", "++ → --"),
    OperatorDef("dec_to_inc", "increment", r"(?<!-)--(?!>)", "++", "-- → ++"),
    # Void call removal
    OperatorDef(
        "void_call_remove", "void_call",
        r"^\s*(?:[a-zA-Z_]\w*(?:::|->|\.))*[a-zA-Z_]\w*\s*\([^)]*\)\s*;",
        ";", "foo(); → ;",
    ),
    # Null return (C++ uses nullptr)
    OperatorDef(
        "return_nullptr", "return_value",
        r"return\s+(?!nullptr\b).+;", "return nullptr;",
        "return expr → return nullptr",
    ),
]

_CPP_STRONGER: list[OperatorDef] = [
    # Return value
    OperatorDef(
        "return_zero",
        "return_value",
        r"return\s+(?!0\s*;).+;",
        "return 0;",
        "return expr → return 0",
    ),
    # Condition removal
    OperatorDef(
        "cond_to_true",
        "condition",
        r"if\s*\((?!true\)).+?\)",
        "if (true)",
        "if(cond) → if(true)",
    ),
    OperatorDef(
        "cond_to_false",
        "condition",
        r"if\s*\((?!false\)).+?\)",
        "if (false)",
        "if(cond) → if(false)",
    ),
    # Statement removal (assignments, increments — not bare calls or control flow)
    OperatorDef(
        "stmt_remove", "statement",
        r"^\s*(?:[a-zA-Z_]\w*(?:\.|->|::))*[a-zA-Z_]\w*\s*(?:\+\+|--|(?:[+\-*/%&|^]|<<|>>)?=.+)\s*;",
        ";", "statement → ;",
    ),
    # Switch: break removal (creates fallthrough)
    OperatorDef("switch_break_remove", "switch", r"^\s*break\s*;", ";", "break; → ; (fallthrough)"),
    # Switch: case value mutation
    OperatorDef("switch_case_zero", "switch", r"case\s+(?!0\s*:)\w+\s*:", "case 0:", "case X: → case 0:"),
]

_CPP_ALL: list[OperatorDef] = [
    OperatorDef("const_zero_to_one", "constant", r"\b0\b", "1", "0 → 1"),
    OperatorDef("const_one_to_zero", "constant", r"(?<!\.)(?<!\d)\b1\b(?!\.)", "0", "1 → 0"),
]


def _build_presets() -> dict[str, list[OperatorDef]]:
    shared = SHARED_OPERATORS["STANDARD"]
    shared_stronger = SHARED_OPERATORS["STRONGER_EXTRA"]

    standard = shared + _CPP_STANDARD
    stronger = standard + shared_stronger + _CPP_STRONGER
    all_ops = stronger + _CPP_ALL
    return {"STANDARD": standard, "STRONGER": stronger, "ALL": all_ops}


# ---------------------------------------------------------------------------
# C/C++ code masking
# ---------------------------------------------------------------------------


def compute_code_masks_cpp(lines: list[str]) -> tuple[list[list[bool]], list[bool]]:
    """Return per-character masks (True = mutable code) and per-line skip flags.

    Handles: block comments, line comments, string literals, char literals,
    preprocessor directives.
    """
    masks: list[list[bool]] = []
    skip_line: list[bool] = []
    in_block_comment = False

    for line in lines:
        mask = [True] * len(line)
        stripped = line.lstrip()
        is_pp = stripped.startswith("#")
        skip_line.append(is_pp)

        if is_pp and not in_block_comment:
            masks.append([False] * len(line))
            continue

        i = 0
        while i < len(line):
            if in_block_comment:
                mask[i] = False
                if i + 1 < len(line) and line[i] == "*" and line[i + 1] == "/":
                    mask[i + 1] = False
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            ch = line[i]

            # Block comment start
            if ch == "/" and i + 1 < len(line) and line[i + 1] == "*":
                mask[i] = False
                mask[i + 1] = False
                in_block_comment = True
                i += 2
                continue

            # Line comment
            if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                for j in range(i, len(line)):
                    mask[j] = False
                break

            # String literal
            if ch == '"':
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

            # Char literal
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
    "name": "cpp",
    "source_globs": ["*.cpp", "*.cc", "*.cxx", "*.c"],
    "build_required": True,
    "compute_code_masks": compute_code_masks_cpp,
    "operators": _build_presets(),
}
