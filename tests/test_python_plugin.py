"""Tests for the Python language plugin — code masking and operators."""

import re

from cerebro.lang.python import LANGUAGE, compute_code_masks_python
from cerebro.operators import OperatorDef
from cerebro.mutation import generate_mutations


def _mutable(line: str) -> str:
    """Return only the characters marked as mutable for a single line."""
    masks, _ = compute_code_masks_python([line])
    return "".join(ch for ch, m in zip(line, masks[0]) if m)


def _matches(op: OperatorDef, text: str) -> list[str]:
    return [m.group() for m in re.finditer(op.pattern, text)]


def _find(name: str, preset: str = "STANDARD") -> OperatorDef:
    for op in LANGUAGE["operators"][preset]:
        if op.name == name:
            return op
    raise ValueError(f"{name!r} not in {preset}")


def _mutate(content: str, preset: str = "STANDARD") -> list:
    ops = LANGUAGE["operators"][preset]
    return generate_mutations("test.py", content, ops, compute_code_masks_python)


# ── Plugin metadata ─────────────────────────────────────────────────────

class TestPluginMetadata:
    def test_name(self):
        assert LANGUAGE["name"] == "python"

    def test_source_globs(self):
        assert LANGUAGE["source_globs"] == ["*.py"]

    def test_build_not_required(self):
        assert LANGUAGE["build_required"] is False

    def test_has_all_presets(self):
        ops = LANGUAGE["operators"]
        assert "STANDARD" in ops
        assert "STRONGER" in ops
        assert "ALL" in ops

    def test_stronger_superset_of_standard(self):
        s = set(op.name for op in LANGUAGE["operators"]["STANDARD"])
        st = set(op.name for op in LANGUAGE["operators"]["STRONGER"])
        assert s.issubset(st)

    def test_all_superset_of_stronger(self):
        st = set(op.name for op in LANGUAGE["operators"]["STRONGER"])
        a = set(op.name for op in LANGUAGE["operators"]["ALL"])
        assert st.issubset(a)


# ── Code masking ────────────────────────────────────────────────────────

class TestPythonCodeMasks:
    def test_line_comment(self):
        assert _mutable("x = 1  # comment") == "x = 1  "

    def test_decorator_skipped(self):
        _, skip = compute_code_masks_python(["@decorator", "def foo():"])
        assert skip[0] is True
        assert skip[1] is False

    def test_double_quoted_string(self):
        assert _mutable('x = "hello"') == "x = "

    def test_single_quoted_string(self):
        assert _mutable("x = 'hello'") == "x = "

    def test_escaped_quote_in_string(self):
        assert _mutable(r'x = "he\"llo"') == "x = "

    def test_triple_double_quote_single_line(self):
        assert _mutable('x = """hello"""') == "x = "

    def test_triple_single_quote_single_line(self):
        assert _mutable("x = '''hello'''") == "x = "

    def test_triple_quote_multiline(self):
        lines = ['x = """', "multi-line", '"""', "y = 1"]
        masks, skip = compute_code_masks_python(lines)
        # Line 0: x =  is mutable, """ onwards is not
        assert masks[0][0]  # 'x'
        assert not masks[0][-1]  # last quote
        # Line 1: entirely inside triple-quoted string
        assert not any(masks[1])
        assert skip[1] is True
        # Line 2: closing triple-quote, still string
        assert skip[2] is True
        # Line 3: back to normal code
        assert skip[3] is False
        assert any(masks[3])

    def test_code_after_string_is_mutable(self):
        assert _mutable('x = "s" + y') == "x =  + y"

    def test_fstring_masked(self):
        # f-strings start with f" — the f is code, the string content is not
        result = _mutable('x = f"hello {name}"')
        assert "hello" not in result
        # The 'f' before the quote is mutable code
        assert "f" in result or "x" in result

    def test_empty_line(self):
        masks, skip = compute_code_masks_python([""])
        assert masks[0] == []
        assert skip[0] is False

    def test_pure_code_fully_mutable(self):
        line = "x = a + b - c"
        masks, _ = compute_code_masks_python([line])
        assert all(masks[0])


# ── Operator patterns ───────────────────────────────────────────────────

class TestPythonBooleans:
    def test_true_to_false(self):
        op = _find("bool_True_to_False")
        assert _matches(op, "return True") == ["True"]

    def test_false_to_true(self):
        op = _find("bool_False_to_True")
        assert _matches(op, "if False:") == ["False"]

    def test_no_match_in_identifier(self):
        op = _find("bool_True_to_False")
        assert _matches(op, "is_True_value") == []


class TestPythonLogical:
    def test_and_to_or(self):
        op = _find("log_and_to_or")
        assert _matches(op, "x and y") == ["and"]
        assert op.replacement == "or"

    def test_or_to_and(self):
        op = _find("log_or_to_and")
        assert _matches(op, "x or y") == ["or"]
        assert op.replacement == "and"

    def test_not_remove(self):
        op = _find("unary_not_remove")
        assert _matches(op, "not x") == ["not "]
        assert op.replacement == ""

    def test_and_no_match_in_command(self):
        op = _find("log_and_to_or")
        assert _matches(op, "expand_command") == []


