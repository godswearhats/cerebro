#!/usr/bin/env python3
"""cerebro — Multi-language source-level mutation testing.

A reusable, zero-dependency mutation testing tool that patches source files,
optionally recompiles, runs tests, and reports which mutations survived.
Language support is provided by plugins (cerebro_lang_*.py).

Usage:
    python cerebro.py run [--config cerebro.json] [--workers N] [--file path]
    python cerebro.py dry-run [--config cerebro.json]
    python cerebro.py list-operators [--preset STANDARD]
    python cerebro.py report [--config cerebro.json] [--format text|json|html]
"""

from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import fnmatch
import hashlib
import html as html_module
import importlib.util
import json
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

KILLED = "killed"
SURVIVED = "survived"
BUILD_ERROR = "build_error"
TIMEOUT = "timeout"
SKIPPED = "skipped"

# ---------------------------------------------------------------------------
# Mutation operator definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorDef:
    """A single mutation operator."""

    name: str
    category: str
    pattern: str
    replacement: str
    description: str


# ---------------------------------------------------------------------------
# Shared mutation operators (language-agnostic)
#
# These are used by language plugins to compose their preset tiers.
# Plugins import SHARED_OPERATORS and combine with language-specific extras.
# ---------------------------------------------------------------------------

SHARED_OPERATORS: dict[str, list[OperatorDef]] = {
    "STANDARD": [
        # --- Relational ---
        OperatorDef("rel_lt_to_lte", "relational", r"(?<![<\-])< (?![<=<])", "<=", "< → <="),
        OperatorDef("rel_lte_to_lt", "relational", r"(?<![<])<=", "<", "<= → <"),
        OperatorDef("rel_gt_to_gte", "relational", r"(?<![>\-=])>(?![>=])", ">=", "> → >="),
        OperatorDef("rel_gte_to_gt", "relational", r"(?<![>])>=", ">", ">= → >"),
        OperatorDef("rel_eq_to_neq", "relational", r"(?<![!<>=])== ", "!=", "== → !="),
        OperatorDef("rel_neq_to_eq", "relational", r"(?<![!])!=(?!=)", "==", "!= → =="),
        # --- Arithmetic ---
        OperatorDef("arith_add_to_sub", "arithmetic", r"(?<![+]) *\+(?![+=])", "-", "+ → -"),
        OperatorDef("arith_sub_to_add", "arithmetic", r"(?<![->]) *\-(?![->=-])", "+", "- → +"),
        OperatorDef("arith_mul_to_div", "arithmetic", r"(?<![/]) *\*(?![*=])", "/", "* → /"),
        OperatorDef("arith_div_to_mul", "arithmetic", r"(?<![*/]) */(?![/=*])", "*", "/ → *"),
        # --- Boolean literal ---
        OperatorDef("bool_true_to_false", "boolean", r"\btrue\b", "false", "true → false"),
        OperatorDef("bool_false_to_true", "boolean", r"\bfalse\b", "true", "false → true"),
        # --- Invert negatives ---
        OperatorDef("unary_minus_remove", "unary", r"(?<![a-zA-Z_0-9)\]])-(?=[a-zA-Z_\d(])", "", "-x → x"),
    ],
    "STRONGER_EXTRA": [
        # --- Assignment operators ---
        OperatorDef("assign_add_to_sub", "assignment", r"\+=", "-=", "+= → -="),
        OperatorDef("assign_sub_to_add", "assignment", r"(?<![->])-=", "+=", "-= → +="),
        OperatorDef("assign_mul_to_div", "assignment", r"\*=", "/=", "*= → /="),
        OperatorDef("assign_div_to_mul", "assignment", r"/=", "*=", "/= → *="),
    ],
}


# ---------------------------------------------------------------------------
# Language plugin discovery
# ---------------------------------------------------------------------------

# Built-in C++ fallback — used when no cerebro_lang_cpp.py plugin is found.
# This keeps cerebro.py fully functional as a standalone file.

