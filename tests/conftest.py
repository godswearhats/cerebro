"""Shared test helpers for cerebro tests."""

import re

from cerebro.operators import OperatorDef
from cerebro.mutation import generate_mutations
from cerebro.types import Mutation


def make_op(name: str, pattern: str, replacement: str) -> OperatorDef:
    return OperatorDef(name, "test", pattern, replacement, f"{name} desc")


def find_op(operators: list[OperatorDef], name: str) -> OperatorDef:
    """Find an operator by name in a list."""
    for op in operators:
        if op.name == name:
            return op
    raise ValueError(f"Operator {name!r} not found")


def matches(op: OperatorDef, line: str) -> list[str]:
    """Return all substrings matched by an operator's pattern on a line."""
    return [m.group() for m in re.finditer(op.pattern, line)]


def all_mutable_mask(lines):
    """Mask that marks every character as mutable code (no comments/strings)."""
    masks = [[True] * len(line) for line in lines]
    skip = [False] * len(lines)
    return masks, skip


def mutate(content: str, operators: list[OperatorDef], mask_fn=None) -> list[Mutation]:
    """Shorthand: generate mutations for a string treated as a single file."""
    if mask_fn is None:
        mask_fn = all_mutable_mask
    return generate_mutations("test.cpp", content, operators, mask_fn)