class TestPythonIdentity:
    def test_is_not_to_is(self):
        op = _find("identity_isnot_to_is")
        assert _matches(op, "x is not None") == ["is not"]
        assert op.replacement == "is"

    def test_is_to_is_not(self):
        op = _find("identity_is_to_isnot")
        assert _matches(op, "x is None") == ["is"]
        assert op.replacement == "is not"

    def test_is_not_matches_only_is_not(self):
        """'is → is not' should NOT match inside 'is not'."""
        op = _find("identity_is_to_isnot")
        assert _matches(op, "x is not None") == []


class TestPythonMembership:
    def test_not_in_to_in(self):
        op = _find("member_notin_to_in")
        assert _matches(op, "x not in lst") == ["not in"]

    def test_in_to_not_in(self):
        op = _find("member_in_to_notin")
        assert _matches(op, "x in lst") == ["in"]

    def test_in_no_match_in_func_call(self):
        """'in' should not match 'in(' to avoid matching builtins."""
        op = _find("member_in_to_notin")
        assert _matches(op, "x = int(y)") == []


class TestPythonVoidCallAndReturn:
    def test_void_call_remove(self):
        op = _find("void_call_remove")
        assert _matches(op, "    foo(x, y)") != []
        assert op.replacement == "pass"

    def test_return_none(self):
        op = _find("return_null")
        assert _matches(op, "    return x + 1") != []
        assert op.replacement == "return None"

    def test_return_none_no_match_on_return_none(self):
        op = _find("return_null")
        assert _matches(op, "    return None") == []


class TestPythonStronger:
    def test_cond_to_true(self):
        op = _find("cond_to_true", "STRONGER")
        assert _matches(op, "if x > 0:") != []
        assert op.replacement == "if True:"

    def test_cond_to_false(self):
        op = _find("cond_to_false", "STRONGER")
        assert _matches(op, "if x > 0:") != []
        assert op.replacement == "if False:"

    def test_cond_no_match_on_true(self):
        op = _find("cond_to_true", "STRONGER")
        assert _matches(op, "if True:") == []

    def test_stmt_remove(self):
        op = _find("stmt_remove", "STRONGER")
        assert _matches(op, "    x = 42") != []
        assert op.replacement == "pass"

    def test_string_empty_double(self):
        op = _find("string_empty_double", "STRONGER")
        assert _matches(op, 'x = "hello"') != []
        assert op.replacement == '""'

    def test_string_empty_single(self):
        op = _find("string_empty_single", "STRONGER")
        assert _matches(op, "x = 'hello'") != []
        assert op.replacement == "''"

    def test_return_zero(self):
        op = _find("return_zero", "STRONGER")
        assert _matches(op, "return x + 1") != []
        assert op.replacement == "return 0"


class TestPythonAll:
    def test_const_zero_to_one(self):
        op = _find("const_zero_to_one", "ALL")
        assert _matches(op, "x = 0") == ["0"]

    def test_const_one_to_zero(self):
        op = _find("const_one_to_zero", "ALL")
        assert _matches(op, "x = 1") == ["1"]

    def test_power_to_mul(self):
        op = _find("arith_pow_to_mul", "ALL")
        assert _matches(op, "x ** 2") == ["**"]
        assert op.replacement == "*"

    def test_floordiv_to_div(self):
        op = _find("arith_floordiv_to_div", "ALL")
        assert _matches(op, "x // 2") == ["//"]
        assert op.replacement == "/"


# ── Integration: generate_mutations on Python code ──────────────────────

class TestPythonMutationGeneration:
    def test_mutations_on_simple_code(self):
        code = "x = True and False"
        muts = _mutate(code)
        ops = {m.operator for m in muts}
        assert "bool_True_to_False" in ops
        assert "bool_False_to_True" in ops
        assert "log_and_to_or" in ops

    def test_no_mutations_in_comment(self):
        code = "# True and False"
        muts = _mutate(code)
        assert len(muts) == 0

    def test_no_mutations_in_string(self):
        code = 'x = "True and False"'
        muts = _mutate(code)
        # Should only mutate x = part, not string contents
        bool_muts = [m for m in muts if "bool" in m.operator or "log" in m.operator]
        assert len(bool_muts) == 0

    def test_no_mutations_on_decorator(self):
        code = "@property\ndef foo(): pass"
        muts = _mutate(code)
        # No mutations from the decorator line
        decorator_muts = [m for m in muts if m.line == 1]
        assert len(decorator_muts) == 0

    def test_identity_operators_in_code(self):
        code = "if x is not None:\n    return x"
        muts = _mutate(code)
        ops = {m.operator for m in muts}
        assert "identity_isnot_to_is" in ops

    def test_membership_operators(self):
        code = "if x in items:\n    pass"
        muts = _mutate(code)
        ops = {m.operator for m in muts}
        assert "member_in_to_notin" in ops