def _builtin_cpp_code_masks(lines: list[str]) -> tuple[list[list[bool]], list[bool]]:
    """C/C++ code masking (built-in fallback). See cerebro_lang_cpp.py for the
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
        # Void call removal
        OperatorDef(
            "void_call_remove", "void_call",
            r"^\s*(?:[a-zA-Z_]\w*(?:::|->|\.))*[a-zA-Z_]\w*\s*\([^)]*\)\s*;",
            ";", "foo(); → ;",
        ),
        # Null return (C++ uses nullptr)
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
        # Statement removal
        OperatorDef(
            "stmt_remove", "statement",
            r"^\s*(?:[a-zA-Z_]\w*(?:\.|->|::))*[a-zA-Z_]\w*\s*(?:\+\+|--|(?:[+\-*/%&|^]|<<|>>)?=.+)\s*;",
            ";", "statement → ;",
        ),
        # Switch: break removal (creates fallthrough)
        OperatorDef("switch_break_remove", "switch", r"^\s*break\s*;", ";", "break; → ; (fallthrough)"),
        # Switch: case value mutation
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
    "compute_code_masks": _builtin_cpp_code_masks,
    "operators": _builtin_cpp_operators(),
}


def _discover_languages() -> dict[str, dict]:
    """Find cerebro_lang_*.py plugins next to this file and return {name: LANGUAGE dict}."""
    here = Path(__file__).resolve().parent
    langs: dict[str, dict] = {}
    for p in sorted(here.glob("cerebro_lang_*.py")):
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
            warn(f"Failed to load language plugin {p.name}: {exc}")
    return langs


def load_language(name: str) -> dict:
    """Load a language plugin by name. Falls back to built-in C++ if no plugin found."""
    plugins = _discover_languages()
    if name in plugins:
        return plugins[name]
    if name == "cpp":
        return _BUILTIN_CPP
    available = sorted(set(list(plugins.keys()) + ["cpp"]))
    die(f"Unknown language: {name!r}. Available: {', '.join(available)}")
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
            die(f"Config file not found: {path}")
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
            die(f"Unknown preset: {cfg.preset!r}. Choose from: {', '.join(lang_operators)}")

        build_required = cfg.lang_plugin.get("build_required", True)
        if build_required and not cfg.build_command:
            die("build_command is required in config.")
        if not cfg.test_command:
            die("test_command is required in config.")
        if not cfg.source_dirs:
            die("source_dirs must list at least one directory.")
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
            warn(f"Source directory not found, skipping: {src_dir}")
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


# ---------------------------------------------------------------------------
# Line classifier & code mask
# ---------------------------------------------------------------------------


## Code masking is provided by language plugins via the compute_code_masks callable.
## See cerebro_lang_cpp.py / cerebro_lang_gdscript.py for implementations.


# ---------------------------------------------------------------------------
# Mutation data structures
# ---------------------------------------------------------------------------


@dataclass
class Mutation:
    file: str
    line: int  # 1-based
    col: int  # 0-based
    end_col: int  # 0-based, exclusive
    original: str
    replacement: str
    operator: str
    description: str


@dataclass
class MutationResult:
    mutation: Mutation
    status: str  # KILLED, SURVIVED, BUILD_ERROR, TIMEOUT, SKIPPED
    duration_seconds: float = 0.0
    detail: str = ""


# ---------------------------------------------------------------------------
# Mutation generator
# ---------------------------------------------------------------------------


def generate_mutations(
    file_path: str,
    content: str,
    operators: list[OperatorDef],
    mask_fn: callable = None,
) -> list[Mutation]:
    """Scan a source file and produce all candidate mutations."""
    lines = content.splitlines()
    if mask_fn is None:
        mask_fn = _builtin_cpp_code_masks
    masks, is_pp = mask_fn(lines)
    mutations: list[Mutation] = []

    for line_idx, line in enumerate(lines):
        if is_pp[line_idx]:
            continue
        mask = masks[line_idx]

        for op in operators:
            for m in re.finditer(op.pattern, line):
                start, end = m.start(), m.end()
                # Every matched character must be in mutable code
                if start >= len(mask) or end > len(mask):
                    continue
                if not all(mask[i] for i in range(start, end)):
                    continue

                mutations.append(
                    Mutation(
                        file=file_path,
                        line=line_idx + 1,
                        col=start,
                        end_col=end,
                        original=m.group(),
                        replacement=op.replacement,
                        operator=op.name,
                        description=op.description,
                    )
                )

    # Deduplicate by (file, line, col, replacement)
    seen: set[tuple[str, int, int, str]] = set()
    unique: list[Mutation] = []
    for mut in mutations:
        key = (mut.file, mut.line, mut.col, mut.replacement)
        if key not in seen:
            seen.add(key)
            unique.append(mut)

    return unique


# ---------------------------------------------------------------------------
# File patcher
# ---------------------------------------------------------------------------


def apply_mutation(content: str, mutation: Mutation) -> str:
    """Apply a single mutation to file content and return the modified content."""
    lines = content.splitlines(keepends=True)
    line_idx = mutation.line - 1
    if line_idx < 0 or line_idx >= len(lines):
        raise ValueError(f"Line {mutation.line} out of range for file with {len(lines)} lines")

    line = lines[line_idx]
    new_line = line[: mutation.col] + mutation.replacement + line[mutation.end_col :]
    lines[line_idx] = new_line
    return "".join(lines)


# ---------------------------------------------------------------------------
# Build & test runner
# ---------------------------------------------------------------------------


def run_command(command: str, cwd: str, timeout: int, env: dict | None = None) -> tuple[int, str]:
    """Run a shell command, return (exit_code, combined output). -1 on timeout."""
    merged_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=merged_env,
        )
        return proc.returncode, (proc.stdout + proc.stderr)[-2000:]  # cap output size
    except subprocess.TimeoutExpired:
        return -1, "Timed out"


def classify_outcome(
    mutation: Mutation,
    build_cmd: str,
    test_cmd: str,
    cwd: str,
    build_timeout: int,
    test_timeout: int,
) -> MutationResult:
    """Build and test a single mutation, returning the classified result."""
    t0 = time.monotonic()

    # Build (skip for interpreted languages with no build command)
    if build_cmd:
        rc, output = run_command(build_cmd, cwd, build_timeout)
        if rc != 0:
            status = TIMEOUT if rc == -1 else BUILD_ERROR
            return MutationResult(
                mutation=mutation,
                status=status,
                duration_seconds=time.monotonic() - t0,
                detail=output[:500] if status == BUILD_ERROR else "Build timed out",
            )

    # Test
    rc, output = run_command(test_cmd, cwd, test_timeout)
    elapsed = time.monotonic() - t0
    if rc == -1:
        return MutationResult(mutation=mutation, status=TIMEOUT, duration_seconds=elapsed, detail="Test timed out")
    if rc != 0:
        return MutationResult(mutation=mutation, status=KILLED, duration_seconds=elapsed)
    return MutationResult(mutation=mutation, status=SURVIVED, duration_seconds=elapsed)


# ---------------------------------------------------------------------------
# Sandbox manager
# ---------------------------------------------------------------------------

_active_sandboxes: list[str] = []


def _cleanup_sandboxes():
    """Remove all sandbox directories. Called by atexit and signal handlers."""
    for d in _active_sandboxes:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    _active_sandboxes.clear()


atexit.register(_cleanup_sandboxes)


def _sigint_handler(signum, frame):
    _cleanup_sandboxes()
    sys.exit(130)


def _smart_copy_tree(src_dir: str, dst_dir: str, symlink_names: set[str], exclude: set[str]):
    """Recursively copy a directory tree, symlinking named subdirectories at any depth.

    This significantly reduces sandbox creation time and disk usage for large
    read-only dependencies (e.g., godot-cpp/, extern/).
    """
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(src_dir):
        if entry in exclude:
            continue
        src = os.path.join(src_dir, entry)
        dst = os.path.join(dst_dir, entry)
        if entry in symlink_names and os.path.isdir(src):
            os.symlink(src, dst)
        elif os.path.isdir(src):
            # Recurse so we can symlink matching dirs at deeper levels
            _smart_copy_tree(src, dst, symlink_names, exclude)
        else:
            shutil.copy2(src, dst)


def create_sandboxes(
    project_root: str,
    build_tree: str,
    num_workers: int,
    exclude: list[str] | None = None,
) -> list[str]:
    """Create N sandbox copies of the project tree for parallel mutation testing.

    If build_tree is set, only that subtree is copied; everything else at the
    project root level is symlinked.  If build_tree is empty, the entire project
    root is copied (minus excluded patterns).

    Large read-only directories (godot-cpp, extern, godot-cpp/gen) within the
    build tree are symlinked rather than copied to save time and disk.
    """
    exclude_set = set(exclude or [".git", ".cerebro_cache", ".cerebro_reports", "__pycache__"])
    # Large read-only dirs that can be symlinked inside the build tree
    readonly_dirs = {"godot-cpp", "extern", "godot_cpp"}
    sandboxes: list[str] = []

    for i in range(num_workers):
        sandbox = tempfile.mkdtemp(prefix=f"cerebro_w{i}_")
        root_mirror = os.path.join(sandbox, "root")
        os.makedirs(root_mirror)

        if build_tree:
            build_tree_top = build_tree.split("/")[0] if "/" in build_tree else build_tree
            for entry in os.listdir(project_root):
                if entry in exclude_set:
                    continue
                src = os.path.join(project_root, entry)
                dst = os.path.join(root_mirror, entry)
                if entry == build_tree_top:
                    # Walk the build tree, symlinking read-only deps
                    if build_tree_top == build_tree:
                        # build_tree is a single directory (e.g., "client")
                        _smart_copy_tree(src, dst, readonly_dirs, exclude_set)
                    else:
                        # build_tree is nested (e.g., "client/engine")
                        # Copy the top level, then handle the nested part specially
                        _smart_copy_tree(src, dst, readonly_dirs, exclude_set)
                else:
                    os.symlink(src, dst)
        else:
            shutil.copytree(
                project_root,
                root_mirror,
                symlinks=True,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*exclude_set),
            )

        sandboxes.append(root_mirror)
        _active_sandboxes.append(sandbox)

    return sandboxes


# ---------------------------------------------------------------------------
# Worker function (runs in subprocess via ProcessPoolExecutor)
# ---------------------------------------------------------------------------


def _worker_execute_mutation(args: tuple) -> dict:
    """Execute a single mutation in a sandbox. Returns a serializable result dict."""
    (
        sandbox_root,
        file_rel,
        original_content,
        mutation_dict,
        build_cmd,
        test_cmd,
        build_timeout,
        test_timeout,
    ) = args

    mutation = Mutation(**mutation_dict)
    file_path = os.path.join(sandbox_root, file_rel)

    try:
        # Apply mutation
        mutated = apply_mutation(original_content, mutation)
        with open(file_path, "w") as f:
            f.write(mutated)

        # Build & test
        result = classify_outcome(mutation, build_cmd, test_cmd, sandbox_root, build_timeout, test_timeout)

        # Restore original
        with open(file_path, "w") as f:
            f.write(original_content)

        return {
            "file": result.mutation.file,
            "line": result.mutation.line,
            "col": result.mutation.col,
            "end_col": result.mutation.end_col,
            "original": result.mutation.original,
            "replacement": result.mutation.replacement,
            "operator": result.mutation.operator,
            "description": result.mutation.description,
            "status": result.status,
            "duration_seconds": result.duration_seconds,
            "detail": result.detail,
        }
    except Exception as exc:
        # Restore original on any error
        try:
            with open(file_path, "w") as f:
                f.write(original_content)
        except Exception:
            pass
        return {
            "file": mutation.file,
            "line": mutation.line,
            "col": mutation.col,
            "end_col": mutation.end_col,
            "original": mutation.original,
            "replacement": mutation.replacement,
            "operator": mutation.operator,
            "description": mutation.description,
            "status": BUILD_ERROR,
            "duration_seconds": 0.0,
            "detail": str(exc),
        }


# ---------------------------------------------------------------------------
# Inject -j flag into build commands
# ---------------------------------------------------------------------------


def inject_build_jobs(build_cmd: str, jobs: int) -> str:
    """If the build command uses scons/make and doesn't have -j, append it."""
    if "-j" in build_cmd:
        return build_cmd
    if any(tool in build_cmd for tool in ("scons", "make", "ninja")):
        return f"{build_cmd} -j{jobs}"
    return build_cmd


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

