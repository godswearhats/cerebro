---
name: cerebro-kill
description: Analyze surviving mutants from a Cerebro run and write tests to kill them
argument-hint: "[file-filter]"
allowed-tools: Bash Read Write Edit Grep Glob
---

# Kill Surviving Mutants

Analyze the most recent Cerebro mutation test report and write targeted tests to kill surviving mutants.

## Step 1: Load the Report

Read `.cerebro_reports/cerebro-report.json` and extract all surviving mutants. Group them by file and sort by line number.

If the user specifies a file filter, only show/target survivors in matching files.

## Step 2: Analyze Survivors

For each file with survivors:
1. Read the source file
2. For each surviving mutant, understand what code path it's in and why tests didn't catch the mutation
3. Categorize survivors:
   - **Easy kills**: boolean flips, operator swaps in tested functions — just need a more specific assertion
   - **Missing coverage**: entire functions or branches with no test coverage
   - **Hard to test**: code that requires external setup (I/O, subprocesses, etc.)

## Step 3: Write Tests

For each file, write targeted tests in the corresponding `tests/test_<module>.py` file:

- **Pattern**: read existing tests first to match style and helpers (see `tests/conftest.py` for shared utilities)
- **Naming**: test class per logical group, descriptive method names
- **Focus**: each test should kill at least one specific surviving mutant
- **Avoid**: don't write tests that only pass by accident — each test should assert the specific behavior the mutant would break
- **Masking tests**: for language plugin masks (`compute_code_masks_*`), use the `_mutable_chars` helper pattern to verify specific characters are/aren't masked

## Step 4: Verify

1. Run `PYTHONPATH=src /home/aj/.local/bin/pytest tests/ -q` to confirm all tests pass
2. Optionally re-run Cerebro to measure improvement

## Key Patterns from Cerebro's Test Suite

- `conftest.py` has: `make_op()`, `find_op()`, `matches()`, `all_mutable_mask()`, `mutate()`
- Mask tests use: `masks, skip = mask_fn([line])` then check individual positions
- Operator tests use: `re.finditer(op.pattern, text)` to verify matches
- Config tests use: `tempfile.TemporaryDirectory()` with real filesystem
- Runner tests use: real subprocess calls (e.g., `run_command("echo hello", "/tmp", 10)`)
- Use `pytest.raises(SystemExit)` for functions that call `sys.exit()`
