"""Sandbox management, build/test execution, caching, and mutation runner."""

from __future__ import annotations

import atexit
import concurrent.futures
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from cerebro.types import Mutation, MutationResult, KILLED, SURVIVED, BUILD_ERROR, TIMEOUT
from cerebro.mutation import generate_mutations, apply_mutation
from cerebro.config import Config, discover_sources, select_sources, builtin_cpp_code_masks, OPERATOR_PRESETS


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

    Large read-only directories (godot-cpp, extern, godot_cpp) within the
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
                        _smart_copy_tree(src, dst, readonly_dirs, exclude_set)
                    else:
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
# Output helpers
# ---------------------------------------------------------------------------


def info(msg: str):
    print(msg, file=sys.stderr, flush=True)


def warn(msg: str):
    print(f"WARNING: {msg}", file=sys.stderr, flush=True)


def die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------


def run_mutations(cfg: Config, file_filter: str | None = None) -> list[MutationResult]:
    """Main execution: scan, (optionally cache-skip), build sandboxes, run mutations."""
    signal.signal(signal.SIGINT, _sigint_handler)

    lang_operators = cfg.lang_plugin.get("operators", OPERATOR_PRESETS)
    operators = lang_operators[cfg.preset]
    mask_fn = cfg.lang_plugin.get("compute_code_masks", builtin_cpp_code_masks)
    sources = select_sources(discover_sources(cfg), file_filter)
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
        # Sequential mode
        info(f"\nRunning {total_new} mutations (sequential)...\n")
        for i, mut in enumerate(all_mutations, 1):
            content = file_contents[mut.file]
            abs_path = cfg.resolve(mut.file)

            # Patch
            mutated = apply_mutation(content, mut)
            with open(abs_path, "w") as f:
                f.write(mutated)

            try:
                result = classify_outcome(
                    mut, build_cmd, cfg.test_command,
                    cfg.project_root, cfg.build_timeout_seconds, cfg.test_timeout_seconds,
                )
            finally:
                # Always restore
                with open(abs_path, "w") as f:
                    f.write(content)

            results.append(result)
            status_char = {"killed": "K", "survived": "S", "build_error": "E", "timeout": "T"}.get(result.status, "?")
            info(
                f"  [{i}/{total_new}] {status_char}  {mut.file}:{mut.line}:{mut.col}  "
                f"{mut.description}  ({result.duration_seconds:.1f}s)"
            )
    else:
        # Parallel mode
        sandboxes = create_sandboxes(cfg.project_root, cfg.build_tree, num_workers)
        info(f"\nRunning {total_new} mutations ({num_workers} workers)...\n")

        # Pre-validation build
        info("Validating baseline build...")
        rc, out = run_command(build_cmd, sandboxes[0], cfg.build_timeout_seconds)
        if rc != 0:
            die(f"Baseline build failed:\n{out}")
        info("Baseline build OK.\n")

        # Prepare work items
        work_items = []
        for mut in all_mutations:
            worker_idx = len(work_items) % num_workers
            work_items.append((
                sandboxes[worker_idx],
                mut.file,
                file_contents[mut.file],
                {
                    "file": mut.file,
                    "line": mut.line,
                    "col": mut.col,
                    "end_col": mut.end_col,
                    "original": mut.original,
                    "replacement": mut.replacement,
                    "operator": mut.operator,
                    "description": mut.description,
                },
                build_cmd,
                cfg.test_command,
                cfg.build_timeout_seconds,
                cfg.test_timeout_seconds,
            ))

        completed = 0
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as pool:
            future_to_idx = {pool.submit(_worker_execute_mutation, item): i for i, item in enumerate(work_items)}
            for future in concurrent.futures.as_completed(future_to_idx):
                completed += 1
                rd = future.result()
                mut = Mutation(
                    file=rd["file"], line=rd["line"], col=rd["col"], end_col=rd["end_col"],
                    original=rd["original"], replacement=rd["replacement"],
                    operator=rd["operator"], description=rd["description"],
                )
                result = MutationResult(
                    mutation=mut, status=rd["status"],
                    duration_seconds=rd["duration_seconds"], detail=rd.get("detail", ""),
                )
                results.append(result)
                status_char = {"killed": "K", "survived": "S", "build_error": "E", "timeout": "T"}.get(
                    result.status, "?"
                )
                info(
                    f"  [{completed}/{total_new}] {status_char}  {mut.file}:{mut.line}:{mut.col}  "
                    f"{mut.description}  ({result.duration_seconds:.1f}s)"
                )

        _cleanup_sandboxes()

    # --- Update cache ---
    for r in results:
        if r.mutation.file not in cache.get("files", {}):
            cache.setdefault("files", {})[r.mutation.file] = {
                "hash": file_hash(cfg.resolve(r.mutation.file)),
                "mutations": [],
            }

    # Group new results by file
    new_by_file: dict[str, list[MutationResult]] = {}
    for r in results:
        if r not in cached_results:
            new_by_file.setdefault(r.mutation.file, []).append(r)

    for src, file_results in new_by_file.items():
        cache["files"][src] = {
            "hash": file_hash(cfg.resolve(src)),
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
