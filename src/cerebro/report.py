"""Scoring and report generation (text, JSON, HTML)."""

from __future__ import annotations

import html as html_module
import textwrap

from cerebro.types import MutationResult, KILLED, SURVIVED, BUILD_ERROR, TIMEOUT
from cerebro.config import Config


def compute_score(results: list[MutationResult]) -> dict:
    """Compute summary statistics from mutation results."""
    total = len(results)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    killed = by_status.get(KILLED, 0)
    survived = by_status.get(SURVIVED, 0)
    build_errors = by_status.get(BUILD_ERROR, 0)
    timeouts = by_status.get(TIMEOUT, 0)
    testable = killed + survived
    score = (killed / testable * 100) if testable > 0 else 0.0

    return {
        "total": total,
        "killed": killed,
        "survived": survived,
        "build_errors": build_errors,
        "timeouts": timeouts,
        "testable": testable,
        "score": score,
    }


def report_text(results: list[MutationResult], threshold: int) -> str:
    """Generate a terminal-friendly text report."""
    lines: list[str] = []

    # Per-file breakdown
    files: dict[str, list[MutationResult]] = {}
    for r in results:
        files.setdefault(r.mutation.file, []).append(r)

    lines.append("")
    lines.append("=" * 80)
    lines.append("MUTATION TESTING REPORT")
    lines.append("=" * 80)
    lines.append("")

    header = f"{'File':<45} {'Mut':>4} {'Kill':>4} {'Surv':>4} {'Err':>4} {'TO':>3} {'Score':>6}"
    lines.append(header)
    lines.append("-" * len(header))

    for fpath in sorted(files.keys()):
        frs = files[fpath]
        s = compute_score(frs)
        short = fpath if len(fpath) <= 44 else "..." + fpath[-41:]
        lines.append(
            f"{short:<45} {s['total']:>4} {s['killed']:>4} {s['survived']:>4} "
            f"{s['build_errors']:>4} {s['timeouts']:>3} {s['score']:>5.1f}%"
        )

    lines.append("-" * len(header))
    overall = compute_score(results)
    lines.append(
        f"{'TOTAL':<45} {overall['total']:>4} {overall['killed']:>4} {overall['survived']:>4} "
        f"{overall['build_errors']:>4} {overall['timeouts']:>3} {overall['score']:>5.1f}%"
    )
    lines.append("")

    # Survived mutants detail
    survived = [r for r in results if r.status == SURVIVED]
    if survived:
        lines.append(f"SURVIVED MUTANTS ({len(survived)}):")
        lines.append("-" * 60)
        for r in sorted(survived, key=lambda x: (x.mutation.file, x.mutation.line)):
            lines.append(
                f"  {r.mutation.file}:{r.mutation.line}:{r.mutation.col}  "
                f"{r.mutation.description}  ({r.mutation.operator})"
            )
        lines.append("")

    # Threshold check
    passed = overall["score"] >= threshold
    status = "PASS" if passed else "FAIL"
    lines.append(f"Mutation score: {overall['score']:.1f}% (threshold: {threshold}%)  [{status}]")
    lines.append("")

    return "\n".join(lines)


def report_json(results: list[MutationResult], cfg: Config) -> dict:
    """Generate a JSON-serializable report."""
    files: dict[str, list[dict]] = {}
    for r in results:
        entry = {
            "line": r.mutation.line,
            "col": r.mutation.col,
            "original": r.mutation.original,
            "replacement": r.mutation.replacement,
            "operator": r.mutation.operator,
            "description": r.mutation.description,
            "status": r.status,
            "duration_seconds": r.duration_seconds,
        }
        if r.detail:
            entry["detail"] = r.detail
        files.setdefault(r.mutation.file, []).append(entry)

    overall = compute_score(results)
    return {
        "preset": cfg.preset,
        "threshold": cfg.threshold,
        "summary": overall,
        "files": {k: sorted(v, key=lambda x: (x["line"], x["col"])) for k, v in sorted(files.items())},
    }