CACHE_VERSION = 1


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def load_cache(cfg: Config) -> dict:
    cache_path = os.path.join(cfg.resolve(cfg.cache_dir), "results.json")
    if not os.path.exists(cache_path):
        return {"version": CACHE_VERSION, "preset": cfg.preset, "files": {}}
    try:
        with open(cache_path) as f:
            data = json.load(f)
        if data.get("version") != CACHE_VERSION or data.get("preset") != cfg.preset:
            info("Cache invalidated (version or preset changed).")
            return {"version": CACHE_VERSION, "preset": cfg.preset, "files": {}}
        return data
    except (json.JSONDecodeError, KeyError):
        return {"version": CACHE_VERSION, "preset": cfg.preset, "files": {}}


def save_cache(cfg: Config, cache: dict):
    cache_dir = cfg.resolve(cfg.cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "results.json")
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------


def run_mutations(cfg: Config, file_filter: str | None = None) -> list[MutationResult]:
    """Main execution: scan, (optionally cache-skip), build sandboxes, run mutations."""
    signal.signal(signal.SIGINT, _sigint_handler)

    lang_operators = cfg.lang_plugin.get("operators", OPERATOR_PRESETS)
    operators = lang_operators[cfg.preset]
    mask_fn = cfg.lang_plugin.get("compute_code_masks", _builtin_cpp_code_masks)
    sources = discover_sources(cfg)
    if file_filter:
        sources = [s for s in sources if file_filter in s]
    if not sources:
        die("No source files found.")

    info(f"Found {len(sources)} source file(s).")

    # --- Scan phase ---
    cache = load_cache(cfg)
    all_mutations: list[Mutation] = []
    file_contents: dict[str, str] = {}
    cached_results: list[MutationResult] = []

    for src in sources:
        abs_path = cfg.resolve(src)
        with open(abs_path) as f:
            content = f.read()
        file_contents[src] = content
        fhash = file_hash(abs_path)

        # Check cache
        cached_file = cache.get("files", {}).get(src, {})
        if cached_file.get("hash") == fhash:
            for cm in cached_file.get("mutations", []):
                mut = Mutation(
                    file=src,
                    line=cm["line"],
                    col=cm["col"],
                    end_col=cm.get("end_col", cm["col"] + len(cm.get("original", ""))),
                    original=cm.get("original", ""),
                    replacement=cm.get("replacement", ""),
                    operator=cm["op"],
                    description=cm.get("description", ""),
                )
                cached_results.append(
                    MutationResult(mutation=mut, status=cm["status"], duration_seconds=cm.get("duration", 0.0))
                )
            info(f"  {src}: {len(cached_file.get('mutations', []))} mutations (cached)")
            continue

        mutations = generate_mutations(src, content, operators, mask_fn)
        all_mutations.extend(mutations)
        info(f"  {src}: {len(mutations)} mutations")

    total_new = len(all_mutations)
    total_cached = len(cached_results)
    info(f"\nTotal: {total_new} new mutations to test, {total_cached} cached.")

    if total_new == 0:
        info("Nothing new to test.")
        return cached_results

    # --- Sandbox + execution phase ---
    num_workers = cfg.effective_workers(total_new)
    build_cmd = inject_build_jobs(cfg.build_command, cfg.build_jobs)

    results: list[MutationResult] = list(cached_results)

    if num_workers == 1:
        # Sequential mode: mutate in place with backup/restore
        info(f"\nRunning {total_new} mutations sequentially...")
        for idx, mutation in enumerate(all_mutations):
            abs_path = cfg.resolve(mutation.file)
            original = file_contents[mutation.file]
            progress = f"[{idx + 1}/{total_new}]"

            try:
                mutated = apply_mutation(original, mutation)
                with open(abs_path, "w") as f:
                    f.write(mutated)

                result = classify_outcome(
                    mutation, build_cmd, cfg.test_command, cfg.project_root,
                    cfg.build_timeout_seconds, cfg.test_timeout_seconds,
                )
                results.append(result)
                status_char = {"killed": "✗", "survived": "✓", "build_error": "B", "timeout": "T"}
                print(
                    f"  {progress} {status_char.get(result.status, '?')} {mutation.file}:{mutation.line} "
                    f"{mutation.description} [{result.duration_seconds:.1f}s]",
                    flush=True,
                )
            finally:
                with open(abs_path, "w") as f:
                    f.write(original)
    else:
        # Parallel mode: create sandboxes
        info(f"\nCreating {num_workers} worker sandbox(es)...")
        sandboxes = create_sandboxes(cfg.project_root, cfg.build_tree, num_workers)
        # Warm up each sandbox with a no-op build so the build system rebuilds
        # its dependency database. Skip for interpreted languages (no build step).
        if build_cmd:
            info(f"Warming up {num_workers} sandbox(es) (parallel no-op builds)...")
            warmup_t0 = time.monotonic()
            with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as warmup_pool:
                warmup_futures = [
                    warmup_pool.submit(run_command, build_cmd, sb, cfg.build_timeout_seconds)
                    for sb in sandboxes
                ]
                for i, fut in enumerate(concurrent.futures.as_completed(warmup_futures)):
                    rc, _ = fut.result()
                    if rc != 0:
                        warn(f"Sandbox {i} warmup build failed (exit {rc}), mutations may be slow.")
            warmup_elapsed = time.monotonic() - warmup_t0
            info(f"Warmup done in {warmup_elapsed:.0f}s.")
        info(f"Running {total_new} mutations across {num_workers} workers...\n")

        # Build work queue: round-robin assign mutations to sandboxes
        work_items: list[tuple] = []
        for idx, mutation in enumerate(all_mutations):
            sandbox = sandboxes[idx % num_workers]
            work_items.append((
                sandbox,
                mutation.file,
                file_contents[mutation.file],
                {
                    "file": mutation.file,
                    "line": mutation.line,
                    "col": mutation.col,
                    "end_col": mutation.end_col,
                    "original": mutation.original,
                    "replacement": mutation.replacement,
                    "operator": mutation.operator,
                    "description": mutation.description,
                },
                build_cmd,
                cfg.test_command,
                cfg.build_timeout_seconds,
                cfg.test_timeout_seconds,
            ))

        completed = 0
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {executor.submit(_worker_execute_mutation, wi): wi for wi in work_items}
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                try:
                    rd = future.result()
                    mut = Mutation(
                        file=rd["file"],
                        line=rd["line"],
                        col=rd["col"],
                        end_col=rd["end_col"],
                        original=rd["original"],
                        replacement=rd["replacement"],
                        operator=rd["operator"],
                        description=rd["description"],
                    )
                    mr = MutationResult(
                        mutation=mut,
                        status=rd["status"],
                        duration_seconds=rd["duration_seconds"],
                        detail=rd.get("detail", ""),
                    )
                    results.append(mr)
                    status_char = {"killed": "✗", "survived": "✓", "build_error": "B", "timeout": "T"}
                    print(
                        f"  [{completed}/{total_new}] {status_char.get(mr.status, '?')} "
                        f"{mr.mutation.file}:{mr.mutation.line} "
                        f"{mr.mutation.description} [{mr.duration_seconds:.1f}s]",
                        flush=True,
                    )
                except Exception as exc:
                    warn(f"Worker error: {exc}")

        # Cleanup sandboxes
        _cleanup_sandboxes()

    # --- Update cache ---
    for src in sources:
        abs_path = cfg.resolve(src)
        if not os.path.exists(abs_path):
            continue
        fhash = file_hash(abs_path)
        file_results = [r for r in results if r.mutation.file == src]
        cache.setdefault("files", {})[src] = {
            "hash": fhash,
            "mutations": [
                {
                    "line": r.mutation.line,
                    "col": r.mutation.col,
                    "end_col": r.mutation.end_col,
                    "original": r.mutation.original,
                    "replacement": r.mutation.replacement,
                    "op": r.mutation.operator,
                    "description": r.mutation.description,
                    "status": r.status,
                    "duration": r.duration_seconds,
                }
                for r in file_results
            ],
        }
    save_cache(cfg, cache)

    return results


