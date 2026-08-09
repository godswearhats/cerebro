"""Command-line interface: argument parser, subcommands, and entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys

from cerebro.operators import OperatorDef
from cerebro.types import Mutation, MutationResult
from cerebro.mutation import generate_mutations
from cerebro.config import Config, discover_sources, select_sources, builtin_cpp_code_masks, OPERATOR_PRESETS
from cerebro.runner import run_mutations, load_cache
from cerebro.report import compute_score, report_text, report_json, report_html


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def info(msg: str):
    print(msg, file=sys.stderr, flush=True)


def die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_list_operators(args: argparse.Namespace):
    preset = args.preset or "STANDARD"
    # Load language plugin if config is provided, else use default C++ operators
    if args.config and os.path.exists(args.config):
        cfg = Config.load(args.config)
        lang_ops = cfg.lang_plugin.get("operators", OPERATOR_PRESETS)
        lang_name = cfg.language
    else:
        lang_ops = OPERATOR_PRESETS
        lang_name = "cpp"
    if preset not in lang_ops:
        die(f"Unknown preset: {preset!r}")
    ops = lang_ops[preset]
    print(f"\nMutation operators for {lang_name}/{preset} ({len(ops)} operators):\n")
    categories: dict[str, list[OperatorDef]] = {}
    for op in ops:
        categories.setdefault(op.category, []).append(op)
    for cat in sorted(categories):
        print(f"  {cat}:")
        for op in categories[cat]:
            print(f"    {op.name:<30} {op.description}")
        print()


def cmd_dry_run(args: argparse.Namespace):
    cfg = Config.load(args.config)
    if args.preset:
        cfg.preset = args.preset
    lang_operators = cfg.lang_plugin.get("operators", OPERATOR_PRESETS)
    operators = lang_operators[cfg.preset]
    mask_fn = cfg.lang_plugin.get("compute_code_masks", builtin_cpp_code_masks)
    sources = select_sources(discover_sources(cfg), args.file)
    if not sources:
        die("No source files found.")

    info(f"Preset: {cfg.preset} ({len(operators)} operators)")
    info(f"Found {len(sources)} source file(s).\n")

    total = 0
    for src in sources:
        abs_path = cfg.resolve(src)
        with open(abs_path) as f:
            content = f.read()
        mutations = generate_mutations(src, content, operators, mask_fn)
        total += len(mutations)
        print(f"{src}: {len(mutations)} mutations")
        for m in mutations:
            print(f"  L{m.line}:{m.col}  {m.description:<20}  {m.original!r} → {m.replacement!r}  ({m.operator})")
        print()

    print(f"Total: {total} mutations across {len(sources)} file(s).")
    print("(dry run — no mutations were applied)")


def cmd_run(args: argparse.Namespace):
    cfg = Config.load(args.config)
    if args.preset:
        cfg.preset = args.preset
    if args.threshold is not None:
        cfg.threshold = args.threshold
    if args.workers is not None:
        cfg.workers = args.workers

    info(f"cerebro — mutation testing")
    info(f"  Language:  {cfg.language}")
    info(f"  Preset:    {cfg.preset}")
    info(f"  Threshold: {cfg.threshold}%")
    if cfg.build_command:
        info(f"  Build:     {cfg.build_command}")
    info(f"  Test:      {cfg.test_command}")
    info("")

    results = run_mutations(cfg, file_filter=args.file)

    # Print text report
    text = report_text(results, cfg.threshold)
    print(text)

    # Write reports
    report_dir = cfg.resolve(cfg.report_dir)
    os.makedirs(report_dir, exist_ok=True)

    json_path = os.path.join(report_dir, "cerebro-report.json")
    with open(json_path, "w") as f:
        json.dump(report_json(results, cfg), f, indent=2)
    info(f"JSON report: {json_path}")

    html_path = os.path.join(report_dir, "cerebro-report.html")
    with open(html_path, "w") as f:
        f.write(report_html(results, cfg))
    info(f"HTML report: {html_path}")

    # Exit code
    overall = compute_score(results)
    if overall["score"] >= cfg.threshold:
        info(f"\nPASS: {overall['score']:.1f}% >= {cfg.threshold}%")
        sys.exit(0)
    else:
        info(f"\nFAIL: {overall['score']:.1f}% < {cfg.threshold}%")
        sys.exit(1)


def cmd_report(args: argparse.Namespace):
    cfg = Config.load(args.config)
    cache = load_cache(cfg)

    # Reconstruct results from cache
    results: list[MutationResult] = []
    for src, file_data in cache.get("files", {}).items():
        for cm in file_data.get("mutations", []):
            mut = Mutation(
                file=src,
                line=cm["line"],
                col=cm["col"],
                end_col=cm.get("end_col", cm["col"]),
                original=cm.get("original", ""),
                replacement=cm.get("replacement", ""),
                operator=cm["op"],
                description=cm.get("description", ""),
            )
            results.append(MutationResult(mutation=mut, status=cm["status"], duration_seconds=cm.get("duration", 0.0)))

    if not results:
        die("No cached results found. Run `cerebro run` first.")

    fmt = args.format or "text"
    if fmt == "text":
        print(report_text(results, cfg.threshold))
    elif fmt == "json":
        print(json.dumps(report_json(results, cfg), indent=2))
    elif fmt == "html":
        print(report_html(results, cfg))
    else:
        die(f"Unknown format: {fmt!r}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    preset_names = list(OPERATOR_PRESETS.keys())
    parser = argparse.ArgumentParser(
        prog="cerebro",
        description="Multi-language source-level mutation testing",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Run mutation testing")
    p_run.add_argument("--config", default="cerebro.json", help="Config file path (default: cerebro.json)")
    p_run.add_argument("--preset", choices=preset_names, help="Override preset")
    p_run.add_argument("--threshold", type=int, help="Override threshold percentage")
    p_run.add_argument("--workers", type=int, help="Number of parallel workers (0=auto, 1=sequential)")
    p_run.add_argument("--file", help="Only test this file (exact path or name; falls back to substring match)")

    # dry-run
    p_dry = sub.add_parser("dry-run", help="List mutations without executing")
    p_dry.add_argument("--config", default="cerebro.json", help="Config file path")
    p_dry.add_argument("--preset", choices=preset_names, help="Override preset")
    p_dry.add_argument("--file", help="Only scan this file (exact path or name; falls back to substring match)")

    # list-operators
    p_ops = sub.add_parser("list-operators", help="Show available mutation operators")
    p_ops.add_argument("--config", default=None, help="Config file path (for language-specific operators)")
    p_ops.add_argument("--preset", choices=preset_names, help="Preset to show (default: STANDARD)")

    # report
    p_rep = sub.add_parser("report", help="Regenerate report from cached results")
    p_rep.add_argument("--config", default="cerebro.json", help="Config file path")
    p_rep.add_argument("--format", choices=["text", "json", "html"], default="text", help="Output format")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "run": cmd_run,
        "dry-run": cmd_dry_run,
        "list-operators": cmd_list_operators,
        "report": cmd_report,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(2)
