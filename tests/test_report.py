"""Tests for report.py — text, JSON, HTML report generation."""

import os
import tempfile

from cerebro.types import Mutation, MutationResult, KILLED, SURVIVED, BUILD_ERROR, TIMEOUT
from cerebro.report import compute_score, report_text, report_json, report_html
from cerebro.config import Config


def _mut(file="f.py", line=1, col=0, status=KILLED, op="test_op", desc="test"):
    m = Mutation(file=file, line=line, col=col, end_col=col + 1,
                 original="a", replacement="b", operator=op, description=desc)
    return MutationResult(mutation=m, status=status, duration_seconds=0.1)


def _cfg(tmpdir="/tmp"):
    return Config(
        language="python", source_dirs=["src"], source_globs=["*.py"],
        exclude_patterns=[], build_command="", test_command="pytest",
        test_timeout_seconds=30, build_timeout_seconds=120,
        preset="STANDARD", threshold=60, workers=0, build_jobs=2,
        build_tree="", cache_dir=".cache", report_dir=".reports",
        project_root=tmpdir, lang_plugin={},
    )


# ── compute_score ──────────────────────────────────────────────────────

class TestComputeScore:
    def test_all_killed(self):
        results = [_mut(status=KILLED), _mut(status=KILLED)]
        s = compute_score(results)
        assert s["score"] == 100.0
        assert s["killed"] == 2
        assert s["survived"] == 0
        assert s["testable"] == 2

    def test_all_survived(self):
        results = [_mut(status=SURVIVED), _mut(status=SURVIVED)]
        s = compute_score(results)
        assert s["score"] == 0.0
        assert s["survived"] == 2

    def test_mixed(self):
        results = [_mut(status=KILLED), _mut(status=SURVIVED)]
        s = compute_score(results)
        assert s["score"] == 50.0
        assert s["testable"] == 2

    def test_empty(self):
        s = compute_score([])
        assert s["total"] == 0
        assert s["score"] == 0.0

    def test_build_errors_not_testable(self):
        results = [_mut(status=KILLED), _mut(status=BUILD_ERROR)]
        s = compute_score(results)
        assert s["testable"] == 1
        assert s["build_errors"] == 1
        assert s["score"] == 100.0

    def test_timeouts_not_testable(self):
        results = [_mut(status=KILLED), _mut(status=TIMEOUT)]
        s = compute_score(results)
        assert s["testable"] == 1
        assert s["timeouts"] == 1

    def test_total_includes_all(self):
        results = [
            _mut(status=KILLED), _mut(status=SURVIVED),
            _mut(status=BUILD_ERROR), _mut(status=TIMEOUT),
        ]
        s = compute_score(results)
        assert s["total"] == 4


# ── report_text ────────────────────────────────────────────────────────

class TestReportText:
    def test_contains_header(self):
        text = report_text([_mut()], 60)
        assert "MUTATION TESTING REPORT" in text

    def test_contains_file(self):
        text = report_text([_mut(file="src/foo.py")], 60)
        assert "src/foo.py" in text

    def test_pass_status(self):
        text = report_text([_mut(status=KILLED)], 60)
        assert "PASS" in text

    def test_fail_status(self):
        text = report_text([_mut(status=SURVIVED)], 60)
        assert "FAIL" in text

    def test_survived_detail_section(self):
        text = report_text([_mut(status=SURVIVED, desc="x → y")], 60)
        assert "SURVIVED MUTANTS" in text
        assert "x → y" in text

    def test_no_survived_section_when_all_killed(self):
        text = report_text([_mut(status=KILLED)], 60)
        assert "SURVIVED MUTANTS" not in text

    def test_multiple_files(self):
        results = [_mut(file="a.py"), _mut(file="b.py")]
        text = report_text(results, 60)
        assert "a.py" in text
        assert "b.py" in text

    def test_long_filename_truncated(self):
        long_name = "a" * 50 + ".py"
        text = report_text([_mut(file=long_name)], 60)
        assert "..." in text

    def test_total_row(self):
        text = report_text([_mut(), _mut(status=SURVIVED)], 60)
        assert "TOTAL" in text


