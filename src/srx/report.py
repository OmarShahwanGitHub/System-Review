"""Benchmark reports (spec §4, §7). The displayed table is clean values only;
provenance (quote, page, method, confidence) expands on click via <details>.
Self-contained HTML: inline CSS, zero external requests, zero JS."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Optional

from srx.bench import BenchSummary, FieldScore

_COLORS = {
    "correct": "#e6f4ea", "wrong-value": "#fce8e6", "unit-mismatch": "#fef7e0",
    "missed": "#f1f3f4", "spurious-hallucinated": "#fce8e6",
    "spurious-anchored": "#fef7e0", "gold-suspect": "#e8f0fe",
}

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #202124; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { border: 1px solid #dadce0; padding: 6px 10px; font-size: 14px; vertical-align: top; }
th { background: #f8f9fa; text-align: left; }
.cards { display: flex; gap: 1rem; }
.card { border: 1px solid #dadce0; border-radius: 8px; padding: 1rem; min-width: 10rem; }
.card b { font-size: 22px; display: block; }
details summary { cursor: pointer; }
details div { margin-top: 6px; font-size: 12px; color: #5f6368; }
"""


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _esc(x) -> str:
    return html.escape(str(x))


def _cell(score: FieldScore) -> str:
    color = _COLORS.get(score.taxonomy, "#ffffff")
    label = _esc(score.engine_display)
    inner = [f"<i>{_esc(score.taxonomy)}</i>", f"gold: {_esc(score.gold_display)}"]
    if score.provenance:
        p = score.provenance
        inner += [
            f'quote: "{_esc(p.get("quote", ""))}" (page {_esc(p.get("page", "?"))})',
            f"method: {_esc(p.get('method', ''))} · anchor: {_esc(p.get('anchor_status', ''))}"
            f" · confidence: {_esc(p.get('confidence', ''))}",
        ]
    body = "<br>".join(inner)
    return (f'<td style="background:{color}"><details><summary>{label}</summary>'
            f"<div>{body}</div></details></td>")


def render_html(summary: BenchSummary, scores: list[FieldScore], run_id: str,
                prev: Optional[tuple[str, dict[str, Optional[float]]]] = None) -> str:
    parts = [f"<style>{_CSS}</style>"]
    parts.append(f"<h1>Systematic review extraction benchmark — run {_esc(run_id)}</h1>")
    parts.append(
        '<div class="cards">'
        f'<div class="card">Overall agreement<b>{_pct(summary.overall_agreement)}</b></div>'
        f'<div class="card">Numeric-field agreement<b>{_pct(summary.numeric_agreement)}</b></div>'
        f'<div class="card">Scored fields<b>{summary.n_scored}</b></div>'
        f'<div class="card">Studies<b>{summary.n_studies}</b></div>'
        "</div>"
    )

    # per-field table (with Δ vs previous benched run when available)
    delta_header = f"<th>Δ vs {_esc(prev[0])}</th>" if prev else ""
    rows = []
    for fid, stats in summary.per_field.items():
        delta_cell = ""
        if prev:
            prev_agreement = prev[1].get(fid)
            cur = stats["agreement"]
            if prev_agreement is None or cur is None:
                delta_cell = "<td>n/a</td>"
            else:
                d = (cur - prev_agreement) * 100
                delta_cell = f"<td>{'+' if d >= 0 else ''}{d:.1f}%</td>"
        taxonomy = ", ".join(f"{k}: {v}" for k, v in sorted(stats["taxonomy"].items()))
        rows.append(
            f"<tr><td>{_esc(fid)}</td><td>{stats['n']}</td>"
            f"<td>{_pct(stats['agreement'])}</td>{delta_cell}<td>{_esc(taxonomy)}</td></tr>"
        )
    parts.append(
        "<h2>Per-field agreement</h2><table>"
        f"<tr><th>Field</th><th>n</th><th>Agreement</th>{delta_header}<th>Taxonomy</th></tr>"
        + "".join(rows) + "</table>"
    )

    # study × field matrix — clean values, provenance on click (spec §4)
    study_ids = sorted({s.study_id for s in scores})
    field_ids = list(summary.per_field.keys())
    by_key = {(s.study_id, s.field_id): s for s in scores}
    matrix_rows = []
    for sid in study_ids:
        cells = []
        for fid in field_ids:
            score = by_key.get((sid, fid))
            cells.append(_cell(score) if score else "<td></td>")
        matrix_rows.append(f"<tr><th>{_esc(sid)}</th>{''.join(cells)}</tr>")
    parts.append(
        "<h2>Study × field matrix</h2>"
        "<p>Click a value to see its provenance (quote, page, method, confidence).</p>"
        "<table><tr><th>Study</th>" + "".join(f"<th>{_esc(f)}</th>" for f in field_ids)
        + "</tr>" + "".join(matrix_rows) + "</table>"
    )
    return "".join(parts)


def render_markdown(summary: BenchSummary, run_id: str) -> str:
    lines = [
        f"# Systematic review extraction benchmark — run {run_id}",
        "",
        f"- Overall agreement: {_pct(summary.overall_agreement)}",
        f"- Numeric-field agreement: {_pct(summary.numeric_agreement)}",
        f"- Scored fields: {summary.n_scored} across {summary.n_studies} studies",
        "",
        "| Field | n | Agreement |",
        "|---|---|---|",
    ]
    for fid, stats in summary.per_field.items():
        lines.append(f"| {fid} | {stats['n']} | {_pct(stats['agreement'])} |")
    return "\n".join(lines)


def write_report(run_dir: Path, html_text: str) -> Path:
    path = Path(run_dir) / "report.html"
    path.write_text(html_text, encoding="utf-8")
    return path
