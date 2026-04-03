"""Extended code mask tests for all language plugins — targeting surviving mutants.

The surviving mutants are primarily in the character-by-character parsing loops
(escape handling, state transitions, boundary conditions). These tests exercise
those specific code paths.
"""

from cerebro.lang.cpp import compute_code_masks_cpp, LANGUAGE as CPP_LANG
from cerebro.lang.typescript import compute_code_masks_typescript, LANGUAGE as TS_LANG
from cerebro.lang.gdscript import compute_code_masks_gdscript, LANGUAGE as GDS_LANG


def _mutable_chars(mask_fn, line: str) -> str:
    masks, _ = mask_fn([line])
    return "".join(ch for ch, m in zip(line, masks[0]) if m)


def _mutable_multi(mask_fn, lines: list[str]) -> list[str]:
    masks, _ = mask_fn(lines)
    return ["".join(ch for ch, m in zip(line, mask) if m) for line, mask in zip(lines, masks)]


# ═══════════════════════════════════════════════════════════════════════
# C++ extended mask tests
# ═══════════════════════════════════════════════════════════════════════

class TestCppMaskExtended:
    def _m(self, line):
        return _mutable_chars(compute_code_masks_cpp, line)

    def test_escape_in_string(self):
        assert self._m(r'x = "a\"b";') == "x = ;"

    def test_escape_in_char(self):
        assert self._m(r"x = '\n';") == "x = ;"

    def test_string_then_code(self):
        assert self._m('"s" + x') == " + x"

    def test_empty_string(self):
        assert self._m('x = "";') == "x = ;"

    def test_single_char_string(self):
        assert self._m('x = "a";') == "x = ;"

    def test_block_comment_close_boundary(self):
        """Closing * / exactly at end of line."""
        lines = ["/* comment", "end*/"]
        result = _mutable_multi(compute_code_masks_cpp, lines)
        assert result[0] == ""
        assert result[1] == ""

    def test_block_comment_with_stars(self):
        assert self._m("x = /** doc **/ y;") == "x =  y;"

    def test_code_between_two_strings(self):
        assert self._m('"a" + "b"') == " + "

    def test_nested_escape_sequences(self):
        assert self._m(r'x = "\\";') == "x = ;"

    def test_preprocessor_with_leading_space(self):
        _, skip = compute_code_masks_cpp(["  #define X"])
        assert skip[0] is True

    def test_char_escape_backslash(self):
        assert self._m(r"x = '\\';") == "x = ;"

    def test_line_comment_at_start(self):
        assert self._m("// all comment") == ""

    def test_empty_line(self):
        masks, skip = compute_code_masks_cpp([""])
        assert masks[0] == []
        assert skip[0] is False

    def test_block_comment_same_line(self):
        assert self._m("a /* b */ c") == "a  c"

    def test_string_with_backslash_at_end(self):
        # Unterminated string with escape at end
        result = self._m('x = "test\\')
        # Everything from quote onward should be masked
        assert result.startswith("x = ")


class TestCppPluginMeta:
    def test_name(self):
        assert CPP_LANG["name"] == "cpp"

    def test_build_required(self):
        assert CPP_LANG["build_required"] is True

    def test_presets(self):
        ops = CPP_LANG["operators"]
        assert "STANDARD" in ops
        assert "STRONGER" in ops
        assert "ALL" in ops

    def test_stronger_superset(self):
        std = {op.name for op in CPP_LANG["operators"]["STANDARD"]}
        strong = {op.name for op in CPP_LANG["operators"]["STRONGER"]}
        assert std.issubset(strong)

    def test_all_superset(self):
        strong = {op.name for op in CPP_LANG["operators"]["STRONGER"]}
        all_ops = {op.name for op in CPP_LANG["operators"]["ALL"]}
        assert strong.issubset(all_ops)


# ═══════════════════════════════════════════════════════════════════════
# TypeScript extended mask tests
# ═══════════════════════════════════════════════════════════════════════

class TestTypeScriptMaskExtended:
    def _m(self, line):
        return _mutable_chars(compute_code_masks_typescript, line)

    def test_escape_in_double_string(self):
        assert self._m(r'x = "a\"b";') == "x = ;"

    def test_escape_in_single_string(self):
        assert self._m(r"x = 'a\'b';") == "x = ;"

    def test_template_literal_simple(self):
        assert self._m("x = `hello`;") == "x = ;"

    def test_template_with_escape(self):
        assert self._m(r"x = `he\`lo`;") == "x = ;"

    def test_block_comment(self):
        assert self._m("x = /* hi */ y;") == "x =  y;"

    def test_multiline_block_comment(self):
        lines = ["a; /*", "comment", "*/ b;"]
        result = _mutable_multi(compute_code_masks_typescript, lines)
        assert result[0] == "a; "
        assert result[1] == ""
        assert result[2] == " b;"

    def test_multiline_template_literal_state(self):
        lines = ["x = `start", "mid", "end`;", "y = 1;"]
        masks, _ = compute_code_masks_typescript(lines)
        # Line 3 (y = 1;) should be fully mutable
        assert all(masks[3])

    def test_decorator_full_line_masked(self):
        masks, skip = compute_code_masks_typescript(["@Component({})"])
        assert skip[0] is True
        assert all(not m for m in masks[0])

    def test_code_after_string(self):
        assert self._m('"s" + x') == " + x"

    def test_empty_string_literals(self):
        assert self._m('x = "" + \'\';') == "x =  + ;"

    def test_line_comment_at_start(self):
        assert self._m("// comment") == ""

    def test_block_comment_single_line(self):
        assert self._m("a /* b */ c") == "a  c"


