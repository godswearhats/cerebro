---
name: cerebro-run
description: Run Cerebro mutation testing on its own codebase (self-hosted dogfooding)
argument-hint: "[preset] [file-filter]"
allowed-tools: Bash Read Grep Glob
---

# Run Cerebro Self-Hosted Mutation Testing

Run mutation testing on Cerebro's own Python source using the `python` language plugin.

## Setup

1. Ensure `cerebro.json` exists in the project root with these settings:
   ```json
   {
     "language": "python",
     "source_dirs": ["src"],
     "exclude_patterns": ["__pycache__", "lang/python.py"],
     "test_command": "PYTHONPATH=src /home/aj/.local/bin/pytest tests/ -x -q",
     "test_timeout_seconds": 30,
     "preset": "STANDARD",
     "threshold": 60,
     "workers": 0,
     "report_dir": ".cerebro_reports"
   }
   ```

2. If the user provides a preset argument, use that preset (STANDARD, STRONGER, or ALL).
3. If the user provides a file filter, pass `--file <filter>` to scope the run. An exact
   relative path or file name wins outright; a partial string still matches as a substring,
   so `--file lang/` scopes to a directory.

Note: `build_tree` is absent here, which means the whole repo is copied per worker — that
is what you want. It is also correct to write `"."`.

## Execution

1. First verify all tests pass: `PYTHONPATH=src /home/aj/.local/bin/pytest tests/ -q`
2. Clear stale cache if needed: `rm -rf .cerebro_cache`
3. Run: `PYTHONPATH=src python -m cerebro run`
4. Parse the JSON report at `.cerebro_reports/cerebro-report.json`
5. Display per-file kill rates in a table, flagging files below threshold with `!!`

## Report Format

Show results like:
```
=== Cerebro Mutation Test Results ===
Total:     N
Killed:    N
Survived:  N
Score:     N.N%
Threshold: N%
Result:    PASS/FAIL

Per-file:
  OK module.py                   N/N killed  (N%)
  !! weak_module.py              N/N killed  (N%)
```
