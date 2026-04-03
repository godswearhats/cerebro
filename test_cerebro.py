"""Unit tests for cerebro mutation testing engine."""

import re
import pytest
from cerebro import (
    OperatorDef,
    Mutation,
    MutationResult,
    SHARED_OPERATORS,
    KILLED,
    SURVIVED,
    BUILD_ERROR,
    TIMEOUT,
    generate_mutations,
    apply_mutation,
    compute_score,
    _builtin_cpp_code_masks,
)
from cerebro_lang_cpp import (
    compute_code_masks_cpp,
    LANGUAGE as CPP_LANGUAGE,
)
from cerebro_lang_typescript import (
    compute_code_masks_typescript,
    LANGUAGE as TS_LANGUAGE,
)
from cerebro_lang_gdscript import (
    compute_code_masks_gdscript,
    LANGUAGE as GDS_LANGUAGE,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _op(name: str, pattern: str, replacement: str) -> OperatorDef:
    return OperatorDef(name, "test", pattern, replacement, f"{name} desc")


def _find_ops(operators: list[OperatorDef], name: str) -> OperatorDef:
    """Find an operator by name in a list."""
    for op in operators:
        if op.name == name:
            return op
    raise ValueError(f"Operator {name!r} not found")


def _matches(op: OperatorDef, line: str) -> list[str]:
    """Return all substrings matched by an operator's pattern on a line."""
    return [m.group() for m in re.finditer(op.pattern, line)]


def _mutate(content: str, operators: list[OperatorDef], mask_fn=None) -> list[Mutation]:
    """Shorthand: generate mutations for a string treated as a single file."""
    if mask_fn is None:
        mask_fn = _all_mutable_mask
    return generate_mutations("test.cpp", content, operators, mask_fn)


def _all_mutable_mask(lines):
    """Mask that marks every character as mutable code (no comments/strings)."""
    masks = [[True] * len(line) for line in lines]
    skip = [False] * len(lines)
    return masks, skip


# ── apply_mutation ───────────────────────────────────────────────────────


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


# ── generate_mutations ──────────────────────────────────────────────────


class TestGenerateMutations:
    def test_basic_operator_match(self):
        ops = [_op("flip_bool", r"\btrue\b", "false")]
        muts = _mutate("if (true) {", ops)
        assert len(muts) == 1
        assert muts[0].original == "true"
        assert muts[0].replacement == "false"
        assert muts[0].line == 1
        assert muts[0].col == 4

    def test_multiple_matches_per_line(self):
        ops = [_op("flip_bool", r"\btrue\b", "false")]
        muts = _mutate("return true || true;", ops)
        assert len(muts) == 2
        assert muts[0].col == 7
        assert muts[1].col == 15

    def test_multiple_lines(self):
        ops = [_op("flip_bool", r"\btrue\b", "false")]
        muts = _mutate("x = true;\ny = true;", ops)
        assert len(muts) == 2
        assert muts[0].line == 1
        assert muts[1].line == 2

    def test_dedup_same_position(self):
        """Two operators matching the same (file, line, col, replacement) → one mutation."""
        ops = [
            _op("op_a", r"\btrue\b", "false"),
            _op("op_b", r"\btrue\b", "false"),
        ]
        muts = _mutate("return true;", ops)
        assert len(muts) == 1

    def test_different_replacement_not_deduped(self):
        """Same position, different replacements → two mutations."""
        ops = [
            _op("op_a", r"\btrue\b", "false"),
            _op("op_b", r"\btrue\b", "0"),
        ]
        muts = _mutate("return true;", ops)
        assert len(muts) == 2

    def test_mask_blocks_match(self):
        """Matches inside masked regions (comments/strings) are skipped."""
        ops = [_op("flip_bool", r"\btrue\b", "false")]
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
        ops = [_op("flip_bool", r"\btrue\b", "false")]
        muts = _mutate("x = 42;", ops)
        assert muts == []


# ── Code masking ─────────────────────────────────────────────────────────


class TestCppCodeMasks:
    def _mutable_chars(self, line: str) -> str:
        """Return only the characters marked as mutable."""
        masks, _ = compute_code_masks_cpp([line])
        return "".join(ch for ch, m in zip(line, masks[0]) if m)

    def test_line_comment(self):
        assert self._mutable_chars("x = 1; // comment") == "x = 1; "

    def test_block_comment(self):
        assert self._mutable_chars("x = /* hidden */ y;") == "x =  y;"

    def test_string_literal(self):
        assert self._mutable_chars('x = "hello";') == "x = ;"

    def test_char_literal(self):
        assert self._mutable_chars("x = 'a';") == "x = ;"

    def test_preprocessor_skipped(self):
        _, skip = compute_code_masks_cpp(["#include <stdio.h>", "int x;"])
        assert skip[0] is True
        assert skip[1] is False

    def test_multiline_block_comment(self):
        lines = ["x = 1; /*", "still comment", "*/ y = 2;"]
        masks, _ = compute_code_masks_cpp(lines)
        # First line: "x = 1; " is mutable, "/*" is not
        assert all(masks[0][i] for i in range(7))
        # Second line: entirely in block comment
        assert not any(masks[1])
        # Third line: "*/" is not mutable, " y = 2;" is
        assert not masks[2][0] and not masks[2][1]
        assert masks[2][3]  # space after */


class TestTypescriptCodeMasks:
    def _mutable_chars(self, line: str) -> str:
        masks, _ = compute_code_masks_typescript([line])
        return "".join(ch for ch, m in zip(line, masks[0]) if m)

    def test_line_comment(self):
        assert self._mutable_chars("x = 1; // comment") == "x = 1; "

    def test_template_literal(self):
        assert self._mutable_chars("x = `hello ${name}`;") == "x = ;"

    def test_single_quoted_string(self):
        assert self._mutable_chars("x = 'hello';") == "x = ;"

    def test_double_quoted_string(self):
        assert self._mutable_chars('x = "hello";') == "x = ;"

    def test_decorator_skipped(self):
        _, skip = compute_code_masks_typescript(["@Injectable()", "class Foo {}"])
        assert skip[0] is True
        assert skip[1] is False

    def test_multiline_template_literal(self):
        lines = ["x = `start", "middle", "end`;"]
        masks, _ = compute_code_masks_typescript(lines)
        assert masks[0][0]  # "x" is mutable
        # Inside the template literal, chars after ` are not mutable
        assert not masks[0][5]  # the backtick-s
        assert not any(masks[1])  # entire middle line
        assert not masks[2][0]  # "e" of "end" still in template


class TestGdscriptCodeMasks:
    def _mutable_chars(self, line: str) -> str:
        masks, _ = compute_code_masks_gdscript([line])
        return "".join(ch for ch, m in zip(line, masks[0]) if m)

    def test_line_comment(self):
        assert self._mutable_chars("x = 1 # comment") == "x = 1 "

    def test_string_literal(self):
        assert self._mutable_chars('x = "hello"') == "x = "

    def test_annotation_skipped(self):
        _, skip = compute_code_masks_gdscript(["@export var x", "var y"])
        assert skip[0] is True
        assert skip[1] is False


# ── Shared operator patterns ────────────────────────────────────────────


class TestSharedOperatorPatterns:
    """Test that SHARED_OPERATORS regex patterns match intended targets."""

    STANDARD = SHARED_OPERATORS["STANDARD"]
    STRONGER = SHARED_OPERATORS["STRONGER_EXTRA"]

    def _op(self, name: str) -> OperatorDef:
        for lst in [self.STANDARD, self.STRONGER]:
            for op in lst:
                if op.name == name:
                    return op
        raise ValueError(name)

    # --- Relational ---

    def test_lt_to_lte(self):
        op = self._op("rel_lt_to_lte")
        assert _matches(op, "if (x < y)")
        assert not _matches(op, "if (x <= y)")
        assert not _matches(op, "x << 2")

    def test_eq_to_neq(self):
        op = self._op("rel_eq_to_neq")
        assert _matches(op, "x == y")
        assert not _matches(op, "x != y")
        assert not _matches(op, "x === y")

    # --- Arithmetic ---

    def test_add_to_sub(self):
        op = self._op("arith_add_to_sub")
        assert _matches(op, "x + y")
        assert not _matches(op, "x++")
        assert not _matches(op, "x += 1")

    def test_mul_to_div(self):
        op = self._op("arith_mul_to_div")
        assert _matches(op, "x * y")
        assert not _matches(op, "x *= 1")

    # --- Boolean ---

    def test_true_to_false(self):
        op = self._op("bool_true_to_false")
        assert _matches(op, "return true;")
        assert not _matches(op, "trueColor")  # word boundary

    # --- Invert negatives ---

    def test_unary_minus_remove(self):
        op = self._op("unary_minus_remove")
        assert _matches(op, "x = -y")
        assert _matches(op, "return -1;")  # -1 doesn't match (digit), wait...
        assert _matches(op, "foo(-x)")
        assert _matches(op, "(-x)")
        assert _matches(op, "[-1]")

    def test_unary_minus_skips_binary(self):
        op = self._op("unary_minus_remove")
        # "x - y" — space after - means lookahead fails
        assert not _matches(op, "x - y")
        assert not _matches(op, "x -= 5")
        assert not _matches(op, "a->b")

    # --- Assignment ---

    def test_assign_add_to_sub(self):
        op = self._op("assign_add_to_sub")
        assert _matches(op, "x += 1;")
        assert not _matches(op, "x + 1;")


# ── Language-specific operator patterns ──────────────────────────────────


class TestCppOperatorPatterns:
    OPS = CPP_LANGUAGE["operators"]

    def _op(self, name: str, preset: str = "STANDARD") -> OperatorDef:
        return _find_ops(self.OPS[preset], name)

    def test_void_call_simple(self):
        op = self._op("void_call_remove")
        assert _matches(op, "  foo();")
        assert _matches(op, "  obj.bar(x, y);")
        assert _matches(op, "  ns::func();")
        assert _matches(op, "  this->do_stuff();")

    def test_void_call_skips_assigned(self):
        op = self._op("void_call_remove")
        assert not _matches(op, "  x = foo();")
        assert not _matches(op, "  return foo();")
        assert not _matches(op, "  if (foo())")

    def test_return_nullptr(self):
        op = self._op("return_nullptr")
        assert _matches(op, "return ptr;")
        assert _matches(op, "return new Foo();")
        assert not _matches(op, "return nullptr;")

    def test_stmt_remove(self):
        op = self._op("stmt_remove", "STRONGER")
        assert _matches(op, "  x = 5;")
        assert _matches(op, "  counter++;")
        assert _matches(op, "  obj.field--;")
        assert _matches(op, "  x += 1;")
        assert not _matches(op, "  foo();")  # no = or ++/--
        assert not _matches(op, "  if (x) {")

    def test_switch_break_remove(self):
        op = self._op("switch_break_remove", "STRONGER")
        assert _matches(op, "    break;")
        assert not _matches(op, "    breakpoint();")

    def test_switch_case_zero(self):
        op = self._op("switch_case_zero", "STRONGER")
        assert _matches(op, "  case 42:")
        assert _matches(op, "  case FOO:")
        assert not _matches(op, "  case 0:")


class TestTypescriptOperatorPatterns:
    OPS = TS_LANGUAGE["operators"]

    def _op(self, name: str, preset: str = "STANDARD") -> OperatorDef:
        return _find_ops(self.OPS[preset], name)

    def test_void_call_simple(self):
        op = self._op("void_call_remove")
        assert _matches(op, "  foo();")
        assert _matches(op, "  obj.bar(x, y);")

    def test_void_call_with_await(self):
        op = self._op("void_call_remove")
        assert _matches(op, "  await foo();")
        assert _matches(op, "  await obj.save();")

    def test_void_call_skips_assigned(self):
        op = self._op("void_call_remove")
        assert not _matches(op, "  const x = foo();")
        assert not _matches(op, "  return foo();")

    def test_return_null(self):
        op = self._op("return_null")
        assert _matches(op, "return value;")
        assert not _matches(op, "return null;")

    def test_stmt_remove(self):
        op = self._op("stmt_remove", "STRONGER")
        assert _matches(op, "  count++;")
        assert _matches(op, "  x = 5;")
        assert _matches(op, "  obj.field = value;")
        assert _matches(op, "  x += 1;")
        assert not _matches(op, "  foo();")

    def test_switch_case_zero_ts(self):
        op = self._op("switch_case_zero", "STRONGER")
        assert _matches(op, '  case "hello":')
        assert _matches(op, "  case 42:")
        assert not _matches(op, "  case 0:")

    def test_arrow_to_undefined(self):
        op = self._op("arrow_to_undefined")
        assert _matches(op, "const f = (x) => x + 1")
        assert not _matches(op, "const f = (x) => undefined")
        assert not _matches(op, "const f = (x) => {")


class TestGdscriptOperatorPatterns:
    OPS = GDS_LANGUAGE["operators"]

    def _op(self, name: str, preset: str = "STANDARD") -> OperatorDef:
        return _find_ops(self.OPS[preset], name)

    def test_void_call(self):
        op = self._op("void_call_remove")
        assert _matches(op, "  emit_signal('ready')")
        assert _matches(op, "  obj.do_thing()")

    def test_return_null_in_standard(self):
        """return_null was promoted from STRONGER to STANDARD for GDScript."""
        op = self._op("return_null", "STANDARD")
        assert _matches(op, "return value")
        assert not _matches(op, "return null")

    def test_stmt_remove(self):
        op = self._op("stmt_remove", "STRONGER")
        assert _matches(op, "  x = 5")
        assert _matches(op, "  health -= damage")
        assert not _matches(op, "  do_thing()")

    def test_keyword_logical(self):
        op = self._op("log_and_word_to_or")
        assert _matches(op, "if x and y:")
        assert not _matches(op, "if x or y:")

    def test_match_wildcard(self):
        op = self._op("match_pattern_wildcard", "ALL")
        assert _matches(op, "  42:")
        assert not _matches(op, "  _:")


# ── compute_score ────────────────────────────────────────────────────────


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


# ── Preset composition ──────────────────────────────────────────────────


class TestPresetComposition:
    """Verify that language plugins compose presets correctly."""

    def test_standard_includes_shared(self):
        shared_names = {op.name for op in SHARED_OPERATORS["STANDARD"]}
        for lang in [CPP_LANGUAGE, TS_LANGUAGE, GDS_LANGUAGE]:
            standard_names = {op.name for op in lang["operators"]["STANDARD"]}
            missing = shared_names - standard_names
            assert not missing, f"{lang['name']} STANDARD missing shared ops: {missing}"

    def test_stronger_includes_standard(self):
        for lang in [CPP_LANGUAGE, TS_LANGUAGE, GDS_LANGUAGE]:
            standard_names = {op.name for op in lang["operators"]["STANDARD"]}
            stronger_names = {op.name for op in lang["operators"]["STRONGER"]}
            missing = standard_names - stronger_names
            assert not missing, f"{lang['name']} STRONGER missing STANDARD ops: {missing}"

    def test_all_includes_stronger(self):
        for lang in [CPP_LANGUAGE, TS_LANGUAGE, GDS_LANGUAGE]:
            stronger_names = {op.name for op in lang["operators"]["STRONGER"]}
            all_names = {op.name for op in lang["operators"]["ALL"]}
            missing = stronger_names - all_names
            assert not missing, f"{lang['name']} ALL missing STRONGER ops: {missing}"

    def test_new_operators_present_in_standard(self):
        """All three PITest-default operators should be in STANDARD for every language."""
        for lang in [CPP_LANGUAGE, TS_LANGUAGE, GDS_LANGUAGE]:
            names = {op.name for op in lang["operators"]["STANDARD"]}
            assert "unary_minus_remove" in names, f"{lang['name']} missing unary_minus_remove"
            assert "void_call_remove" in names, f"{lang['name']} missing void_call_remove"
            # Null return has language-specific names
            null_return_names = {"return_nullptr", "return_null"}
            assert names & null_return_names, f"{lang['name']} missing null return operator"

    def test_new_operators_present_in_stronger(self):
        for lang in [CPP_LANGUAGE, TS_LANGUAGE]:
            names = {op.name for op in lang["operators"]["STRONGER"]}
            assert "stmt_remove" in names, f"{lang['name']} missing stmt_remove"
            assert "switch_break_remove" in names, f"{lang['name']} missing switch_break_remove"
            assert "switch_case_zero" in names, f"{lang['name']} missing switch_case_zero"
        # GDScript has stmt_remove but not switch (uses match instead)
        gds_names = {op.name for op in GDS_LANGUAGE["operators"]["STRONGER"]}
        assert "stmt_remove" in gds_names


# ── Integration: generate + apply round-trip ─────────────────────────────


class TestMutationRoundTrip:
    """Generate mutations, apply each, and verify the source is actually changed."""

    def test_bool_flip_roundtrip(self):
        src = "if (true) return false;\n"
        ops = SHARED_OPERATORS["STANDARD"]
        muts = generate_mutations("test.cpp", src, ops, _all_mutable_mask)
        bool_muts = [m for m in muts if m.operator.startswith("bool_")]
        assert len(bool_muts) >= 2
        for mut in bool_muts:
            patched = apply_mutation(src, mut)
            assert patched != src

    def test_void_call_removal_roundtrip_cpp(self):
        src = "  update();\n"
        ops = CPP_LANGUAGE["operators"]["STANDARD"]
        muts = generate_mutations("test.cpp", src, ops, _all_mutable_mask)
        void_muts = [m for m in muts if m.operator == "void_call_remove"]
        assert len(void_muts) == 1
        patched = apply_mutation(src, void_muts[0])
        assert "update" not in patched
        assert ";" in patched

    def test_return_null_roundtrip_ts(self):
        src = "return value;\n"
        ops = TS_LANGUAGE["operators"]["STANDARD"]
        muts = generate_mutations("test.ts", src, ops, _all_mutable_mask)
        null_muts = [m for m in muts if m.operator == "return_null"]
        assert len(null_muts) == 1
        patched = apply_mutation(src, null_muts[0])
        assert "return null;" in patched

    def test_stmt_remove_roundtrip_cpp(self):
        src = "  counter++;\n"
        ops = CPP_LANGUAGE["operators"]["STRONGER"]
        muts = generate_mutations("test.cpp", src, ops, _all_mutable_mask)
        stmt_muts = [m for m in muts if m.operator == "stmt_remove"]
        assert len(stmt_muts) == 1
        patched = apply_mutation(src, stmt_muts[0])
        assert "counter" not in patched

    def test_masking_prevents_string_mutation(self):
        """Mutations inside string literals should not be generated."""
        src = 'const msg = "true and false";\n'
        ops = TS_LANGUAGE["operators"]["STANDARD"]
        muts = generate_mutations("test.ts", src, ops, compute_code_masks_typescript)
        bool_muts = [m for m in muts if m.operator.startswith("bool_")]
        assert len(bool_muts) == 0

    def test_masking_prevents_comment_mutation(self):
        src = "// x + y should be x - y\nreal = a + b;\n"
        ops = CPP_LANGUAGE["operators"]["STANDARD"]
        muts = generate_mutations("test.cpp", src, ops, compute_code_masks_cpp)
        arith_muts = [m for m in muts if m.operator.startswith("arith_")]
        # Only the mutation on line 2 should survive
        assert all(m.line == 2 for m in arith_muts)