# ---------------------------------------------------------------------------
# Reporters
# ---------------------------------------------------------------------------


def compute_score(results: list[MutationResult]) -> dict:
    """Compute summary statistics from mutation results."""
    total = len(results)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    killed = by_status.get(KILLED, 0)
    survived = by_status.get(SURVIVED, 0)
    build_errors = by_status.get(BUILD_ERROR, 0)
    timeouts = by_status.get(TIMEOUT, 0)
    testable = killed + survived
    score = (killed / testable * 100) if testable > 0 else 0.0

    return {
        "total": total,
        "killed": killed,
        "survived": survived,
        "build_errors": build_errors,
        "timeouts": timeouts,
        "testable": testable,
        "score": score,
    }


def report_text(results: list[MutationResult], threshold: int) -> str:
    """Generate a terminal-friendly text report."""
    lines: list[str] = []

    # Per-file breakdown
    files: dict[str, list[MutationResult]] = {}
    for r in results:
        files.setdefault(r.mutation.file, []).append(r)

    lines.append("")
    lines.append("=" * 80)
    lines.append("MUTATION TESTING REPORT")
    lines.append("=" * 80)
    lines.append("")

    header = f"{'File':<45} {'Mut':>4} {'Kill':>4} {'Surv':>4} {'Err':>4} {'TO':>3} {'Score':>6}"
    lines.append(header)
    lines.append("-" * len(header))

    for fpath in sorted(files.keys()):
        frs = files[fpath]
        s = compute_score(frs)
        short = fpath if len(fpath) <= 44 else "..." + fpath[-41:]
        lines.append(
            f"{short:<45} {s['total']:>4} {s['killed']:>4} {s['survived']:>4} "
            f"{s['build_errors']:>4} {s['timeouts']:>3} {s['score']:>5.1f}%"
        )

    lines.append("-" * len(header))
    overall = compute_score(results)
    lines.append(
        f"{'TOTAL':<45} {overall['total']:>4} {overall['killed']:>4} {overall['survived']:>4} "
        f"{overall['build_errors']:>4} {overall['timeouts']:>3} {overall['score']:>5.1f}%"
    )
    lines.append("")

    # Survived mutants detail
    survived = [r for r in results if r.status == SURVIVED]
    if survived:
        lines.append(f"SURVIVED MUTANTS ({len(survived)}):")
        lines.append("-" * 60)
        for r in sorted(survived, key=lambda x: (x.mutation.file, x.mutation.line)):
            lines.append(
                f"  {r.mutation.file}:{r.mutation.line}:{r.mutation.col}  "
                f"{r.mutation.description}  ({r.mutation.operator})"
            )
        lines.append("")

    # Threshold check
    passed = overall["score"] >= threshold
    status = "PASS" if passed else "FAIL"
    lines.append(f"Mutation score: {overall['score']:.1f}% (threshold: {threshold}%)  [{status}]")
    lines.append("")

    return "\n".join(lines)


