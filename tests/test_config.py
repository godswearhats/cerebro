"""Tests for config.py — language discovery, config loading, source discovery."""

import json
import os
import sys
import tempfile

import pytest

from cerebro.config import (
    Config,
    DEFAULT_CONFIG,
    builtin_cpp_code_masks,
    _builtin_cpp_operators,
    _discover_languages,
    load_language,
    discover_sources,
    select_sources,
)


# ── Built-in C++ fallback masks ────────────────────────────────────────

class TestBuiltinCppCodeMasks:
    def _mutable(self, line: str) -> str:
        masks, _ = builtin_cpp_code_masks([line])
        return "".join(ch for ch, m in zip(line, masks[0]) if m)

    def test_line_comment(self):
        assert self._mutable("x = 1; // comment") == "x = 1; "

    def test_block_comment(self):
        assert self._mutable("x = /* hidden */ y;") == "x =  y;"

    def test_string_literal(self):
        assert self._mutable('x = "hello";') == "x = ;"

    def test_char_literal(self):
        assert self._mutable("x = 'a';") == "x = ;"

    def test_escape_in_string(self):
        assert self._mutable(r'x = "he\"llo";') == "x = ;"

    def test_preprocessor_skip(self):
        masks, skip = builtin_cpp_code_masks(["#include <foo>", "int x;"])
        assert skip[0] is True
        assert all(not m for m in masks[0])
        assert skip[1] is False

    def test_multiline_block_comment(self):
        lines = ["x; /*", "mid", "*/ y;"]
        masks, _ = builtin_cpp_code_masks(lines)
        assert masks[0][0]  # 'x' mutable
        assert not masks[0][-1]  # inside comment
        assert not any(masks[1])  # all comment
        assert not masks[2][0]  # '*'
        assert masks[2][3]  # 'y' mutable

    def test_empty_line(self):
        masks, skip = builtin_cpp_code_masks([""])
        assert masks[0] == []
        assert skip[0] is False

    def test_preprocessor_inside_block_comment_not_skipped(self):
        """A #define inside a block comment should not set skip flag."""
        lines = ["/* start", "#define FOO", "*/"]
        masks, skip = builtin_cpp_code_masks(lines)
        # Line 1 is inside block comment, but also starts with # —
        # in_block takes priority, so it shouldn't be treated as PP
        assert not any(masks[1])


# ── Built-in C++ operators ─────────────────────────────────────────────

class TestBuiltinCppOperators:
    def test_has_presets(self):
        ops = _builtin_cpp_operators()
        assert "STANDARD" in ops
        assert "STRONGER" in ops
        assert "ALL" in ops

    def test_standard_has_shared(self):
        ops = _builtin_cpp_operators()
        names = {op.name for op in ops["STANDARD"]}
        assert "rel_lt_to_lte" in names
        assert "arith_add_to_sub" in names

    def test_standard_has_cpp_specific(self):
        ops = _builtin_cpp_operators()
        names = {op.name for op in ops["STANDARD"]}
        assert "log_and_to_or" in names
        assert "void_call_remove" in names
        assert "return_nullptr" in names

    def test_stronger_superset(self):
        ops = _builtin_cpp_operators()
        std = {op.name for op in ops["STANDARD"]}
        strong = {op.name for op in ops["STRONGER"]}
        assert std.issubset(strong)

    def test_all_superset(self):
        ops = _builtin_cpp_operators()
        strong = {op.name for op in ops["STRONGER"]}
        all_ops = {op.name for op in ops["ALL"]}
        assert strong.issubset(all_ops)

    def test_all_has_constants(self):
        ops = _builtin_cpp_operators()
        names = {op.name for op in ops["ALL"]}
        assert "const_zero_to_one" in names
        assert "const_one_to_zero" in names


# ── Language plugin discovery ──────────────────────────────────────────

class TestDiscoverLanguages:
    def test_discovers_plugins(self):
        langs = _discover_languages()
        assert "cpp" in langs
        assert "typescript" in langs
        assert "gdscript" in langs
        assert "python" in langs

    def test_plugin_has_required_keys(self):
        langs = _discover_languages()
        for name, lang in langs.items():
            assert "name" in lang, f"{name} missing 'name'"
            assert "source_globs" in lang, f"{name} missing 'source_globs'"
            assert "build_required" in lang, f"{name} missing 'build_required'"
            assert "compute_code_masks" in lang, f"{name} missing 'compute_code_masks'"
            assert "operators" in lang, f"{name} missing 'operators'"

    def test_skips_dunder_files(self):
        langs = _discover_languages()
        # __init__.py should not be treated as a plugin
        for name in langs:
            assert not name.startswith("_")


