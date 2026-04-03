"""Integration tests: generate + apply round-trip."""

from cerebro.operators import SHARED_OPERATORS
from cerebro.mutation import generate_mutations, apply_mutation
from cerebro.lang.cpp import LANGUAGE as CPP_LANGUAGE, compute_code_masks_cpp
from cerebro.lang.typescript import LANGUAGE as TS_LANGUAGE, compute_code_masks_typescript
from conftest import all_mutable_mask


class TestMutationRoundTrip:
    """Generate mutations, apply each, and verify the source is actually changed."""

    def test_bool_flip_roundtrip(self):
        src = "if (true) return false;\n"
        ops = SHARED_OPERATORS["STANDARD"]
        muts = generate_mutations("test.cpp", src, ops, all_mutable_mask)
        bool_muts = [m for m in muts if m.operator.startswith("bool_")]
        assert len(bool_muts) >= 2
        for mut in bool_muts:
            patched = apply_mutation(src, mut)
            assert patched != src

    def test_void_call_removal_roundtrip_cpp(self):
        src = "  update();\n"
        ops = CPP_LANGUAGE["operators"]["STANDARD"]
        muts = generate_mutations("test.cpp", src, ops, all_mutable_mask)
        void_muts = [m for m in muts if m.operator == "void_call_remove"]
        assert len(void_muts) == 1
        patched = apply_mutation(src, void_muts[0])
        assert "update" not in patched
        assert ";" in patched

    def test_return_null_roundtrip_ts(self):
        src = "return value;\n"
        ops = TS_LANGUAGE["operators"]["STANDARD"]
        muts = generate_mutations("test.ts", src, ops, all_mutable_mask)
        null_muts = [m for m in muts if m.operator == "return_null"]
        assert len(null_muts) == 1
        patched = apply_mutation(src, null_muts[0])
        assert "return null;" in patched

    def test_stmt_remove_roundtrip_cpp(self):
        src = "  counter++;\n"
        ops = CPP_LANGUAGE["operators"]["STRONGER"]
        muts = generate_mutations("test.cpp", src, ops, all_mutable_mask)
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
