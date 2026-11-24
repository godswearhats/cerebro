---
name: kill-mutants
description: Analyse the surviving mutants from the latest cerebro mutation testing run and write tests that kill them. Use after a cerebro run reports survivors, or when the user asks to raise the mutation score, kill mutants, or close the test gaps cerebro found.
argument-hint: "[file-filter]"
allowed-tools:
  - Bash(cerebro *)
  - Read
  - Grep
  - Glob
---

# Kill surviving mutants

File filter: `$ARGUMENTS`

If a file filter is given, only work on survivors whose file path contains it. With no
filter, work on all survivors, starting with the files that have the lowest score.

## 1. Load the report

Read `cerebro.json` to find `report_dir` (default `.cerebro_reports`) and `test_command`,
then read `<report_dir>/cerebro-report.json`. If there is no report, run
`/cerebro:mutation-test` first.

The report has this shape:

```json
{
  "preset": "STANDARD",
  "threshold": 60,
  "summary": { "total": 0, "killed": 0, "survived": 0, "score": 0.0 },
  "files": {
    "src/module.py": [
      { "line": 42, "col": 8, "original": "<", "replacement": "<=",
        "operator": "rel_lt_to_le", "description": "< → <=", "status": "survived" }
    ]
  }
}
```

Collect the entries with `"status": "survived"`, grouped by file and ordered by line.
`line` is 1-based and `col` is 0-based.

A report from a run with `--file` only covers the files that run selected.

## 2. Analyse each survivor

Read the source around each surviving mutant and work out why no test failed when the code
was changed. Sort each one into a category:

- **Weak assertion**: the code is executed by a test, but nothing asserts on the value the
  mutation changes. Needs a more specific assertion.
- **Missing boundary**: a relational or arithmetic swap survives because no test uses the
  boundary value. Needs a test at the exact edge.
- **No coverage**: no test reaches the function or branch at all.
- **Equivalent mutant**: the mutated code behaves identically for every input (for example
  `<` to `<=` where equality cannot occur). No test can kill it.
- **Hard to test**: needs I/O, subprocesses, timing or other external setup.

## 3. Write tests

Before writing anything, read the existing tests for the module and follow the project's
conventions: test framework, file layout and naming, fixtures and shared helpers.

- Each new test must fail against the mutant and pass against the original. State which
  mutant it targets if that is not obvious from the test.
- Assert the specific behaviour the mutation breaks. Do not assert on incidental details
  just to make the mutant fail.
- Prefer extending an existing test file over creating a new one.
- Do not change production code to make a mutant easier to kill. If a survivor reveals a
  real bug or dead code, report it to the user and leave the source alone.
- Do not write tests for equivalent mutants. List them in the summary with the reason.

## 4. Verify

1. Run the `test_command` from `cerebro.json` and confirm the whole suite passes.
2. Delete the cache directory (`cache_dir` in the config, `.cerebro_cache` by default).
   cerebro caches results by source file hash, so without this the old "survived" results
   are reused and the new tests appear to have no effect.
3. Re-run cerebro on the files you worked on: `cerebro run --file <file>`.

## 5. Summarise

Report the score before and after, the mutants killed, and every survivor left alive with
its category and the reason it was not killed.
