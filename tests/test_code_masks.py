"""Tests for language-specific code masking."""

from cerebro.lang.cpp import compute_code_masks_cpp
from cerebro.lang.typescript import compute_code_masks_typescript
from cerebro.lang.gdscript import compute_code_masks_gdscript


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
