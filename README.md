# cerebro

Source-level mutation testing for C/C++ projects.

Zero dependencies beyond Python 3.8+. Patches source files with small mutations (operator swaps, boolean flips, etc.), recompiles, runs your test suite, and reports which mutations survived — revealing code your tests don't actually verify.

## Quick start

```bash
cp cerebro.py /path/to/your/project/
cp cerebro.example.json /path/to/your/project/cerebro.json
# Edit cerebro.json to match your build/test setup
python cerebro.py run
```

## Commands

| Command | Description |
|---------|-------------|
| `run` | Run mutation analysis (parallel by default) |
| `dry-run` | List mutations that would be tested, without running |
| `list-operators` | Show available mutation operators for a preset |
| `report` | Generate report from cached results (`--format text\|json\|html`) |

## Options

```
--config FILE    Config file (default: cerebro.json)
--workers N      Parallel workers (0 = auto, 1 = deterministic)
--file PATH      Run mutations on a single file only
--preset NAME    Operator preset: STANDARD, STRONGER, ALL
--format FMT     Report format: text, json, html
```

## Configuration

See `cerebro.example.json` for all options:

- **source_dirs** — directories to scan for source files
- **source_globs** — file patterns to include (e.g. `*.cpp`)
- **exclude_patterns** — files to skip
- **build_command** — how to compile your project
- **test_command** — how to run your test suite
- **build_tree** — subtree to copy for parallel sandboxes (`.` for whole repo)
- **preset** — mutation operator set (`STANDARD` covers relational, arithmetic, logical, unary, increment, and boolean operators)
- **threshold** — minimum kill rate percentage to pass
- **workers** — `0` for auto (CPU count), `1` for deterministic single-threaded

## Operator presets

| Preset | Operators |
|--------|-----------|
| STANDARD | `< <= > >= == != + - * / && \|\| ! ++ -- true false` |
| STRONGER | STANDARD + assignment operators, return value, condition removal |
| ALL | STRONGER + constant mutations (0↔1) |

## How it works

1. Scans source files for mutation sites matching the chosen operators
2. For each mutation, copies the build tree to a sandbox, patches the source, builds, and runs tests
3. If tests still pass → **survived** (potential gap). If tests fail → **killed** (good).
4. Results are cached by file content hash for incremental re-runs