class TestLoadLanguage:
    def test_load_known_plugin(self):
        lang = load_language("python")
        assert lang["name"] == "python"

    def test_load_cpp_plugin(self):
        lang = load_language("cpp")
        assert lang["name"] == "cpp"

    def test_load_unknown_exits(self):
        with pytest.raises(SystemExit):
            load_language("nonexistent_lang_xyz")

    def test_load_cpp_fallback(self):
        """Even if cpp plugin file exists, load_language('cpp') should work."""
        lang = load_language("cpp")
        assert "operators" in lang
        assert "STANDARD" in lang["operators"]


# ── Config loading ─────────────────────────────────────────────────────

class TestConfigLoad:
    def _write_config(self, tmpdir, cfg_dict):
        path = os.path.join(tmpdir, "cerebro.json")
        with open(path, "w") as f:
            json.dump(cfg_dict, f)
        return path

    def test_load_minimal_python_config(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            path = self._write_config(d, {
                "language": "python",
                "source_dirs": ["src"],
                "test_command": "pytest",
            })
            cfg = Config.load(path)
            assert cfg.language == "python"
            assert cfg.test_command == "pytest"
            assert cfg.source_globs == ["*.py"]  # from plugin defaults
            assert cfg.lang_plugin["name"] == "python"

    def test_load_applies_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            path = self._write_config(d, {
                "language": "python",
                "source_dirs": ["src"],
                "test_command": "pytest",
            })
            cfg = Config.load(path)
            assert cfg.preset == "STANDARD"
            assert cfg.threshold == 60
            assert cfg.workers == 0

    def test_load_missing_file_exits(self):
        with pytest.raises(SystemExit):
            Config.load("/nonexistent/cerebro.json")

    def test_load_missing_test_command_exits(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            path = self._write_config(d, {
                "language": "python",
                "source_dirs": ["src"],
            })
            with pytest.raises(SystemExit):
                Config.load(path)

    def test_load_missing_source_dirs_exits(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_config(d, {
                "language": "python",
                "test_command": "pytest",
            })
            with pytest.raises(SystemExit):
                Config.load(path)

    def test_load_build_required_no_build_cmd_exits(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            path = self._write_config(d, {
                "language": "cpp",
                "source_dirs": ["src"],
                "test_command": "./test",
                # no build_command — cpp requires it
            })
            with pytest.raises(SystemExit):
                Config.load(path)

    def test_load_invalid_preset_exits(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            path = self._write_config(d, {
                "language": "python",
                "source_dirs": ["src"],
                "test_command": "pytest",
                "preset": "NONEXISTENT",
            })
            with pytest.raises(SystemExit):
                Config.load(path)

    def test_load_custom_source_globs(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            path = self._write_config(d, {
                "language": "python",
                "source_dirs": ["src"],
                "test_command": "pytest",
                "source_globs": ["*.pyx"],
            })
            cfg = Config.load(path)
            assert cfg.source_globs == ["*.pyx"]


# ── Config methods ─────────────────────────────────────────────────────

class TestConfigMethods:
    def _make_cfg(self, **kwargs):
        defaults = {
            "language": "python",
            "source_dirs": ["src"],
            "source_globs": ["*.py"],
            "exclude_patterns": [],
            "build_command": "",
            "test_command": "pytest",
            "test_timeout_seconds": 30,
            "build_timeout_seconds": 120,
            "preset": "STANDARD",
            "threshold": 60,
            "workers": 0,
            "build_jobs": 2,
            "build_tree": "",
            "cache_dir": ".cerebro_cache",
            "report_dir": ".cerebro_reports",
            "project_root": "/tmp/proj",
            "lang_plugin": {},
        }
        defaults.update(kwargs)
        return Config(**defaults)

    def test_resolve_path(self):
        cfg = self._make_cfg(project_root="/tmp/proj")
        assert cfg.resolve("src/foo.py").endswith("src/foo.py")
        assert cfg.resolve("src/foo.py").startswith("/")

    def test_effective_workers_one(self):
        cfg = self._make_cfg(workers=1)
        assert cfg.effective_workers(100) == 1

    def test_effective_workers_explicit(self):
        cfg = self._make_cfg(workers=4)
        assert cfg.effective_workers(100) == 4

    def test_effective_workers_explicit_capped(self):
        cfg = self._make_cfg(workers=4)
        assert cfg.effective_workers(2) == 2

    def test_effective_workers_zero_mutations(self):
        cfg = self._make_cfg(workers=4)
        assert cfg.effective_workers(0) == 1

    def test_effective_workers_auto(self):
        cfg = self._make_cfg(workers=0, build_jobs=2)
        n = cfg.effective_workers(1000)
        assert n >= 1


# ── Source discovery ───────────────────────────────────────────────────

class TestDiscoverSources:
    def _make_cfg(self, project_root, **kwargs):
        defaults = {
            "language": "python",
            "source_dirs": ["src"],
            "source_globs": ["*.py"],
            "exclude_patterns": [],
            "build_command": "",
            "test_command": "pytest",
            "test_timeout_seconds": 30,
            "build_timeout_seconds": 120,
            "preset": "STANDARD",
            "threshold": 60,
            "workers": 0,
            "build_jobs": 2,
            "build_tree": "",
            "cache_dir": ".cache",
            "report_dir": ".reports",
            "project_root": project_root,
            "lang_plugin": {},
        }
        defaults.update(kwargs)
        return Config(**defaults)

    def test_finds_matching_files(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            open(os.path.join(src, "foo.py"), "w").close()
            open(os.path.join(src, "bar.py"), "w").close()
            open(os.path.join(src, "readme.txt"), "w").close()
            cfg = self._make_cfg(d)
            sources = discover_sources(cfg)
            assert len(sources) == 2
            assert all(s.endswith(".py") for s in sources)

    def test_exclude_patterns(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            open(os.path.join(src, "foo.py"), "w").close()
            open(os.path.join(src, "test_foo.py"), "w").close()
            cfg = self._make_cfg(d, exclude_patterns=["test_*"])
            sources = discover_sources(cfg)
            assert len(sources) == 1
            assert "foo.py" in sources[0]

    def test_nested_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "src", "sub")
            os.makedirs(sub)
            open(os.path.join(sub, "deep.py"), "w").close()
            cfg = self._make_cfg(d)
            sources = discover_sources(cfg)
            assert len(sources) == 1
            assert "deep.py" in sources[0]

    def test_missing_source_dir_skipped(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._make_cfg(d, source_dirs=["nonexistent"])
            sources = discover_sources(cfg)
            assert sources == []

    def test_deduplication(self):
        """Overlapping source_dirs should not produce duplicates."""
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "src", "app")
            os.makedirs(sub)
            open(os.path.join(sub, "main.py"), "w").close()
            cfg = self._make_cfg(d, source_dirs=["src", "src/app"])
            sources = discover_sources(cfg)
            assert len(sources) == 1

    def test_sorted_output(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            for name in ["z.py", "a.py", "m.py"]:
                open(os.path.join(src, name), "w").close()
            cfg = self._make_cfg(d)
            sources = discover_sources(cfg)
            assert sources == sorted(sources)

    def test_exclude_by_relpath(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "src", "lang")
            os.makedirs(sub)
            open(os.path.join(sub, "python.py"), "w").close()
            open(os.path.join(sub, "cpp.py"), "w").close()
            cfg = self._make_cfg(d, exclude_patterns=["src/lang/python.py"])
            sources = discover_sources(cfg)
            assert len(sources) == 1
            assert "cpp.py" in sources[0]


# ── Source selection (--file) ──────────────────────────────────────────

class TestSelectSources:
    SOURCES = ["src/compress.py", "src/decompress.py", "src/lang/compress.py"]

    def test_no_filter_returns_everything(self):
        assert select_sources(self.SOURCES, None) == self.SOURCES

    def test_empty_filter_returns_everything(self):
        assert select_sources(self.SOURCES, "") == self.SOURCES

    def test_exact_relpath_beats_substring(self):
        assert select_sources(self.SOURCES, "src/compress.py") == ["src/compress.py"]

    def test_basename_beats_substring(self):
        assert select_sources(self.SOURCES, "compress.py") == [
            "src/compress.py",
            "src/lang/compress.py",
        ]

    def test_substring_used_when_nothing_matches_exactly(self):
        assert select_sources(self.SOURCES, "compress") == self.SOURCES

    def test_unmatched_filter_selects_nothing(self):
        assert select_sources(self.SOURCES, "nosuchfile.py") == []