def report_html(results: list[MutationResult], cfg: Config) -> str:
    """Generate a self-contained HTML report with source annotations."""
    esc = html_module.escape
    overall = compute_score(results)

    # Group by file
    files: dict[str, list[MutationResult]] = {}
    for r in results:
        files.setdefault(r.mutation.file, []).append(r)

    # Read source files for annotation
    file_sources: dict[str, list[str]] = {}
    for fpath in files:
        abs_path = cfg.resolve(fpath)
        try:
            with open(abs_path) as f:
                file_sources[fpath] = f.read().splitlines()
        except FileNotFoundError:
            file_sources[fpath] = []

    parts: list[str] = []
    parts.append(textwrap.dedent("""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="utf-8">
    <title>cerebro Report</title>
    <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
           margin: 2em; background: #1a1a2e; color: #eee; }
    h1 { color: #e94560; }
    h2 { color: #0f3460; background: #16213e; padding: 0.5em; border-radius: 4px; color: #eee; }
    .summary { background: #16213e; padding: 1em; border-radius: 8px; margin-bottom: 2em; }
    .summary td { padding: 0.3em 1em; }
    .score { font-size: 2em; font-weight: bold; }
    .pass { color: #4ecca3; }
    .fail { color: #e94560; }
    table.files { border-collapse: collapse; width: 100%; margin: 1em 0; }
    table.files th, table.files td { padding: 0.4em 0.8em; text-align: left;
                                      border-bottom: 1px solid #333; }
    table.files th { background: #16213e; }
    .source { background: #0a0a1a; padding: 0; margin: 1em 0; border-radius: 4px;
              overflow-x: auto; font-size: 0.85em; }
    .source table { border-collapse: collapse; width: 100%; }
    .source td { padding: 0 0.8em; white-space: pre; font-family: monospace; }
    .source .ln { color: #555; text-align: right; user-select: none; width: 3em;
                  border-right: 1px solid #333; padding-right: 0.5em; }
    .source .killed { background: rgba(78, 204, 163, 0.15); }
    .source .survived { background: rgba(233, 69, 96, 0.2); }
    .source .both { background: rgba(233, 165, 69, 0.2); }
    .badge { display: inline-block; padding: 0.15em 0.5em; border-radius: 3px;
             font-size: 0.8em; margin: 0.1em; }
    .badge.killed { background: #4ecca3; color: #000; }
    .badge.survived { background: #e94560; color: #fff; }
    .badge.build_error { background: #666; color: #fff; }
    .badge.timeout { background: #f0a500; color: #000; }
    details { margin: 0.5em 0; }
    summary { cursor: pointer; color: #4ecca3; }
    </style>
    </head>
    <body>
    """))

    score_class = "pass" if overall["score"] >= cfg.threshold else "fail"
    parts.append(f'<h1>cerebro Report</h1>')
    parts.append(f'<div class="summary"><table>')
    parts.append(
        f'<tr><td>Mutation Score</td><td class="score {score_class}">{overall["score"]:.1f}%</td></tr>'
    )
    parts.append(f'<tr><td>Threshold</td><td>{cfg.threshold}%</td></tr>')
    parts.append(f'<tr><td>Total Mutants</td><td>{overall["total"]}</td></tr>')
    parts.append(f'<tr><td>Killed</td><td>{overall["killed"]}</td></tr>')
    parts.append(f'<tr><td>Survived</td><td>{overall["survived"]}</td></tr>')
    parts.append(f'<tr><td>Build Errors</td><td>{overall["build_errors"]}</td></tr>')
    parts.append(f'<tr><td>Timeouts</td><td>{overall["timeouts"]}</td></tr>')
    parts.append(f'<tr><td>Preset</td><td>{esc(cfg.preset)}</td></tr>')
    parts.append(f'</table></div>')

    # File summary table
    parts.append('<table class="files">')
    parts.append("<tr><th>File</th><th>Mutants</th><th>Killed</th><th>Survived</th><th>Score</th></tr>")
    for fpath in sorted(files.keys()):
        s = compute_score(files[fpath])
        sc = "pass" if s["score"] >= cfg.threshold else "fail"
        parts.append(
            f'<tr><td><a href="#{esc(fpath)}">{esc(fpath)}</a></td>'
            f'<td>{s["total"]}</td><td>{s["killed"]}</td><td>{s["survived"]}</td>'
            f'<td class="{sc}">{s["score"]:.1f}%</td></tr>'
        )
    parts.append("</table>")

    # Per-file source annotations
    for fpath in sorted(files.keys()):
        parts.append(f'<h2 id="{esc(fpath)}">{esc(fpath)}</h2>')
        source_lines = file_sources.get(fpath, [])
        frs = files[fpath]

        # Map line -> mutations
        line_mutations: dict[int, list[MutationResult]] = {}
        for r in frs:
            line_mutations.setdefault(r.mutation.line, []).append(r)

        parts.append('<div class="source"><table>')
        for line_num, line_text in enumerate(source_lines, 1):
            muts = line_mutations.get(line_num, [])
            statuses = {m.status for m in muts}
            if SURVIVED in statuses and KILLED in statuses:
                cls = "both"
            elif SURVIVED in statuses:
                cls = "survived"
            elif KILLED in statuses:
                cls = "killed"
            else:
                cls = ""

            badges = ""
            if muts:
                badge_parts = []
                for m in sorted(muts, key=lambda x: x.mutation.col):
                    badge_parts.append(
                        f'<span class="badge {m.status}" title="{esc(m.mutation.description)}: '
                        f'{esc(m.mutation.original)} → {esc(m.mutation.replacement)}">'
                        f"{esc(m.status[0].upper())}</span>"
                    )
                badges = " " + "".join(badge_parts)

            parts.append(
                f'<tr class="{cls}"><td class="ln">{line_num}</td>'
                f"<td>{esc(line_text)}{badges}</td></tr>"
            )
        parts.append("</table></div>")

    parts.append("</body></html>")
    return "\n".join(parts)
