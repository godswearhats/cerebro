"""cerebro language plugin — TypeScript / JavaScript."""

from __future__ import annotations

from cerebro.operators import SHARED_OPERATORS, OperatorDef


# ---------------------------------------------------------------------------
# TypeScript-specific mutation operators
# ---------------------------------------------------------------------------

_TS_STANDARD: list[OperatorDef] = [
    # Logical (C-style, same as C++)
    OperatorDef("log_and_to_or", "logical", r"&&", "||", "&& → ||"),
    OperatorDef("log_or_to_and", "logical", r"\|\|", "&&", "|| → &&"),
    # Unary
    OperatorDef("unary_neg_remove", "unary", r"!(?![!=])", "", "!x → x"),
    # Increment / Decrement
    OperatorDef("inc_to_dec", "increment", r"\+\+", "--", "++ → --"),
    OperatorDef("dec_to_inc", "increment", r"(?<!-)--(?!>)", "++", "-- → ++"),
    # Strict equality (TypeScript-specific)
    OperatorDef("rel_strict_eq_to_neq", "relational", r"===", "!==", "=== → !=="),
    OperatorDef("rel_strict_neq_to_eq", "relational", r"!==", "===", "!== → ==="),
    # Nullish coalescing
    OperatorDef("nullish_to_or", "logical", r"\?\?", "||", "?? → ||"),
    # Optional chaining removal (a?.b → a.b)
    OperatorDef("optional_chain_remove", "optional_chaining", r"\?\.", ".", "?. → ."),
    # Arrow function body removal (() => expr → () => undefined)
    # Matches "=> expr" not followed by { (block body) or already undefined.
    # Anchored by ") =>" to avoid matching type annotations like ": () => string".
    OperatorDef(
        "arrow_to_undefined",
        "arrow_function",
        r"(?<=\))\s*=>\s+(?!undefined\b|\{).+",
        " => undefined",
        "=> expr → => undefined",
    ),
    # Void call removal (includes await)
    OperatorDef(
        "void_call_remove", "void_call",
        r"^\s*(?:await\s+)?(?:[a-zA-Z_$]\w*\.)*[a-zA-Z_$]\w*\s*\([^)]*\)\s*;",
        ";", "foo(); → ;",
    ),
    # Null return
    OperatorDef(
        "return_null", "return_value",
        r"return\s+(?!null\b).+;", "return null;",
        "return expr → return null",
    ),
]

_TS_STRONGER: list[OperatorDef] = [
    # Return value
    OperatorDef(
        "return_undefined",
        "return_value",
        r"return\s+(?!undefined\b).+;",
        "return undefined;",
        "return expr → return undefined",
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
    # String literal emptying ("foo" → "")
    OperatorDef(
        "string_empty_double",
        "string_literal",
        r'"[^"\\](?:[^"\\]|\\.)*"',
        '""',
        '"str" → ""',
    ),
    OperatorDef(
        "string_empty_single",
        "string_literal",
        r"'[^'\\](?:[^'\\]|\\.)*'",
        "''",
        "'str' → ''",
    ),
    # Statement removal (assignments, increments — not bare calls or control flow)
    OperatorDef(
        "stmt_remove", "statement",
        r"^\s*(?:[a-zA-Z_$]\w*\.)*[a-zA-Z_$]\w*\s*(?:\+\+|--|(?:[+\-*/%&|^]|<<|>>)?=.+)\s*;",
        ";", "statement → ;",
    ),
    # Switch: break removal (creates fallthrough)
    OperatorDef("switch_break_remove", "switch", r"^\s*break\s*;", ";", "break; → ; (fallthrough)"),
    # Switch: case value mutation
    OperatorDef("switch_case_zero", "switch", r"case\s+(?!0\s*:).+?\s*:", "case 0:", "case X: → case 0:"),
]

_TS_ALL: list[OperatorDef] = [
    OperatorDef("const_zero_to_one", "constant", r"\b0\b", "1", "0 → 1"),
    OperatorDef("const_one_to_zero", "constant", r"(?<!\.)(?<!\d)\b1\b(?!\.)", "0", "1 → 0"),
]


def _build_presets() -> dict[str, list[OperatorDef]]:
    shared = SHARED_OPERATORS["STANDARD"]
    shared_stronger = SHARED_OPERATORS["STRONGER_EXTRA"]

    standard = shared + _TS_STANDARD
    stronger = standard + shared_stronger + _TS_STRONGER
    all_ops = stronger + _TS_ALL
    return {"STANDARD": standard, "STRONGER": stronger, "ALL": all_ops}


# ---------------------------------------------------------------------------
# TypeScript / JavaScript code masking
# ---------------------------------------------------------------------------


def compute_code_masks_typescript(lines: list[str]) -> tuple[list[list[bool]], list[bool]]:
    """Return per-character masks (True = mutable code) and per-line skip flags.

    Handles: block comments, line comments, double-quoted strings,
    single-quoted strings, template literals (backtick), decorators.
    """
    masks: list[list[bool]] = []
    skip_line: list[bool] = []
    in_block_comment = False
    in_template_literal = False

    for line in lines:
        mask = [True] * len(line)
        stripped = line.lstrip()

        # Skip decorator lines (@Injectable, etc.)
        is_decorator = stripped.startswith("@") and not in_block_comment and not in_template_literal
        skip_line.append(is_decorator)
        if is_decorator:
            masks.append([False] * len(line))
            continue

        i = 0
        while i < len(line):
            # Continue template literal from previous line
            if in_template_literal:
                mask[i] = False
                if line[i] == "\\":
                    i += 1
                    if i < len(line):
                        mask[i] = False
                    i += 1
                    continue
                if line[i] == "`":
                    in_template_literal = False
                    i += 1
                    continue
                i += 1
                continue

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

            # Template literal (backtick)
            if ch == "`":
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
                    if line[i] == "`":
                        mask[i] = False
                        i += 1
                        break
                    mask[i] = False
                    i += 1
                else:
                    # Backtick not closed — multi-line template literal
                    in_template_literal = True
                continue

            # String literals (double or single quoted)
            if ch in ('"', "'"):
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


# ---------------------------------------------------------------------------
# Language definition
# ---------------------------------------------------------------------------

LANGUAGE = {
    "name": "typescript",
    "source_globs": ["*.ts", "*.tsx", "*.js", "*.jsx"],
    "build_required": False,
    "compute_code_masks": compute_code_masks_typescript,
    "operators": _build_presets(),
}
