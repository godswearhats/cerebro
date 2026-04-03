"""Tests for operator preset composition across languages."""

from cerebro.operators import SHARED_OPERATORS
from cerebro.lang.cpp import LANGUAGE as CPP_LANGUAGE
from cerebro.lang.typescript import LANGUAGE as TS_LANGUAGE
from cerebro.lang.gdscript import LANGUAGE as GDS_LANGUAGE


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
