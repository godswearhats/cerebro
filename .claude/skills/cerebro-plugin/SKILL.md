---
name: cerebro-plugin
description: Add support for a new programming language to cerebro by creating a language plugin in src/cerebro/lang/. For contributors working in the cerebro repository itself.
argument-hint: "<language-name>"
allowed-tools:
  - Read
  - Grep
  - Glob
disable-model-invocation: true
---

# Create a Cerebro Language Plugin

Language: `$ARGUMENTS`

Create a new language plugin for Cerebro at `src/cerebro/lang/<language>.py`. Cerebro auto-discovers plugins from this directory — no registration needed.

## Plugin Interface

Every plugin exports a `LANGUAGE` dictionary:

```python
LANGUAGE = {
    "name": "<language>",              # unique identifier
    "source_globs": ["*.<ext>"],       # file patterns to scan
    "build_required": bool,            # True for compiled languages
    "compute_code_masks": <function>,  # masking function
    "operators": <presets dict>,       # operator presets
}
```

## Required Components

### 1. Code Masking Function

Signature: `(lines: list[str]) -> tuple[list[list[bool]], list[bool]]`

Returns:
- `masks`: per-character booleans (True = mutable code, False = comment/string/directive)
- `skip_line`: per-line booleans (True = skip entire line, e.g. decorators/preprocessor)

Must handle:
- **Comments**: line comments and block comments (track multi-line state across lines)
- **String literals**: all quote styles, escape sequences, multi-line strings
- **Non-mutable lines**: decorators, annotations, preprocessor directives

Pattern: iterate character-by-character, maintaining state for multi-line constructs.

### 2. Language-Specific Operators

Three tiers composing from `SHARED_OPERATORS`:

```python
from cerebro.operators import SHARED_OPERATORS, OperatorDef

# Filter shared ops if needed (e.g., Python uses True/False not true/false)
_SHARED_STANDARD = [op for op in SHARED_OPERATORS["STANDARD"] if ...]

_LANG_STANDARD = [...]   # language-specific STANDARD operators
_LANG_STRONGER = [...]   # additional STRONGER operators
_LANG_ALL = [...]         # additional ALL operators

def _build_presets():
    standard = _SHARED_STANDARD + _LANG_STANDARD
    stronger = standard + SHARED_OPERATORS["STRONGER_EXTRA"] + _LANG_STRONGER
    all_ops = stronger + _LANG_ALL
    return {"STANDARD": standard, "STRONGER": stronger, "ALL": all_ops}
```

**STANDARD tier** — high-confidence mutations that should always be caught:
- Boolean literal flips (match the language's casing: True/False vs true/false)
- Logical operator swaps (&&/|| or and/or or keyword variants)
- Unary negation removal (! or not)
- Language-specific identity/membership operators
- Void call removal (replace with no-op: `;` or `pass`)
- Return null/None/nullptr

**STRONGER tier** — deeper mutations:
- Condition removal (if cond → if true/false)
- Statement removal (assignments → no-op)
- String literal emptying
- Assignment operator swaps (+=/-=)

**ALL tier** — exhaustive:
- Constant mutations (0↔1)
- Language-specific exotic operators (power, floor div, match patterns)

### 3. Shared Operator Adjustments

The shared operators use lowercase `true`/`false`. Languages with different casing (Python: `True`/`False`) must:
1. Filter out the shared boolean operators
2. Add language-specific boolean operators

## After Creating the Plugin

1. Verify it loads: `PYTHONPATH=src python3 -c "from cerebro.lang.<name> import LANGUAGE; print(LANGUAGE['name'])"`
2. Verify discovery: `PYTHONPATH=src python3 -c "from cerebro.config import load_language; print(load_language('<name>')['name'])"`
3. Test masking on sample code
4. Test mutation generation on a real file
5. Write tests in `tests/test_<name>_plugin.py` covering:
   - Plugin metadata (name, globs, build_required, presets)
   - Code masking (comments, strings, escapes, multi-line, decorators)
   - Each operator category (pattern matches, replacements, negative cases)
   - Integration: `generate_mutations()` on sample code
6. Run `/cerebro-dogfood` to dogfood it

## Reference: Existing Plugins

- `cpp.py` — compiled, block+line comments, char literals, preprocessor directives
- `typescript.py` — interpreted, template literals, decorators, strict equality, optional chaining
- `gdscript.py` — interpreted, keyword logicals (and/or/not), annotations, match patterns
- `python.py` — interpreted, keyword logicals, identity (is/is not), membership (in/not in), triple-quoted strings, decorators, capitalized booleans
