"""Tests for cli.py — argument parsing, subcommands, output helpers."""

import json
import os
import sys
import tempfile

import pytest

from cerebro.cli import build_parser, info, die, cmd_list_operators, cmd_dry_run, cmd_report


# ── Output helpers ─────────────────────────────────────────────────────

class TestOutputHelpers:
    def test_info(self, capsys):
        info("hello")
        assert "hello" in capsys.readouterr().err

    def test_die(self):
        with pytest.raises(SystemExit) as exc_info:
            die("boom")
        assert exc_info.value.code == 2


# ── Argument parser ────────────────────────────────────────────────────

class TestBuildParser:
    def test_run_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.command == "run"
        assert args.config == "cerebro.json"
        assert args.preset is None
        assert args.threshold is None
        assert args.workers is None
        assert args.file is None

    def test_run_all_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "run", "--config", "custom.json", "--preset", "STRONGER",
            "--threshold", "80", "--workers", "4", "--file", "foo.py",
        ])
        assert args.config == "custom.json"
        assert args.preset == "STRONGER"
        assert args.threshold == 80
        assert args.workers == 4
        assert args.file == "foo.py"

    def test_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["dry-run", "--config", "c.json"])
        assert args.command == "dry-run"
        assert args.config == "c.json"

    def test_list_operators(self):
        parser = build_parser()
        args = parser.parse_args(["list-operators"])
        assert args.command == "list-operators"
        assert args.config is None

    def test_list_operators_with_preset(self):
        parser = build_parser()
        args = parser.parse_args(["list-operators", "--preset", "ALL"])
        assert args.preset == "ALL"

    def test_report_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["report"])
        assert args.command == "report"
        assert args.format == "text"

    def test_report_json_format(self):
        parser = build_parser()
        args = parser.parse_args(["report", "--format", "json"])
        assert args.format == "json"

    def test_no_command_fails(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


# ── cmd_list_operators ─────────────────────────────────────────────────

class TestCmdListOperators:
    def test_lists_default_operators(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["list-operators"])
        cmd_list_operators(args)
        out = capsys.readouterr().out
        assert "relational" in out or "arithmetic" in out
        assert "operators" in out

    def test_lists_with_config(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            cfg_path = os.path.join(d, "cerebro.json")
            with open(cfg_path, "w") as f:
                json.dump({
                    "language": "python",
                    "source_dirs": ["src"],
                    "test_command": "pytest",
                }, f)
            parser = build_parser()
            args = parser.parse_args(["list-operators", "--config", cfg_path])
            cmd_list_operators(args)
            out = capsys.readouterr().out
            assert "python" in out

    def test_invalid_preset_exits(self):
        parser = build_parser()
        # Can't test via parse_args since argparse restricts choices,
        # but we can construct args manually
        import argparse
        args = argparse.Namespace(preset="NONEXISTENT", config=None)
        with pytest.raises(SystemExit):
            cmd_list_operators(args)


# ── cmd_dry_run ────────────────────────────────────────────────────────

class TestCmdDryRun:
    def test_dry_run_output(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            with open(os.path.join(src, "sample.py"), "w") as f:
                f.write("x = True and False\n")
            cfg_path = os.path.join(d, "cerebro.json")
            with open(cfg_path, "w") as f:
                json.dump({
                    "language": "python",
                    "source_dirs": ["src"],
                    "test_command": "pytest",
                }, f)
            parser = build_parser()
            args = parser.parse_args(["dry-run", "--config", cfg_path])
            cmd_dry_run(args)
            out = capsys.readouterr().out
            assert "sample.py" in out
            assert "mutations" in out
            assert "dry run" in out

    def test_dry_run_no_files_exits(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            cfg_path = os.path.join(d, "cerebro.json")
            with open(cfg_path, "w") as f:
                json.dump({
                    "language": "python",
                    "source_dirs": ["src"],
                    "test_command": "pytest",
                }, f)
            parser = build_parser()
            args = parser.parse_args(["dry-run", "--config", cfg_path, "--file", "nonexistent"])
            with pytest.raises(SystemExit):
                cmd_dry_run(args)

    def test_dry_run_with_file_filter(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            with open(os.path.join(src, "foo.py"), "w") as f:
                f.write("x = True\n")
            with open(os.path.join(src, "bar.py"), "w") as f:
                f.write("y = False\n")
            cfg_path = os.path.join(d, "cerebro.json")
            with open(cfg_path, "w") as f:
                json.dump({
                    "language": "python",
                    "source_dirs": ["src"],
                    "test_command": "pytest",
                }, f)
            parser = build_parser()
            args = parser.parse_args(["dry-run", "--config", cfg_path, "--file", "foo"])
            cmd_dry_run(args)
            out = capsys.readouterr().out
            assert "foo.py" in out
            assert "bar.py" not in out


# ── cmd_report ─────────────────────────────────────────────────────────

class TestCmdReport:
    def _setup_cached(self, d):
        src = os.path.join(d, "src")
        os.makedirs(src)
        with open(os.path.join(src, "f.py"), "w") as f:
            f.write("x = 1\n")
        cfg_path = os.path.join(d, "cerebro.json")
        with open(cfg_path, "w") as f:
            json.dump({
                "language": "python",
                "source_dirs": ["src"],
                "test_command": "pytest",
            }, f)
        cache_dir = os.path.join(d, ".cerebro_cache")
        os.makedirs(cache_dir)
        with open(os.path.join(cache_dir, "results.json"), "w") as f:
            json.dump({
                "version": 1,
                "preset": "STANDARD",
                "files": {
                    "src/f.py": {
                        "hash": "abc",
                        "mutations": [
                            {"line": 1, "col": 0, "op": "test", "status": "killed", "description": "test"},
                            {"line": 1, "col": 4, "op": "test", "status": "survived", "description": "test2"},
                        ],
                    }
                },
            }, f)
        return cfg_path

    def test_report_text(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._setup_cached(d)
            parser = build_parser()
            args = parser.parse_args(["report", "--config", cfg_path])
            cmd_report(args)
            out = capsys.readouterr().out
            assert "MUTATION TESTING REPORT" in out

    def test_report_json(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._setup_cached(d)
            parser = build_parser()
            args = parser.parse_args(["report", "--config", cfg_path, "--format", "json"])
            cmd_report(args)
            out = capsys.readouterr().out
            data = json.loads(out)
            assert "summary" in data
            assert "files" in data

    def test_report_html(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._setup_cached(d)
            parser = build_parser()
            args = parser.parse_args(["report", "--config", cfg_path, "--format", "html"])
            cmd_report(args)
            out = capsys.readouterr().out
            assert "<html" in out

    def test_report_no_cache_exits(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            cfg_path = os.path.join(d, "cerebro.json")
            with open(cfg_path, "w") as f:
                json.dump({
                    "language": "python",
                    "source_dirs": ["src"],
                    "test_command": "pytest",
                }, f)
            parser = build_parser()
            args = parser.parse_args(["report", "--config", cfg_path])
            with pytest.raises(SystemExit):
                cmd_report(args)

    def test_report_unknown_format_exits(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._setup_cached(d)
            import argparse
            args = argparse.Namespace(config=cfg_path, format="xml")
            with pytest.raises(SystemExit):
                cmd_report(args)
