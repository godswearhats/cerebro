"""Mutation generation and application."""

from __future__ import annotations

import re

from cerebro.operators import OperatorDef
from cerebro.types import Mutation


def generate_mutations(
    file_path: str,
    content: str,
    operators: list[OperatorDef],
    mask_fn: callable = None,
) -> list[Mutation]:
    """Scan a source file and produce all candidate mutations."""
    from cerebro.config import builtin_cpp_code_masks

    lines = content.splitlines()
    if mask_fn is None:
        mask_fn = builtin_cpp_code_masks
    masks, is_pp = mask_fn(lines)
    mutations: list[Mutation] = []

    for line_idx, line in enumerate(lines):
        if is_pp[line_idx]:
            continue
        mask = masks[line_idx]

        for op in operators:
            for m in re.finditer(op.pattern, line):
                start, end = m.start(), m.end()
                # Every matched character must be in mutable code
                if start >= len(mask) or end > len(mask):
                    continue
                if not all(mask[i] for i in range(start, end)):
                    continue

                mutations.append(
                    Mutation(
                        file=file_path,
                        line=line_idx + 1,
                        col=start,
                        end_col=end,
                        original=m.group(),
                        replacement=op.replacement,
                        operator=op.name,
                        description=op.description,
                    )
                )

    # Deduplicate by (file, line, col, replacement)
    seen: set[tuple[str, int, int, str]] = set()
    unique: list[Mutation] = []
    for mut in mutations:
        key = (mut.file, mut.line, mut.col, mut.replacement)
        if key not in seen:
            seen.add(key)
            unique.append(mut)

    return unique


def apply_mutation(content: str, mutation: Mutation) -> str:
    """Apply a single mutation to file content and return the modified content."""
    lines = content.splitlines(keepends=True)
    line_idx = mutation.line - 1
    if line_idx < 0 or line_idx >= len(lines):
        raise ValueError(f"Line {mutation.line} out of range for file with {len(lines)} lines")

    line = lines[line_idx]
    new_line = line[: mutation.col] + mutation.replacement + line[mutation.end_col :]
    lines[line_idx] = new_line
    return "".join(lines)