# ── report_json ────────────────────────────────────────────────────────

class TestReportJson:
    def test_structure(self):
        r = report_json([_mut()], _cfg())
        assert "preset" in r
        assert "threshold" in r
        assert "summary" in r
        assert "files" in r

    def test_summary_fields(self):
        r = report_json([_mut(), _mut(status=SURVIVED)], _cfg())
        s = r["summary"]
        assert s["total"] == 2
        assert s["killed"] == 1
        assert s["survived"] == 1

    def test_files_sorted(self):
        results = [_mut(file="z.py"), _mut(file="a.py")]
        r = report_json(results, _cfg())
        assert list(r["files"].keys()) == ["a.py", "z.py"]

    def test_mutations_sorted_by_line(self):
        results = [_mut(file="f.py", line=5), _mut(file="f.py", line=1)]
        r = report_json(results, _cfg())
        lines = [m["line"] for m in r["files"]["f.py"]]
        assert lines == [1, 5]

    def test_detail_included_when_present(self):
        m = _mut(status=BUILD_ERROR)
        m.detail = "compile error"
        r = report_json([m], _cfg())
        entry = r["files"]["f.py"][0]
        assert entry["detail"] == "compile error"

    def test_detail_absent_when_empty(self):
        r = report_json([_mut()], _cfg())
        entry = r["files"]["f.py"][0]
        assert "detail" not in entry


# ── report_html ────────────────────────────────────────────────────────

class TestReportHtml:
    def test_valid_html(self):
        html = report_html([_mut()], _cfg())
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_score_in_html(self):
        html = report_html([_mut(status=KILLED)], _cfg())
        assert "100.0%" in html

    def test_pass_class(self):
        html = report_html([_mut(status=KILLED)], _cfg())
        assert 'class="pass"' in html or "pass" in html

    def test_fail_class(self):
        html = report_html([_mut(status=SURVIVED)], _cfg())
        assert "fail" in html

    def test_file_section(self):
        html = report_html([_mut(file="src/mod.py")], _cfg())
        assert "src/mod.py" in html

    def test_badges(self):
        html = report_html([_mut(status=KILLED), _mut(status=SURVIVED, line=2)], _cfg())
        assert "badge" in html

    def test_source_annotation_with_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            with open(os.path.join(src, "f.py"), "w") as f:
                f.write("x = True\ny = False\n")
            cfg = _cfg(d)
            results = [_mut(file="src/f.py", line=1, status=KILLED)]
            html = report_html(results, cfg)
            assert "x = True" in html
            assert "killed" in html

    def test_missing_source_no_crash(self):
        """Report should not crash if source file is missing."""
        cfg = _cfg("/nonexistent")
        html = report_html([_mut(file="missing.py")], cfg)
        assert "missing.py" in html

    def test_html_escaping(self):
        """Special chars in source should be HTML-escaped."""
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            with open(os.path.join(src, "f.py"), "w") as f:
                f.write('x = "<script>"\n')
            cfg = _cfg(d)
            html = report_html([_mut(file="src/f.py")], cfg)
            assert "<script>" not in html
            assert "&lt;script&gt;" in html

    def test_both_class_when_killed_and_survived(self):
        results = [
            _mut(file="f.py", line=1, status=KILLED),
            _mut(file="f.py", line=1, col=5, status=SURVIVED),
        ]
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            with open(os.path.join(src, "f.py"), "w") as f:
                f.write("x = True\n")
            cfg = _cfg(d)
            # Adjust file paths
            results[0].mutation.file = "src/f.py"
            results[1].mutation.file = "src/f.py"
            html = report_html(results, cfg)
            assert "both" in html
