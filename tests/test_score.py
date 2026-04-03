"""Tests for compute_score."""

from cerebro.types import Mutation, MutationResult, KILLED, SURVIVED, BUILD_ERROR, TIMEOUT
from cerebro.report import compute_score


class TestComputeScore:
    def _mr(self, status: str) -> MutationResult:
        m = Mutation("f", 1, 0, 1, "x", "y", "op", "")
        return MutationResult(mutation=m, status=status)

    def test_all_killed(self):
        results = [self._mr(KILLED)] * 5
        score = compute_score(results)
        assert score["total"] == 5
        assert score["killed"] == 5
        assert score["survived"] == 0
        assert score["score"] == 100.0

    def test_mixed(self):
        results = [self._mr(KILLED)] * 3 + [self._mr(SURVIVED)] * 2
        score = compute_score(results)
        assert score["total"] == 5
        assert score["score"] == 60.0

    def test_empty(self):
        score = compute_score([])
        assert score["total"] == 0
        assert score["score"] == 0.0

    def test_build_errors_not_testable(self):
        results = [self._mr(KILLED), self._mr(BUILD_ERROR), self._mr(TIMEOUT)]
        score = compute_score(results)
        assert score["testable"] == 1
        assert score["score"] == 100.0

    def test_all_survived(self):
        results = [self._mr(SURVIVED)] * 4
        score = compute_score(results)
        assert score["score"] == 0.0
