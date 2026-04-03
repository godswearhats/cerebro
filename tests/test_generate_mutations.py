"""Tests for generate_mutations."""

from cerebro.mutation import generate_mutations
from conftest import make_op, mutate, all_mutable_mask


class TestGenerateMutations:
    def test_basic_operator_match(self):
        ops = [make_op("flip_bool", r"\btrue\b", "false")]
        muts = mutate("if (true) {", ops)
        assert len(muts) == 1
        assert muts[0].original == "true"
        assert muts[0].replacement == "false"
        assert muts[0].line == 1
        assert muts[0].col == 4

    def test_multiple_matches_per_line(self):
        ops = [make_op("flip_bool", r"\btrue\b", "false")]
        muts = mutate("return true || true;", ops)
        assert len(muts) == 2
        assert muts[0].col == 7
        assert muts[1].col == 15

    def test_multiple_lines(self):
        ops = [make_op("flip_bool", r"\btrue\b", "false")]
        muts = mutate("x = true;\ny = true;", ops)
        assert len(muts) == 2
        assert muts[0].line == 1
        assert muts[1].line == 2

    def test_dedup_same_position(self):
        """Two operators matching the same (file, line, col, replacement) -> one mutation."""
        ops = [
            make_op("op_a", r"\btrue\b", "false"),
            make_op("op_b", r"\btrue\b", "false"),
        ]
        muts = mutate("return true;", ops)
        assert len(muts) == 1

    def test_different_replacement_not_deduped(self):
        """Same position, different replacements -> two mutations."""
        ops = [
            make_op("op_a", r"\btrue\b", "false"),
            make_op("op_b", r"\btrue\b", "0"),
        ]
        muts = mutate("return true;", ops)
        assert len(muts) == 2

    def test_mask_blocks_match(self):
        """Matches inside masked regions (comments/strings) are skipped."""
        ops = [make_op("flip_bool", r"\btrue\b", "false")]
        content = "// true\nreal_true = true;"

        def mask_with_comment(lines):
            masks = []
            skip = []
            for i, line in enumerate(lines):
                if line.startswith("//"):
                    masks.append([False] * len(line))
                    skip.append(True)
                else:
                    masks.append([True] * len(line))
                    skip.append(False)
            return masks, skip

        muts = generate_mutations("f.cpp", content, ops, mask_with_comment)
        assert len(muts) == 1
        assert muts[0].line == 2

    def test_no_matches_returns_empty(self):
        ops = [make_op("flip_bool", r"\btrue\b", "false")]
        muts = mutate("x = 42;", ops)
        assert muts == []