def report_json(results: list[MutationResult], cfg: Config) -> dict:
    """Generate a JSON-serializable report."""
    files: dict[str, list[dict]] = {}
    for r in results:
        entry = {
            "line": r.mutation.line,
            "col": r.mutation.col,
            "original": r.mutation.original,
            "replacement": r.mutation.replacement,
            "operator": r.mutation.operator,
            "description": r.mutation.description,
            "status": r.status,
            "duration_seconds": r.duration_seconds,
        }
        if r.detail:
            entry["detail"] = r.detail
        files.setdefault(r.mutation.file, []).append(entry)

    overall = compute_score(results)
    return {
        "preset": cfg.preset,
        "threshold": cfg.threshold,
        "summary": overall,
        "files": {k: sorted(v, key=lambda x: (x["line"], x["col"])) for k, v in sorted(files.items())},
    }


def report_html(results: list[MutationResult], cfg: Config) -> str:
    """Generate a self-contained HTML report with source annotations."""
    esc = html_module.escape
    overall = compute_score(results)

    # Group by file
    files: dict[str, list[MutationResult]] = {}
    for r in results:
        files.setdefault(r.mutation.file, []).append(r)

    # Read source files for annotation
    file_sources: dict[str, list[str]] = {}
    for fpath in files:
        abs_path = cfg.resolve(fpath)
        try:
            with open(abs_path) as f:
                file_sources[fpath] = f.read().splitlines()
        except FileNotFoundError:
            file_sources[fpath] = []

    parts: list[str] = []
    parts.append(textwrap.dedent("""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="utf-8">
    <title>cerebro Report</title>
    <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
           margin: 2em; background: #1a1a2e; color: #eee; }
    h1 { color: #e94560; }
    h2 { color: #0f3460; background: #16213e; padding: 0.5em; border-radius: 4px; color: #eee; }
    .summary { background: #16213e; padding: 1em; border-radius: 8px; margin-bottom: 2em; }
    .summary td { padding: 0.3em 1em; }
    .score { font-size: 2em; font-weight: bold; }
    .pass { color: #4ecca3; }
    .fail { color: #e94560; }
    table.files { border-collapse: collapse; width: 100%; margin: 1em 0; }
    table.files th, table.files td { padding: 0.4em 0.8em; text-align: left;
                                      border-bottom: 1px solid #333; }
    table.files th { background: #16213e; }
    .source { background: #0a0a1a; padding: 0; margin: 1em 0; border-radius: 4px;
              overflow-x: auto; font-size: 0.85em; }
    .source table { border-collapse: collapse; width: 100%; }
    .source td { padding: 0 0.8em; white-space: pre; font-family: monospace; }
    .source .ln { color: #555; text-align: right; user-select: none; width: 3em;
                  border-right: 1px solid #333; padding-right: 0.5em; }
    .source .killed { background: rgba(78, 204, 163, 0.15); }
    .source .survived { background: rgba(233, 69, 96, 0.2); }
    .source .both { background: rgba(233, 165, 69, 0.2); }
    .badge { display: inline-block; padding: 0.15em 0.5em; border-radius: 3px;
             font-size: 0.8em; margin: 0.1em; }
    .badge.killed { background: #4ecca3; color: #000; }
    .badge.survived { background: #e94560; color: #fff; }
    .badge.build_error { background: #666; color: #fff; }
    .badge.timeout { background: #f0a500; color: #000; }
    details { margin: 0.5em 0; }
    summary { cursor: pointer; color: #4ecca3; }
    </style>
    </head>
    <body>
    """))

    score_class = "pass" if overall["score"] >= cfg.threshold else "fail"
    parts.append(f'<h1>cerebro Report</h1>')
    parts.append(f'<div class="summary"><table>')
    parts.append(
        f'<tr><td>Mutation Score</td><td class="score {score_class}">{overall["score"]:.1f}%</td></tr>'
    )
    parts.append(f'<tr><td>Threshold</td><td>{cfg.threshold}%</td></tr>')
    parts.append(f'<tr><td>Total Mutants</td><td>{overall["total"]}</td></tr>')
    parts.append(f'<tr><td>Killed</td><td>{overall["killed"]}</td></tr>')
    parts.append(f'<tr><td>Survived</td><td>{overall["survived"]}</td></tr>')
    parts.append(f'<tr><td>Build Errors</td><td>{overall["build_errors"]}</td></tr>')
    parts.append(f'<tr><td>Timeouts</td><td>{overall["timeouts"]}</td></tr>')
    parts.append(f'<tr><td>Preset</td><td>{esc(cfg.preset)}</td></tr>')
    parts.append(f'</table></div>')

    # File summary table
    parts.append('<table class="files">')
    parts.append("<tr><th>File</th><th>Mutants</th><th>Killed</th><th>Survived</th><th>Score</th></tr>")
    for fpath in sorted(files.keys()):
        s = compute_score(files[fpath])
        sc = "pass" if s["score"] >= cfg.threshold else "fail"
        parts.append(
            f'<tr><td><a href="#{esc(fpath)}">{esc(fpath)}</a></td>'
            f'<td>{s["total"]}</td><td>{s["killed"]}</td><td>{s["survived"]}</td>'
            f'<td class="{sc}">{s["score"]:.1f}%</td></tr>'
        )
    parts.append("</table>")

    # Per-file source annotations
    for fpath in sorted(files.keys()):
        parts.append(f'<h2 id="{esc(fpath)}">{esc(fpath)}</h2>')
        source_lines = file_sources.get(fpath, [])
        frs = files[fpath]

        # Map line → mutations
        line_mutations: dict[int, list[MutationResult]] = {}
        for r in frs:
            line_mutations.setdefault(r.mutation.line, []).append(r)

        parts.append('<div class="source"><table>')
        for line_num, line_text in enumerate(source_lines, 1):
            muts = line_mutations.get(line_num, [])
            statuses = {m.status for m in muts}
            if SURVIVED in statuses and KILLED in statuses:
                cls = "both"
            elif SURVIVED in statuses:
                cls = "survived"
            elif KILLED in statuses:
                cls = "killed"
            else:
                cls = ""

            badges = ""
            if muts:
                badge_parts = []
                for m in sorted(muts, key=lambda x: x.mutation.col):
                    badge_parts.append(
                        f'<span class="badge {m.status}" title="{esc(m.mutation.description)}: '
                        f'{esc(m.mutation.original)} → {esc(m.mutation.replacement)}">'
                        f"{esc(m.status[0].upper())}</span>"
                    )
                badges = " " + "".join(badge_parts)

            parts.append(
                f'<tr class="{cls}"><td class="ln">{line_num}</td>'
                f"<td>{esc(line_text)}{badges}</td></tr>"
            )
        parts.append("</table></div>")

    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def info(msg: str):
    print(msg, file=sys.stderr, flush=True)


