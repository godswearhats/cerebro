"""Tests for apply_mutation."""

import pytest
from cerebro.mutation import apply_mutation
from cerebro.types import Mutation


class TestApplyMutation:
    def test_simple_replacement(self):
        content = "x + y\n"
        mut = Mutation("f.cpp", 1, 2, 3, "+", "-", "arith", "")
        assert apply_mutation(content, mut) == "x - y\n"

    def test_deletion(self):
        content = "!flag\n"
        mut = Mutation("f.cpp", 1, 0, 1, "!", "", "unary", "")
        assert apply_mutation(content, mut) == "flag\n"

    def test_longer_replacement(self):
        content = "a < b\n"
        mut = Mutation("f.cpp", 1, 2, 3, "<", "<=", "rel", "")
        assert apply_mutation(content, mut) == "a <= b\n"

    def test_multiline_targets_correct_line(self):
        content = "line1\nline2\nline3\n"
        mut = Mutation("f.cpp", 2, 0, 5, "line2", "XXXXX", "test", "")
        result = apply_mutation(content, mut)
        assert result == "line1\nXXXXX\nline3\n"

    def test_out_of_range_raises(self):
        content = "only one line\n"
        mut = Mutation("f.cpp", 5, 0, 1, "x", "y", "test", "")
        with pytest.raises(ValueError, match="out of range"):
            apply_mutation(content, mut)

    def test_replace_with_empty_leaves_line(self):
        """Replacing content with '' leaves the line (with newline) intact."""
        content = "  foo();\n  bar();\n"
        mut = Mutation("f.cpp", 1, 0, 8, "  foo();", ";", "void_call", "")
        result = apply_mutation(content, mut)
        assert result == ";\n  bar();\n"
