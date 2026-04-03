"""Tests for shared and language-specific operator patterns."""

from cerebro.operators import SHARED_OPERATORS
from cerebro.lang.cpp import LANGUAGE as CPP_LANGUAGE
from cerebro.lang.typescript import LANGUAGE as TS_LANGUAGE
from cerebro.lang.gdscript import LANGUAGE as GDS_LANGUAGE
from conftest import find_op, matches


class TestSharedOperatorPatterns:
    """Test that SHARED_OPERATORS regex patterns match intended targets."""

    STANDARD = SHARED_OPERATORS["STANDARD"]
    STRONGER = SHARED_OPERATORS["STRONGER_EXTRA"]

    def _op(self, name: str):
        for lst in [self.STANDARD, self.STRONGER]:
            for op in lst:
                if op.name == name:
                    return op
        raise ValueError(name)

    # --- Relational ---

    def test_lt_to_lte(self):
        op = self._op("rel_lt_to_lte")
        assert matches(op, "if (x < y)")
        assert not matches(op, "if (x <= y)")
        assert not matches(op, "x << 2")

    def test_eq_to_neq(self):
        op = self._op("rel_eq_to_neq")
        assert matches(op, "x == y")
        assert not matches(op, "x != y")
        assert not matches(op, "x === y")

    # --- Arithmetic ---

    def test_add_to_sub(self):
        op = self._op("arith_add_to_sub")
        assert matches(op, "x + y")
        assert not matches(op, "x++")
        assert not matches(op, "x += 1")

    def test_mul_to_div(self):
        op = self._op("arith_mul_to_div")
        assert matches(op, "x * y")
        assert not matches(op, "x *= 1")

    # --- Boolean ---

    def test_true_to_false(self):
        op = self._op("bool_true_to_false")
        assert matches(op, "return true;")
        assert not matches(op, "trueColor")  # word boundary

    # --- Invert negatives ---

    def test_unary_minus_remove(self):
        op = self._op("unary_minus_remove")
        assert matches(op, "x = -y")
        assert matches(op, "return -1;")
        assert matches(op, "foo(-x)")
        assert matches(op, "(-x)")
        assert matches(op, "[-1]")

    def test_unary_minus_skips_binary(self):
        op = self._op("unary_minus_remove")
        assert not matches(op, "x - y")
        assert not matches(op, "x -= 5")
        assert not matches(op, "a->b")

    # --- Assignment ---

    def test_assign_add_to_sub(self):
        op = self._op("assign_add_to_sub")
        assert matches(op, "x += 1;")
        assert not matches(op, "x + 1;")


class TestCppOperatorPatterns:
    OPS = CPP_LANGUAGE["operators"]

    def _op(self, name: str, preset: str = "STANDARD"):
        return find_op(self.OPS[preset], name)

    def test_void_call_simple(self):
        op = self._op("void_call_remove")
        assert matches(op, "  foo();")
        assert matches(op, "  obj.bar(x, y);")
        assert matches(op, "  ns::func();")
        assert matches(op, "  this->do_stuff();")

    def test_void_call_skips_assigned(self):
        op = self._op("void_call_remove")
        assert not matches(op, "  x = foo();")
        assert not matches(op, "  return foo();")
        assert not matches(op, "  if (foo())")

    def test_return_nullptr(self):
        op = self._op("return_nullptr")
        assert matches(op, "return ptr;")
        assert matches(op, "return new Foo();")
        assert not matches(op, "return nullptr;")

    def test_stmt_remove(self):
        op = self._op("stmt_remove", "STRONGER")
        assert matches(op, "  x = 5;")
        assert matches(op, "  counter++;")
        assert matches(op, "  obj.field--;")
        assert matches(op, "  x += 1;")
        assert not matches(op, "  foo();")  # no = or ++/--
        assert not matches(op, "  if (x) {")

    def test_switch_break_remove(self):
        op = self._op("switch_break_remove", "STRONGER")
        assert matches(op, "    break;")
        assert not matches(op, "    breakpoint();")

    def test_switch_case_zero(self):
        op = self._op("switch_case_zero", "STRONGER")
        assert matches(op, "  case 42:")
        assert matches(op, "  case FOO:")
        assert not matches(op, "  case 0:")


class TestTypescriptOperatorPatterns:
    OPS = TS_LANGUAGE["operators"]

    def _op(self, name: str, preset: str = "STANDARD"):
        return find_op(self.OPS[preset], name)

    def test_void_call_simple(self):
        op = self._op("void_call_remove")
        assert matches(op, "  foo();")
        assert matches(op, "  obj.bar(x, y);")

    def test_void_call_with_await(self):
        op = self._op("void_call_remove")
        assert matches(op, "  await foo();")
        assert matches(op, "  await obj.save();")

    def test_void_call_skips_assigned(self):
        op = self._op("void_call_remove")
        assert not matches(op, "  const x = foo();")
        assert not matches(op, "  return foo();")

    def test_return_null(self):
        op = self._op("return_null")
        assert matches(op, "return value;")
        assert not matches(op, "return null;")

    def test_stmt_remove(self):
        op = self._op("stmt_remove", "STRONGER")
        assert matches(op, "  count++;")
        assert matches(op, "  x = 5;")
        assert matches(op, "  obj.field = value;")
        assert matches(op, "  x += 1;")
        assert not matches(op, "  foo();")

    def test_switch_case_zero_ts(self):
        op = self._op("switch_case_zero", "STRONGER")
        assert matches(op, '  case "hello":')
        assert matches(op, "  case 42:")
        assert not matches(op, "  case 0:")

    def test_arrow_to_undefined(self):
        op = self._op("arrow_to_undefined")
        assert matches(op, "const f = (x) => x + 1")
        assert not matches(op, "const f = (x) => undefined")
        assert not matches(op, "const f = (x) => {")


class TestGdscriptOperatorPatterns:
    OPS = GDS_LANGUAGE["operators"]

    def _op(self, name: str, preset: str = "STANDARD"):
        return find_op(self.OPS[preset], name)

    def test_void_call(self):
        op = self._op("void_call_remove")
        assert matches(op, "  emit_signal('ready')")
        assert matches(op, "  obj.do_thing()")

    def test_return_null_in_standard(self):
        """return_null was promoted from STRONGER to STANDARD for GDScript."""
        op = self._op("return_null", "STANDARD")
        assert matches(op, "return value")
        assert not matches(op, "return null")

    def test_stmt_remove(self):
        op = self._op("stmt_remove", "STRONGER")
        assert matches(op, "  x = 5")
        assert matches(op, "  health -= damage")
        assert not matches(op, "  do_thing()")

    def test_keyword_logical(self):
        op = self._op("log_and_word_to_or")
        assert matches(op, "if x and y:")
        assert not matches(op, "if x or y:")

    def test_match_wildcard(self):
        op = self._op("match_pattern_wildcard", "ALL")
        assert matches(op, "  42:")
        assert not matches(op, "  _:")