def warn(msg: str):
    print(f"WARNING: {msg}", file=sys.stderr, flush=True)


def die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_list_operators(args: argparse.Namespace):
    preset = args.preset or "STANDARD"
    # Load language plugin if config is provided, else use default C++ operators
    if args.config and os.path.exists(args.config):
        cfg = Config.load(args.config)
        lang_ops = cfg.lang_plugin.get("operators", OPERATOR_PRESETS)
        lang_name = cfg.language
    else:
        lang_ops = OPERATOR_PRESETS
        lang_name = "cpp"
    if preset not in lang_ops:
        die(f"Unknown preset: {preset!r}")
    ops = lang_ops[preset]
    print(f"\nMutation operators for {lang_name}/{preset} ({len(ops)} operators):\n")
    categories: dict[str, list[OperatorDef]] = {}
    for op in ops:
        categories.setdefault(op.category, []).append(op)
    for cat in sorted(categories):
        print(f"  {cat}:")
        for op in categories[cat]:
            print(f"    {op.name:<30} {op.description}")
        print()


def cmd_dry_run(args: argparse.Namespace):
    cfg = Config.load(args.config)
    if args.preset:
        cfg.preset = args.preset
    lang_operators = cfg.lang_plugin.get("operators", OPERATOR_PRESETS)
    operators = lang_operators[cfg.preset]
    mask_fn = cfg.lang_plugin.get("compute_code_masks", _builtin_cpp_code_masks)
    sources = discover_sources(cfg)
    if args.file:
        sources = [s for s in sources if args.file in s]
    if not sources:
        die("No source files found.")

    info(f"Preset: {cfg.preset} ({len(operators)} operators)")
    info(f"Found {len(sources)} source file(s).\n")

    total = 0
    for src in sources:
        abs_path = cfg.resolve(src)
        with open(abs_path) as f:
            content = f.read()
        mutations = generate_mutations(src, content, operators, mask_fn)
        total += len(mutations)
        print(f"{src}: {len(mutations)} mutations")
        for m in mutations:
            print(f"  L{m.line}:{m.col}  {m.description:<20}  {m.original!r} → {m.replacement!r}  ({m.operator})")
        print()

    print(f"Total: {total} mutations across {len(sources)} file(s).")
    print("(dry run — no mutations were applied)")


