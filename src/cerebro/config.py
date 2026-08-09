"""Configuration, language plugin discovery, and source file discovery."""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import multiprocessing
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cerebro.operators import OperatorDef, SHARED_OPERATORS


# ---------------------------------------------------------------------------
# Built-in C++ fallback
# ---------------------------------------------------------------------------

def builtin_cpp_code_masks(lines: list[str]) -> tuple[list[list[bool]], list[bool]]:
    """C/C++ code masking (built-in fallback). See cerebro.lang.cpp for the
    primary implementation with comments."""
    masks: list[list[bool]] = []
    skip_line: list[bool] = []
    in_block = False
    for line in lines:
        mask = [True] * len(line)
        stripped = line.lstrip()
        is_pp = stripped.startswith("#")
        skip_line.append(is_pp)
        if is_pp and not in_block:
            masks.append([False] * len(line))
            continue
        i = 0
        while i < len(line):
            if in_block:
                mask[i] = False
                if i + 1 < len(line) and line[i] == "*" and line[i + 1] == "/":
                    mask[i + 1] = False
                    in_block = False
                    i += 2
                    continue
                i += 1
                continue
            ch = line[i]
            if ch == "/" and i + 1 < len(line) and line[i + 1] == "*":
                mask[i] = mask[i + 1] = False
                in_block = True
                i += 2
                continue
            if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                for j in range(i, len(line)):
                    mask[j] = False
                break
            if ch in ('"', "'"):
                end_ch = ch
                mask[i] = False
                i += 1
                while i < len(line):
                    if line[i] == "\\":
                        mask[i] = False
                        i += 1
                        if i < len(line):
                            mask[i] = False
                            i += 1
                        continue
                    if line[i] == end_ch:
                        mask[i] = False
                        i += 1
                        break
                    mask[i] = False
                    i += 1
                continue
            i += 1
        masks.append(mask)
    return masks, skip_line


def _builtin_cpp_operators() -> dict[str, list[OperatorDef]]:
    """Build C++ operator presets using shared + C++-specific operators (fallback)."""
    cpp_standard_extra = [
        OperatorDef("log_and_to_or", "logical", r"&&", "||", "&& → ||"),
        OperatorDef("log_or_to_and", "logical", r"\|\|", "&&", "|| → &&"),
        OperatorDef("unary_neg_remove", "unary", r"!(?![!=])", "", "!x → x"),
        OperatorDef("inc_to_dec", "increment", r"\+\+", "--", "++ → --"),
        OperatorDef("dec_to_inc", "increment", r"(?<!-)--(?!>)", "++", "-- → ++"),
        OperatorDef(
            "void_call_remove", "void_call",
            r"^\s*(?:[a-zA-Z_]\w*(?:::|->|\.))*[a-zA-Z_]\w*\s*\([^)]*\)\s*;",
            ";", "foo(); → ;",
        ),
        OperatorDef(
            "return_nullptr", "return_value",
            r"return\s+(?!nullptr\b).+;", "return nullptr;",
            "return expr → return nullptr",
        ),
    ]
    cpp_stronger_extra = [
        OperatorDef("return_zero", "return_value", r"return\s+(?!0\s*;).+;", "return 0;", "return expr → return 0"),
        OperatorDef("cond_to_true", "condition", r"if\s*\((?!true\)).+?\)", "if (true)", "if(cond) → if(true)"),
        OperatorDef("cond_to_false", "condition", r"if\s*\((?!false\)).+?\)", "if (false)", "if(cond) → if(false)"),
        OperatorDef(
            "stmt_remove", "statement",
            r"^\s*(?:[a-zA-Z_]\w*(?:\.|->|::))*[a-zA-Z_]\w*\s*(?:\+\+|--|(?:[+\-*/%&|^]|<<|>>)?=.+)\s*;",
            ";", "statement → ;",
        ),
        OperatorDef("switch_break_remove", "switch", r"^\s*break\s*;", ";", "break; → ; (fallthrough)"),
        OperatorDef("switch_case_zero", "switch", r"case\s+(?!0\s*:)\w+\s*:", "case 0:", "case X: → case 0:"),
    ]
    cpp_all_extra = [
        OperatorDef("const_zero_to_one", "constant", r"\b0\b", "1", "0 → 1"),
        OperatorDef("const_one_to_zero", "constant", r"(?<!\.)(?<!\d)\b1\b(?!\.)", "0", "1 → 0"),
    ]
    standard = SHARED_OPERATORS["STANDARD"] + cpp_standard_extra
    stronger = standard + SHARED_OPERATORS["STRONGER_EXTRA"] + cpp_stronger_extra
    return {"STANDARD": standard, "STRONGER": stronger, "ALL": stronger + cpp_all_extra}


_BUILTIN_CPP: dict = {
    "name": "cpp",
    "source_globs": ["*.cpp", "*.cc", "*.cxx", "*.c"],
    "build_required": True,
    "compute_code_masks": builtin_cpp_code_masks,
    "operators": _builtin_cpp_operators(),
}


# ---------------------------------------------------------------------------
# Language plugin discovery
# ---------------------------------------------------------------------------


