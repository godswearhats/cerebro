---
name: mutation-test
description: Run cerebro mutation testing on the current project and summarise the kill rate per file. Use when the user asks to run mutation testing, check how well the tests verify the code, measure test strength, or set up cerebro (cerebro.json) for a C/C++, Python, TypeScript/JavaScript or GDScript project.
argument-hint: "[STANDARD|STRONGER|ALL] [file-filter]"
allowed-tools:
  - Bash(cerebro *)
  - Read
  - Grep
  - Glob
---

# Run cerebro mutation testing

Arguments: `$ARGUMENTS`

Interpret the arguments as follows; both are optional and may come in either order:

- A word that is exactly `STANDARD`, `STRONGER` or `ALL` is the operator preset. Pass it as
  `--preset <NAME>`. With no preset argument, pass no `--preset` so the config's value applies.
- Anything else is a file filter. Pass it as `--file <filter>`. An exact relative path or file
  name selects that file alone; any other string is matched as a substring, so `--file lang/`
  scopes the run to a directory.

## 1. Check cerebro is available

Run `cerebro list-operators`. If the command is not found, cerebro is not installed in the
active Python environment. Tell the user and offer to install it from this plugin's own copy
(no network access needed, Python 3.12+ required):

```bash
python3 -m pip install "${CLAUDE_PLUGIN_ROOT}"
```

Do not install without the user's agreement.

## 2. Find or create the config

Look for `cerebro.json` in the project root. If it exists, read it and use it as is.

If it does not exist, create it. Inspect the project to work out the values rather than
guessing, and show the user the file before the first run:

| Key | How to choose it |
|-----|------------------|
| `language` | `cpp`, `python`, `typescript` (also covers JavaScript) or `gdscript` |
| `source_dirs` | Directories holding production code. Leave test directories out. |
| `exclude_patterns` | Generated, vendored or test files inside `source_dirs` |
| `build_command` | Required for `cpp`; omit for interpreted languages |
| `test_command` | The project's real test command, taken from its CI config, Makefile, `package.json` or `pyproject.toml`. Make it stop at the first failure (`pytest -x`, `jest --bail`) since one failure is enough to kill a mutant. |
| `test_timeout_seconds` | Roughly three times the normal suite duration, minimum 30 |
| `preset` | `STANDARD` unless the user asks for more |
| `threshold` | `60` unless the user gives a target |

Rules for the commands:

- They run from a copy of the project in a temporary sandbox, one per worker. Use commands
  that resolve relative to the working directory. Never put an absolute path to the project
  or to a user's home directory in the config, because the file is shared with the team.
- The tests must import the sandbox's copy of the source, not an installed copy. For Python
  projects installed in editable mode, prefix the test command with the source path
  (`PYTHONPATH=src python3 -m pytest ...`), otherwise every mutant survives.
- Leave `build_tree` unset so the whole project is copied. Use `sandbox_exclude` for bulky
  directories the tests never read (`node_modules` caches, asset dumps, build output).

Make sure `.cerebro_cache*/` and `.cerebro_reports*/` are in `.gitignore`.

## 3. Verify the baseline

Run the configured `test_command` (and `build_command`, if any) once from the project root.
cerebro does not check this itself: if the suite already fails, every mutant is reported as
killed and the score is a meaningless 100%. If the baseline fails, stop and report the
failure instead of running cerebro.

## 4. Run

Preview the size of the job first when the project is large or the suite is slow:

```bash
cerebro dry-run [--preset NAME] [--file FILTER]
```

The total run time is roughly mutations × suite duration ÷ workers. If that is more than a
few minutes, tell the user the estimate and suggest narrowing with a file filter.

Then run:

```bash
cerebro run [--preset NAME] [--file FILTER]
```

Notes:

- Exit code `0` means the score met the threshold, `1` means it did not, `2` means a
  configuration or build error. Exit code `1` is a result, not a failure of the tool.
- Do not pass `--workers 1` unless the user asks. Sequential mode patches the working tree in
  place instead of using sandboxes, so an interrupted run can leave a mutation in a source file.
- Results are cached per source file by content hash. Unchanged files are not re-tested, even
  if the tests have changed. After tests change, delete the cache directory (`cache_dir` in the
  config, `.cerebro_cache` by default) before re-running.

## 5. Report

cerebro prints a per-file table and the list of surviving mutants, and writes
`cerebro-report.json` and `cerebro-report.html` to `report_dir` (`.cerebro_reports` by
default). Give the user:

- the overall score against the threshold, and PASS or FAIL
- the per-file table, marking files below the threshold
- the number of survivors, build errors and timeouts
- the path to the HTML report

A high number of build errors or timeouts usually means the config is wrong (timeout too
low, build command not sandbox-safe) rather than that the tests are strong. Say so.

If there are survivors, mention that `/cerebro:kill-mutants` writes tests for them.