def cmd_run(args: argparse.Namespace):
    cfg = Config.load(args.config)
    if args.preset:
        cfg.preset = args.preset
    if args.threshold is not None:
        cfg.threshold = args.threshold
    if args.workers is not None:
        cfg.workers = args.workers

    info(f"cerebro — mutation testing")
    info(f"  Language:  {cfg.language}")
    info(f"  Preset:    {cfg.preset}")
    info(f"  Threshold: {cfg.threshold}%")
    if cfg.build_command:
        info(f"  Build:     {cfg.build_command}")
    info(f"  Test:      {cfg.test_command}")
    info("")

    results = run_mutations(cfg, file_filter=args.file)

    # Print text report
    text = report_text(results, cfg.threshold)
    print(text)

    # Write reports
    report_dir = cfg.resolve(cfg.report_dir)
    os.makedirs(report_dir, exist_ok=True)

    json_path = os.path.join(report_dir, "cerebro-report.json")
    with open(json_path, "w") as f:
        json.dump(report_json(results, cfg), f, indent=2)
    info(f"JSON report: {json_path}")

    html_path = os.path.join(report_dir, "cerebro-report.html")
    with open(html_path, "w") as f:
        f.write(report_html(results, cfg))
    info(f"HTML report: {html_path}")

    # Exit code
    overall = compute_score(results)
    if overall["score"] >= cfg.threshold:
        info(f"\nPASS: {overall['score']:.1f}% >= {cfg.threshold}%")
        sys.exit(0)
    else:
        info(f"\nFAIL: {overall['score']:.1f}% < {cfg.threshold}%")
        sys.exit(1)


