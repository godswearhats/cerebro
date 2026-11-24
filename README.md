# cerebro

Multi-language source-level mutation testing.

Zero dependencies beyond Python 3.12+. Patches source files with small mutations (operator swaps, boolean flips, etc.), optionally recompiles, runs your test suite, and reports which mutations survived — revealing code your tests don't actually verify.

Supports C/C++, Python, TypeScript/JavaScript, and GDScript out of the box.

## Installation

Install as a Python package (no need to copy files into your project):

```bash
# Editable install (changes to cerebro source take effect immediately)
pip install -e /path/to/cerebro

# Or via your system's package manager if preferred
```

Then from any project directory:

```bash
cp /path/to/cerebro/cerebro.example.json ./cerebro.json
# Edit cerebro.json to match your build/test setup
cerebro run
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
--file PATH      Run mutations on a single file. An exact relative path or file
                 name wins; otherwise it is matched as a substring
--preset NAME    Operator preset: STANDARD, STRONGER, ALL
--format FMT     Report format: text, json, html
```

## Configuration

See `cerebro.example.json` for all options:

- **language** — `cpp`, `python`, `typescript`, or `gdscript` (default: `cpp`)
- **source_dirs** — directories to scan for source files
- **source_globs** — file patterns to include (e.g. `*.cpp`); defaults to language's standard globs
- **exclude_patterns** — files to skip
- **build_command** — how to compile your project (required for compiled languages)
- **test_command** — how to run your test suite
- **build_tree** — subtree to copy for parallel sandboxes; `""` or `.` for the
  whole repo. Anything outside the subtree is symlinked into each sandbox
  rather than copied, so name a subtree only when the rest of the repo is
  read-only during a test run.
- **sandbox_exclude** — extra entries to leave out of each worker's copy, on top
  of `.git`/`__pycache__`/cerebro's own dirs. Use it for bulky directories the
  tests never read (asset dumps, vendored toolchains). A bare name or glob
  (`build`, `*.rom`) matches at any depth; include a `/` to pin it to one
  location (`assets/generated`).
- **preset** — mutation operator set (`STANDARD`, `STRONGER`, `ALL`)
- **threshold** — minimum kill rate percentage to pass
- **workers** — `0` for auto (CPU count), `1` for deterministic single-threaded

## Operator presets

| Preset | Operators |
|--------|-----------|
| STANDARD | Relational, arithmetic, logical, unary, increment, boolean, void call removal, null return |
| STRONGER | STANDARD + assignment operators, return value, condition removal, statement removal, switch mutations |
| ALL | STRONGER + constant mutations (0/1) |

Each language adds its own operators on top of the shared set (e.g. TypeScript adds `===`/`!==`, `??`, `?.`, arrow functions; GDScript adds `and`/`or`/`not` keywords, match patterns).

## How it works

1. Scans source files for mutation sites matching the chosen operators
2. Code masking prevents mutations inside comments, strings, and preprocessor directives
3. For each mutation, copies the build tree to a sandbox, patches the source, builds, and runs tests
4. If tests still pass -> **survived** (potential gap). If tests fail -> **killed** (good).
5. Results are cached by file content hash for incremental re-runs

## Claude Code skills

This repository is also a [Claude Code](https://claude.com/claude-code) plugin, so Claude can run cerebro on your project and write tests for the survivors.

Install it from inside Claude Code:

```
/plugin install cerebro --marketplace godswearhats/cerebro
```

On older versions of Claude Code, use the two-step form:

```
/plugin marketplace add godswearhats/cerebro
/plugin install cerebro@cerebro-plugins
```

| Skill | What it does |
|-------|--------------|
| `/cerebro:mutation-test [preset] [file-filter]` | Creates `cerebro.json` if the project has none, checks the test suite passes, runs cerebro and summarises the kill rate per file |
| `/cerebro:kill-mutants [file-filter]` | Reads the latest report, works out why each mutant survived and writes tests that kill them |

The skills call the `cerebro` command, so it must be installed in the Python environment Claude runs in. If it is missing, `/cerebro:mutation-test` offers to install it from the plugin's own copy.

To offer the plugin to everyone working in one of your repositories, add this to that repository's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "cerebro-plugins": {
      "source": { "source": "github", "repo": "godswearhats/cerebro" }
    }
  },
  "enabledPlugins": {
    "cerebro@cerebro-plugins": true
  }
}
```

If you work from a fork or an internal mirror, replace `godswearhats/cerebro` with its location.

### Contributing to cerebro

Two more skills in `.claude/skills/` load automatically when Claude Code is opened in this repository:

| Skill | What it does |
|-------|--------------|
| `/cerebro-dogfood [preset] [file-filter]` | Runs cerebro on its own source and writes tests for the survivors |
| `/cerebro-plugin <language>` | Walks through adding a language plugin in `src/cerebro/lang/` |