def _discover_languages() -> dict[str, dict]:
    """Find lang/*.py plugins in the cerebro package and return {name: LANGUAGE dict}."""
    lang_dir = Path(__file__).resolve().parent / "lang"
    langs: dict[str, dict] = {}
    if not lang_dir.is_dir():
        return langs
    for p in sorted(lang_dir.glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(p.stem, p)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            lang = getattr(mod, "LANGUAGE", None)
            if isinstance(lang, dict) and "name" in lang:
                langs[lang["name"]] = lang
        except Exception as exc:
            _warn(f"Failed to load language plugin {p.name}: {exc}")
    return langs


def load_language(name: str) -> dict:
    """Load a language plugin by name. Falls back to built-in C++ if no plugin found."""
    plugins = _discover_languages()
    if name in plugins:
        return plugins[name]
    if name == "cpp":
        return _BUILTIN_CPP
    available = sorted(set(list(plugins.keys()) + ["cpp"]))
    _die(f"Unknown language: {name!r}. Available: {', '.join(available)}")
    return {}  # unreachable, satisfies type checker


# Backward-compatible module-level presets (used by CLI before config is loaded)
OPERATOR_PRESETS = _BUILTIN_CPP["operators"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "language": "cpp",
    "source_dirs": [],
    "source_globs": [],
    "exclude_patterns": [],
    "build_command": "",
    "test_command": "",
    "test_timeout_seconds": 30,
    "build_timeout_seconds": 120,
    "preset": "STANDARD",
    "threshold": 60,
    "workers": 0,
    "build_jobs": 2,
    "build_tree": "",
    "cache_dir": ".cerebro_cache",
    "report_dir": ".cerebro_reports",
}


@dataclass
class Config:
    language: str
    source_dirs: list[str]
    source_globs: list[str]
    exclude_patterns: list[str]
    build_command: str
    test_command: str
    test_timeout_seconds: int
    build_timeout_seconds: int
    preset: str
    threshold: int
    workers: int
    build_jobs: int
    build_tree: str
    cache_dir: str
    report_dir: str
    project_root: str = ""  # resolved absolute path to config file's directory
    lang_plugin: dict = field(default_factory=dict)  # loaded language plugin

    @staticmethod
    def load(config_path: str) -> Config:
        path = Path(config_path).resolve()
        if not path.exists():
            _die(f"Config file not found: {path}")
        with open(path) as f:
            user = json.load(f)
        merged = {**DEFAULT_CONFIG, **user}
        cfg = Config(**{k: v for k, v in merged.items() if k in Config.__dataclass_fields__})
        cfg.project_root = str(path.parent)

        # Load language plugin
        cfg.lang_plugin = load_language(cfg.language)

        # Apply plugin defaults for source_globs if not set in config
        if not cfg.source_globs:
            cfg.source_globs = cfg.lang_plugin.get("source_globs", ["*"])

        # Validate operator preset against the active language
        lang_operators = cfg.lang_plugin.get("operators", OPERATOR_PRESETS)
        if cfg.preset not in lang_operators:
            _die(f"Unknown preset: {cfg.preset!r}. Choose from: {', '.join(lang_operators)}")

        build_required = cfg.lang_plugin.get("build_required", True)
        if build_required and not cfg.build_command:
            _die("build_command is required in config.")
        if not cfg.test_command:
            _die("test_command is required in config.")
        if not cfg.source_dirs:
            _die("source_dirs must list at least one directory.")
        return cfg

    def resolve(self, rel: str) -> str:
        """Resolve a path relative to the project root."""
        return str(Path(self.project_root, rel).resolve())

    def effective_workers(self, mutation_count: int) -> int:
        if self.workers == 1:
            return 1
        n = self.workers if self.workers > 0 else max(1, multiprocessing.cpu_count() // self.build_jobs)
        return min(n, mutation_count) if mutation_count > 0 else 1


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------


def discover_sources(cfg: Config) -> list[str]:
    """Find source files matching globs, excluding patterns."""
    sources: list[str] = []
    for src_dir in cfg.source_dirs:
        abs_dir = cfg.resolve(src_dir)
        if not os.path.isdir(abs_dir):
            _warn(f"Source directory not found, skipping: {src_dir}")
            continue
        for root, _dirs, files in os.walk(abs_dir):
            for fname in files:
                if not any(fnmatch.fnmatch(fname, g) for g in cfg.source_globs):
                    continue
                rel = os.path.relpath(os.path.join(root, fname), cfg.project_root)
                if any(fnmatch.fnmatch(rel, ep) or fnmatch.fnmatch(fname, ep) for ep in cfg.exclude_patterns):
                    continue
                sources.append(rel)
    # Deduplicate: overlapping source_dirs can discover the same file
    # multiple times via os.walk (e.g. "src" and "src/app" both walk "src/app").
    sources = list(dict.fromkeys(sources))
    sources.sort()
    return sources


def select_sources(sources: list[str], file_filter: str | None) -> list[str]:
    """Narrow discovered sources to a --file selection.

    An exact relative path or file name wins outright, so `--file compress.py`
    selects compress.py alone rather than dragging in decompress.py. Substring
    matching remains the fallback for partial names.
    """
    if not file_filter:
        return list(sources)
    exact = [s for s in sources if s == file_filter or os.path.basename(s) == file_filter]
    if exact:
        return exact
    return [s for s in sources if file_filter in s]


# ---------------------------------------------------------------------------
# Output helpers (local to avoid circular imports with cli.py)
# ---------------------------------------------------------------------------


def _warn(msg: str):
    print(f"WARNING: {msg}", file=sys.stderr, flush=True)


def _die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(2)