def cmd_report(args: argparse.Namespace):
    cfg = Config.load(args.config)
    cache = load_cache(cfg)

    # Reconstruct results from cache
    results: list[MutationResult] = []
    for src, file_data in cache.get("files", {}).items():
        for cm in file_data.get("mutations", []):
            mut = Mutation(
                file=src,
                line=cm["line"],
                col=cm["col"],
                end_col=cm.get("end_col", cm["col"]),
                original=cm.get("original", ""),
                replacement=cm.get("replacement", ""),
                operator=cm["op"],
                description=cm.get("description", ""),
            )
            results.append(MutationResult(mutation=mut, status=cm["status"], duration_seconds=cm.get("duration", 0.0)))

    if not results:
        die("No cached results found. Run `cerebro.py run` first.")

    fmt = args.format or "text"
    if fmt == "text":
        print(report_text(results, cfg.threshold))
    elif fmt == "json":
        print(json.dumps(report_json(results, cfg), indent=2))
    elif fmt == "html":
        print(report_html(results, cfg))
    else:
        die(f"Unknown format: {fmt!r}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    preset_names = list(OPERATOR_PRESETS.keys())
    parser = argparse.ArgumentParser(
        prog="cerebro",
        description="Multi-language source-level mutation testing",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Run mutation testing")
    p_run.add_argument("--config", default="cerebro.json", help="Config file path (default: cerebro.json)")
    p_run.add_argument("--preset", choices=preset_names, help="Override preset")
    p_run.add_argument("--threshold", type=int, help="Override threshold percentage")
    p_run.add_argument("--workers", type=int, help="Number of parallel workers (0=auto, 1=sequential)")
    p_run.add_argument("--file", help="Only test files matching this substring")

    # dry-run
    p_dry = sub.add_parser("dry-run", help="List mutations without executing")
    p_dry.add_argument("--config", default="cerebro.json", help="Config file path")
    p_dry.add_argument("--preset", choices=preset_names, help="Override preset")
    p_dry.add_argument("--file", help="Only scan files matching this substring")

    # list-operators
    p_ops = sub.add_parser("list-operators", help="Show available mutation operators")
    p_ops.add_argument("--config", default=None, help="Config file path (for language-specific operators)")
    p_ops.add_argument("--preset", choices=preset_names, help="Preset to show (default: STANDARD)")

    # report
    p_rep = sub.add_parser("report", help="Regenerate report from cached results")
    p_rep.add_argument("--config", default="cerebro.json", help="Config file path")
    p_rep.add_argument("--format", choices=["text", "json", "html"], default="text", help="Output format")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "run": cmd_run,
        "dry-run": cmd_dry_run,
        "list-operators": cmd_list_operators,
        "report": cmd_report,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
