"""Tests for runner.py — commands, sandbox, caching, classification."""

import json
import os
import tempfile

import pytest

from cerebro.types import Mutation, MutationResult, KILLED, SURVIVED, BUILD_ERROR, TIMEOUT
import concurrent.futures
import multiprocessing

from cerebro.runner import (
    classify_outcome,
    inject_build_jobs,
    file_hash,
    load_cache,
    save_cache,
    create_sandboxes,
    run_command,
    _init_worker,
    _cleanup_sandboxes,
    _active_sandboxes,
    _smart_copy_tree,
)
from cerebro.config import Config


# ── run_command ─────────────────────────────────────────────────────────

class TestRunCommand:
    def test_success(self):
        rc, out = run_command("echo hello", "/tmp", timeout=10)
        assert rc == 0
        assert "hello" in out

    def test_failure(self):
        rc, out = run_command("exit 1", "/tmp", timeout=10)
        assert rc == 1

    def test_timeout(self):
        rc, out = run_command("sleep 60", "/tmp", timeout=1)
        assert rc == -1
        assert "Timed out" in out

    def test_env_override(self):
        rc, out = run_command("echo $CEREBRO_TEST_VAR", "/tmp", timeout=10, env={"CEREBRO_TEST_VAR": "xyz"})
        assert rc == 0
        assert "xyz" in out

    def test_cwd_respected(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = run_command("pwd", d, timeout=10)
            assert rc == 0
            assert d in out

    def test_output_capped(self):
        """Output is truncated to last 2000 chars."""
        rc, out = run_command("python3 -c \"print('x' * 5000)\"", "/tmp", timeout=10)
        assert rc == 0
        assert len(out) <= 2001  # 2000 + newline


# ── classify_outcome ───────────────────────────────────────────────────

class TestClassifyOutcome:
    def _mut(self):
        return Mutation("f.py", 1, 0, 1, "a", "b", "test_op", "test")

    def test_test_fails_is_killed(self):
        result = classify_outcome(self._mut(), "", "exit 1", "/tmp", 10, 10)
        assert result.status == KILLED

    def test_test_passes_is_survived(self):
        result = classify_outcome(self._mut(), "", "exit 0", "/tmp", 10, 10)
        assert result.status == SURVIVED

    def test_build_fails_is_build_error(self):
        result = classify_outcome(self._mut(), "exit 1", "exit 0", "/tmp", 10, 10)
        assert result.status == BUILD_ERROR

    def test_build_timeout_is_timeout(self):
        result = classify_outcome(self._mut(), "sleep 60", "exit 0", "/tmp", 1, 10)
        assert result.status == TIMEOUT
        assert "Build timed out" in result.detail

    def test_test_timeout_is_timeout(self):
        result = classify_outcome(self._mut(), "", "sleep 60", "/tmp", 10, 1)
        assert result.status == TIMEOUT
        assert "Test timed out" in result.detail

    def test_no_build_cmd_skips_build(self):
        """Empty build command should skip straight to test."""
        result = classify_outcome(self._mut(), "", "exit 1", "/tmp", 10, 10)
        assert result.status == KILLED

    def test_duration_positive(self):
        result = classify_outcome(self._mut(), "", "exit 0", "/tmp", 10, 10)
        assert result.duration_seconds >= 0

    def test_build_error_includes_detail(self):
        result = classify_outcome(self._mut(), "echo BUILD_BROKE && exit 1", "exit 0", "/tmp", 10, 10)
        assert result.status == BUILD_ERROR
        assert "BUILD_BROKE" in result.detail


# ── inject_build_jobs ──────────────────────────────────────────────────

class TestInjectBuildJobs:
    def test_make_gets_j(self):
        assert inject_build_jobs("make", 4) == "make -j4"

    def test_scons_gets_j(self):
        assert inject_build_jobs("scons", 2) == "scons -j2"

    def test_ninja_gets_j(self):
        assert inject_build_jobs("ninja", 8) == "ninja -j8"

    def test_already_has_j(self):
        assert inject_build_jobs("make -j2", 4) == "make -j2"

    def test_non_build_tool_unchanged(self):
        assert inject_build_jobs("gcc foo.c", 4) == "gcc foo.c"

    def test_empty_cmd(self):
        assert inject_build_jobs("", 4) == ""


# ── file_hash ──────────────────────────────────────────────────────────

class TestFileHash:
    def test_consistent(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("hello world")
            f.flush()
            h1 = file_hash(f.name)
            h2 = file_hash(f.name)
        os.unlink(f.name)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_different_content(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f1:
            f1.write("hello")
            f1.flush()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f2:
            f2.write("world")
            f2.flush()
        h1 = file_hash(f1.name)
        h2 = file_hash(f2.name)
        os.unlink(f1.name)
        os.unlink(f2.name)
        assert h1 != h2


# ── Cache ──────────────────────────────────────────────────────────────

class TestCache:
    def _make_cfg(self, tmpdir):
        return Config(
            language="python", source_dirs=["src"], source_globs=["*.py"],
            exclude_patterns=[], build_command="", test_command="pytest",
            test_timeout_seconds=30, build_timeout_seconds=120,
            preset="STANDARD", threshold=60, workers=0, build_jobs=2,
            build_tree="", cache_dir=".cache", report_dir=".reports",
            project_root=tmpdir, lang_plugin={},
        )

    def test_load_empty(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._make_cfg(d)
            cache = load_cache(cfg)
            assert cache["version"] == 1
            assert cache["preset"] == "STANDARD"
            assert cache["files"] == {}

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._make_cfg(d)
            cache = {"version": 1, "preset": "STANDARD", "files": {"test.py": {"hash": "abc", "mutations": []}}}
            save_cache(cfg, cache)
            loaded = load_cache(cfg)
            assert loaded["files"]["test.py"]["hash"] == "abc"

    def test_cache_invalidated_on_preset_change(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._make_cfg(d)
            cache = {"version": 1, "preset": "ALL", "files": {"x.py": {}}}
            save_cache(cfg, cache)
            loaded = load_cache(cfg)
            # cfg.preset is STANDARD, cache preset is ALL → invalidated
            assert loaded["files"] == {}

    def test_cache_invalidated_on_version_change(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._make_cfg(d)
            cache = {"version": 999, "preset": "STANDARD", "files": {"x.py": {}}}
            save_cache(cfg, cache)
            loaded = load_cache(cfg)
            assert loaded["files"] == {}

    def test_corrupt_cache_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._make_cfg(d)
            cache_dir = os.path.join(d, ".cache")
            os.makedirs(cache_dir)
            with open(os.path.join(cache_dir, "results.json"), "w") as f:
                f.write("not json")
            loaded = load_cache(cfg)
            assert loaded["files"] == {}


# ── Sandbox ────────────────────────────────────────────────────────────

class TestCreateSandboxes:
    def test_creates_n_sandboxes(self):
        with tempfile.TemporaryDirectory() as d:
            # Create some files in the "project"
            with open(os.path.join(d, "foo.py"), "w") as f:
                f.write("x = 1")
            sandboxes = create_sandboxes(d, "", 3)
            assert len(sandboxes) == 3
            for sb in sandboxes:
                assert os.path.isdir(sb)
                assert os.path.exists(os.path.join(sb, "foo.py"))
            _cleanup_sandboxes()

    def test_build_tree_symlinks_other_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            os.makedirs(os.path.join(d, "deps"))
            with open(os.path.join(d, "src", "main.py"), "w") as f:
                f.write("x = 1")
            with open(os.path.join(d, "deps", "lib.py"), "w") as f:
                f.write("y = 2")
            sandboxes = create_sandboxes(d, "src", 1)
            sb = sandboxes[0]
            # deps should be symlinked
            assert os.path.islink(os.path.join(sb, "deps"))
            # src should be copied (not symlinked)
            assert not os.path.islink(os.path.join(sb, "src"))
            assert os.path.exists(os.path.join(sb, "src", "main.py"))
            _cleanup_sandboxes()


class TestWholeRepoBuildTree:
    """`build_tree` values meaning "the whole repo" must copy, never symlink.

    A symlinked sandbox points every worker at the real tree, so concurrent
    mutations overwrite each other and the run reports phantom survivors.
    """

    WHOLE_REPO = ["", ".", "./"]

    def _project(self, d: str):
        os.makedirs(os.path.join(d, "src"))
        with open(os.path.join(d, "src", "main.py"), "w") as f:
            f.write("x = 1")

    @pytest.mark.parametrize("build_tree", WHOLE_REPO)
    def test_should_copy_sources_rather_than_symlink_them(self, build_tree):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            sb = create_sandboxes(d, build_tree, 1)[0]
            assert not os.path.islink(os.path.join(sb, "src"))
            _cleanup_sandboxes()

    @pytest.mark.parametrize("build_tree", WHOLE_REPO)
    def test_should_isolate_sandbox_writes_from_the_real_project(self, build_tree):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            sb = create_sandboxes(d, build_tree, 1)[0]
            with open(os.path.join(sb, "src", "main.py"), "w") as f:
                f.write("MUTATED")
            with open(os.path.join(d, "src", "main.py")) as f:
                assert f.read() == "x = 1"
            _cleanup_sandboxes()

    @pytest.mark.parametrize("build_tree", WHOLE_REPO)
    def test_should_give_each_worker_an_independent_copy(self, build_tree):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            first, second = create_sandboxes(d, build_tree, 2)
            with open(os.path.join(first, "src", "main.py"), "w") as f:
                f.write("MUTATED")
            with open(os.path.join(second, "src", "main.py")) as f:
                assert f.read() == "x = 1"
            _cleanup_sandboxes()

    def test_should_treat_a_dot_slash_prefixed_subtree_as_that_subtree(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            os.makedirs(os.path.join(d, "deps"))
            sb = create_sandboxes(d, "./src", 1)[0]
            assert not os.path.islink(os.path.join(sb, "src"))
            assert os.path.islink(os.path.join(sb, "deps"))
            _cleanup_sandboxes()


class TestSmartCopyTree:
    def test_copies_files(self):
        with tempfile.TemporaryDirectory() as src:
            with tempfile.TemporaryDirectory() as dst:
                with open(os.path.join(src, "f.txt"), "w") as f:
                    f.write("hi")
                _smart_copy_tree(src, dst, set(), set())
                assert os.path.exists(os.path.join(dst, "f.txt"))

    def test_symlinks_named_dirs(self):
        with tempfile.TemporaryDirectory() as src:
            with tempfile.TemporaryDirectory() as dst:
                os.makedirs(os.path.join(src, "extern"))
                with open(os.path.join(src, "extern", "lib.h"), "w") as f:
                    f.write("int x;")
                _smart_copy_tree(src, dst, {"extern"}, set())
                assert os.path.islink(os.path.join(dst, "extern"))

    def test_excludes_dirs(self):
        with tempfile.TemporaryDirectory() as src:
            with tempfile.TemporaryDirectory() as dst:
                os.makedirs(os.path.join(src, ".git"))
                with open(os.path.join(src, "f.txt"), "w") as f:
                    f.write("hi")
                _smart_copy_tree(src, dst, set(), {".git"})
                assert not os.path.exists(os.path.join(dst, ".git"))
                assert os.path.exists(os.path.join(dst, "f.txt"))


class TestSandboxExclude:
    def _project(self, d: str):
        os.makedirs(os.path.join(d, ".git"))
        os.makedirs(os.path.join(d, "tools"))
        with open(os.path.join(d, "tools", "big.bin"), "w") as f:
            f.write("payload")
        with open(os.path.join(d, "keep.py"), "w") as f:
            f.write("x = 1")
        with open(os.path.join(d, "rom.bin"), "w") as f:
            f.write("rom")

    def test_extra_exclude_omits_named_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            sb = create_sandboxes(d, "", 1, extra_exclude=["tools"])[0]
            assert not os.path.exists(os.path.join(sb, "tools"))
            assert os.path.exists(os.path.join(sb, "keep.py"))
            _cleanup_sandboxes()

    def test_extra_exclude_keeps_builtin_excludes(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            sb = create_sandboxes(d, "", 1, extra_exclude=["tools"])[0]
            assert not os.path.exists(os.path.join(sb, ".git"))
            _cleanup_sandboxes()

    def test_extra_exclude_accepts_globs(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            sb = create_sandboxes(d, "", 1, extra_exclude=["*.bin"])[0]
            assert not os.path.exists(os.path.join(sb, "rom.bin"))
            assert os.path.exists(os.path.join(sb, "keep.py"))
            _cleanup_sandboxes()

    def test_extra_exclude_applies_in_build_tree_mode(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            os.makedirs(os.path.join(d, "src"))
            with open(os.path.join(d, "src", "main.py"), "w") as f:
                f.write("x = 1")
            sb = create_sandboxes(d, "src", 1, extra_exclude=["tools"])[0]
            assert not os.path.exists(os.path.join(sb, "tools"))
            assert os.path.exists(os.path.join(sb, "src", "main.py"))
            _cleanup_sandboxes()

    def test_glob_exclude_applies_inside_copied_build_tree(self):
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, "src", "fixtures")
            os.makedirs(nested)
            with open(os.path.join(nested, "sample.bin"), "w") as f:
                f.write("blob")
            with open(os.path.join(d, "src", "main.py"), "w") as f:
                f.write("x = 1")
            sb = create_sandboxes(d, "src", 1, extra_exclude=["*.bin"])[0]
            assert not os.path.exists(os.path.join(sb, "src", "fixtures", "sample.bin"))
            assert os.path.exists(os.path.join(sb, "src", "main.py"))
            _cleanup_sandboxes()


# ── Worker/sandbox binding ─────────────────────────────────────────────

def _sandbox_probe(delay: float) -> bool:
    """Claim this process's sandbox exclusively; True if someone else held it."""
    import time

    from cerebro import runner as _r

    marker = os.path.join(_r._worker_sandbox, ".busy")
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return True
    try:
        time.sleep(delay)
    finally:
        os.close(fd)
        os.unlink(marker)
    return False


def _report_binding(_: int) -> tuple[int, str]:
    from cerebro import runner as _r

    return os.getpid(), _r._worker_sandbox


class TestWorkerSandboxBinding:
    WORKERS = 4

    def _pool(self, project: str):
        boxes = create_sandboxes(project, "", self.WORKERS)
        q = multiprocessing.Queue()
        for b in boxes:
            q.put(b)
        return concurrent.futures.ProcessPoolExecutor(
            max_workers=self.WORKERS, initializer=_init_worker, initargs=(q,)
        )

    def test_concurrent_tasks_never_share_a_sandbox(self):
        # One long task while the pool churns through short ones. Under
        # index-based assignment the churn wraps back onto the long task's
        # sandbox and clobbers the mutation it is still testing.
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "f.py"), "w").close()
            delays = [0.6] + [0.01] * 39
            with self._pool(d) as pool:
                collisions = list(pool.map(_sandbox_probe, delays))
            _cleanup_sandboxes()
            assert not any(collisions)

    def test_each_process_owns_one_distinct_sandbox(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "f.py"), "w").close()
            with self._pool(d) as pool:
                seen = dict(pool.map(_report_binding, range(40)))
            _cleanup_sandboxes()
            assert len(set(seen.values())) == len(seen)


class TestRunCommandEnvironment:
    ENV_PROBE = (
        "python3 -c \"import os; print(os.environ.get('PYTHONDONTWRITEBYTECODE'))\""
    )

    def test_disables_bytecode_writing_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            _, out = run_command(self.ENV_PROBE, d, 30)
            assert out.strip() == "1"

    def test_explicit_env_still_wins(self):
        with tempfile.TemporaryDirectory() as d:
            _, out = run_command(self.ENV_PROBE, d, 30, env={"PYTHONDONTWRITEBYTECODE": ""})
            assert out.strip() == ""