class TestTsPluginMeta:
    def test_name(self):
        assert TS_LANG["name"] == "typescript"

    def test_build_not_required(self):
        assert TS_LANG["build_required"] is False

    def test_presets(self):
        ops = TS_LANG["operators"]
        assert "STANDARD" in ops
        assert "STRONGER" in ops
        assert "ALL" in ops

    def test_stronger_superset(self):
        std = {op.name for op in TS_LANG["operators"]["STANDARD"]}
        strong = {op.name for op in TS_LANG["operators"]["STRONGER"]}
        assert std.issubset(strong)

    def test_all_superset(self):
        strong = {op.name for op in TS_LANG["operators"]["STRONGER"]}
        all_ops = {op.name for op in TS_LANG["operators"]["ALL"]}
        assert strong.issubset(all_ops)

    def test_has_strict_equality(self):
        names = {op.name for op in TS_LANG["operators"]["STANDARD"]}
        assert "rel_strict_eq_to_neq" in names

    def test_has_nullish(self):
        names = {op.name for op in TS_LANG["operators"]["STANDARD"]}
        assert "nullish_to_or" in names


# ═══════════════════════════════════════════════════════════════════════
# GDScript extended mask tests
# ═══════════════════════════════════════════════════════════════════════

class TestGdscriptMaskExtended:
    def _m(self, line):
        return _mutable_chars(compute_code_masks_gdscript, line)

    def test_comment_masks(self):
        assert self._m("x = 1 # stuff") == "x = 1 "

    def test_double_string(self):
        assert self._m('x = "hello"') == "x = "

    def test_single_string(self):
        assert self._m("x = 'hello'") == "x = "

    def test_escape_in_double_string(self):
        assert self._m(r'x = "a\"b"') == "x = "

    def test_escape_in_single_string(self):
        assert self._m(r"x = 'a\'b'") == "x = "

    def test_annotation_line(self):
        masks, skip = compute_code_masks_gdscript(["@export var x = 1"])
        assert skip[0] is True
        assert all(not m for m in masks[0])

    def test_triple_quote_masks_rest(self):
        """Triple-quote in GDScript conservatively masks rest of line."""
        masks, _ = compute_code_masks_gdscript(['x = """text'])
        # From """ onwards should be masked
        assert masks[0][0]  # 'x' is mutable
        assert not masks[0][-1]

    def test_code_after_string(self):
        assert self._m('"s" + x') == " + x"

    def test_empty_string(self):
        assert self._m('x = ""') == "x = "

    def test_comment_at_start(self):
        assert self._m("# all comment") == ""

    def test_pure_code(self):
        line = "x = a + b"
        masks, _ = compute_code_masks_gdscript([line])
        assert all(masks[0])

    def test_string_then_comment(self):
        assert self._m('"str" # comment') == " "


class TestGdsPluginMeta:
    def test_name(self):
        assert GDS_LANG["name"] == "gdscript"

    def test_build_not_required(self):
        assert GDS_LANG["build_required"] is False

    def test_presets(self):
        ops = GDS_LANG["operators"]
        assert "STANDARD" in ops
        assert "STRONGER" in ops
        assert "ALL" in ops

    def test_stronger_superset(self):
        std = {op.name for op in GDS_LANG["operators"]["STANDARD"]}
        strong = {op.name for op in GDS_LANG["operators"]["STRONGER"]}
        assert std.issubset(strong)

    def test_all_superset(self):
        strong = {op.name for op in GDS_LANG["operators"]["STRONGER"]}
        all_ops = {op.name for op in GDS_LANG["operators"]["ALL"]}
        assert strong.issubset(all_ops)

    def test_has_keyword_logicals(self):
        names = {op.name for op in GDS_LANG["operators"]["STANDARD"]}
        assert "log_and_word_to_or" in names
        assert "log_or_word_to_and" in names

    def test_has_match_wildcard(self):
        names = {op.name for op in GDS_LANG["operators"]["ALL"]}
        assert "match_pattern_wildcard" in names
