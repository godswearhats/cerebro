---
name: cerebro-dogfood
description: Run cerebro's mutation testing against cerebro's own source and write tests for the survivors. For contributors working in the cerebro repository itself; use when asked to dogfood, self-host, or check the mutation score of cerebro's own code.
argument-hint: "[STANDARD|STRONGER|ALL] [file-filter]"
allowed-tools:
  - Read
  - Grep
  - Glob
---

# Dogfood cerebro on its own source

Arguments: `$ARGUMENTS`

A word that is exactly `STANDARD`, `STRONGER` or `ALL` is the preset (`--preset <NAME>`).
Anything else is a file filter (`--file <filter>`): an exact relative path or file name wins
outright, and any other string matches as a substring, so `--file lang/` scopes to a directory.

The committed `cerebro.json` in the repository root is the self-hosting config. Use it as is.
`build_tree` is absent from it on purpose: the whole repository is copied per worker.

Every command below sets `PYTHONPATH=src`. That is required even with an editable install,
because the tests in each sandbox must import that sandbox's mutated copy of the source.

## Run

1. Confirm the suite passes: `PYTHONPATH=src python3 -m pytest tests/ -q`
2. Clear the cache if tests have changed since the last run: `rm -rf .cerebro_cache`.
   Results are cached by source file hash, so new tests are otherwise ignored.
3. Run: `PYTHONPATH=src python3 -m cerebro run [--preset NAME] [--file FILTER]`
4. Report the overall score, the per-file table from the output (flag files below the
   threshold), and the survivors. The JSON report is at `.cerebro_reports/cerebro-report.json`.

Exit code `1` means the score is below the threshold, not that the run failed.

## Kill survivors

For each survivor in the report, read the source, work out why no test caught the change,
and add a test to the matching `tests/test_<module>.py` that fails against the mutant.

Conventions in cerebro's test suite:

- `tests/conftest.py` provides `make_op()`, `find_op()`, `matches()`, `all_mutable_mask()`
  and `mutate()`
- One test class per logical group, with descriptive method names
- Mask tests call `masks, skip = mask_fn([line])` and check individual positions; for the
  language plugin masks (`compute_code_masks_*`) use the `_mutable_chars` helper pattern
- Operator tests use `re.finditer(op.pattern, text)` to verify matches
- Config tests use `tempfile.TemporaryDirectory()` with a real filesystem
- Runner tests make real subprocess calls, for example `run_command("echo hello", "/tmp", 10)`
- Use `pytest.raises(SystemExit)` for functions that call `sys.exit()`

Then repeat the run steps, including clearing the cache.
