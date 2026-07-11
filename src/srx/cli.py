"""`sr` CLI (spec §5): extract, bench, report, schema init."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from srx.bench import BenchSummary, FieldScore, aggregate, exact_judge, make_judge, score_all
from srx.claude_client import ClaudeClient, DEFAULT_PRICING
from srx.goldload import GoldError, load_gold
from srx.pipeline import config_from_project, load_run_extractions, run_extraction
from srx.project import ProjectError, open_project
from srx.report import render_html, render_markdown, write_report
from srx.runstore import RunStore
from srx.schema import SchemaError, draft_schema_from_csv, load_schema


def _make_client(project, client_factory):
    if client_factory is not None:
        return client_factory()
    overrides = {k: tuple(v) for k, v in (project.config.get("pricing") or {}).items()}
    return ClaudeClient(pricing={**DEFAULT_PRICING, **overrides})


def _resolve_run(store: RunStore, run_id: str | None) -> str:
    if run_id:
        if store.run_row(run_id) is None:
            raise ProjectError(f"run '{run_id}' not found")
        return run_id
    row = store.latest_run()
    if row is None:
        raise ProjectError("no runs yet — run `sr extract` first")
    return row["run_id"]


def _cmd_extract(args, client_factory) -> int:
    project = open_project(Path(args.project))
    config = config_from_project(
        project, extract_model=args.model, judge_model=args.judge_model,
        cost_cap_usd=args.cost_cap, resume=args.resume)
    store = RunStore(project.db_path)
    client = _make_client(project, client_factory)
    run_id = run_extraction(project, config, client, store)
    extractions = load_run_extractions(project.runs_dir / run_id)
    n_flagged = sum(len(e.flagged) for e in extractions)
    row = store.run_row(run_id)
    print(f"run {run_id}: {len(extractions)} studies extracted "
          f"({n_flagged} flagged fields for human review), "
          f"status {row['status']}, cost ${row['cost_usd']:.2f}")
    store.close()
    return 0


def _cmd_bench(args, client_factory) -> int:
    project = open_project(Path(args.project))
    if not project.gold_csv.is_file() or not project.mapping_yaml.is_file():
        raise ProjectError(
            f"benchmark needs {project.gold_csv} and {project.mapping_yaml} — "
            "export your human extraction sheet to CSV and map its columns")
    schema = load_schema(project.schema_path)
    store = RunStore(project.db_path)
    run_id = _resolve_run(store, args.run)
    run_dir = project.runs_dir / run_id
    extractions = load_run_extractions(run_dir)
    gold = load_gold(project.gold_csv, project.mapping_yaml, schema)
    missing = sorted(set(gold) - {e.study_id for e in extractions})
    if missing:
        print(
            f"warning: {len(missing)} gold study id(s) have no extraction and "
            f"score as missed: {', '.join(missing)}",
            file=sys.stderr,
        )
    config = config_from_project(project)
    judge = exact_judge if args.no_judge else make_judge(_make_client(project, client_factory), config.judge_model)
    scores = score_all(extractions, gold, schema, judge)
    summary = aggregate(scores, schema)
    store.record_bench(run_id, summary.per_field)
    (run_dir / "scores.json").write_text(json.dumps({
        "summary": summary.to_dict(),
        "scores": [s.to_dict() for s in scores],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    prev = _previous_agreements(store, run_id)
    path = write_report(run_dir, render_html(summary, scores, run_id, prev))
    print(render_markdown(summary, run_id))
    print(f"\nreport: {path}")
    store.close()
    return 0


def _previous_agreements(store: RunStore, run_id: str):
    prev_id = store.previous_benched_run(run_id)
    if prev_id is None:
        return None
    return prev_id, {fid: row["agreement"] for fid, row in store.bench_rows(prev_id).items()}


def _cmd_report(args, client_factory) -> int:
    project = open_project(Path(args.project))
    store = RunStore(project.db_path)
    run_id = _resolve_run(store, args.run)
    run_dir = project.runs_dir / run_id
    scores_path = run_dir / "scores.json"
    if not scores_path.is_file():
        raise ProjectError(f"run '{run_id}' has no scores.json — run `sr bench` first")
    data = json.loads(scores_path.read_text(encoding="utf-8"))
    summary = BenchSummary.from_dict(data["summary"])
    scores = [FieldScore.from_dict(d) for d in data["scores"]]
    path = write_report(run_dir, render_html(summary, scores, run_id,
                                             _previous_agreements(store, run_id)))
    print(f"report: {path}")
    store.close()
    return 0


def _cmd_schema_init(args, client_factory) -> int:
    root = Path(args.project)
    target = root / "schema.yaml"
    if target.exists() and not args.force:
        raise ProjectError(f"{target} exists — pass --force to overwrite")
    source = Path(args.from_csv) if args.from_csv else root / "gold" / "gold.csv"
    if not source.is_file():
        raise ProjectError(f"CSV not found: {source}")
    target.write_text(draft_schema_from_csv(source), encoding="utf-8")
    print(f"drafted {target} from {source} — now hand-refine types, units, "
          f"allowed values, and descriptions before extracting")
    return 0


def main(argv=None, client_factory=None) -> int:
    parser = argparse.ArgumentParser(
        prog="sr", description="Systematic review extraction engine")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="run dual-pass extraction over studies/")
    p.add_argument("--project", default=".")
    p.add_argument("--resume", default=None)
    p.add_argument("--cost-cap", type=float, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--judge-model", default=None)
    p.set_defaults(func=_cmd_extract)

    p = sub.add_parser("bench", help="score a run against the gold-standard extraction")
    p.add_argument("--project", default=".")
    p.add_argument("--run", default=None)
    p.add_argument("--no-judge", action="store_true",
                   help="judge text fields by exact match instead of a model call")
    p.set_defaults(func=_cmd_bench)

    p = sub.add_parser("report", help="re-render report.html from scores.json")
    p.add_argument("--project", default=".")
    p.add_argument("--run", default=None)
    p.set_defaults(func=_cmd_report)

    p_schema = sub.add_parser("schema", help="schema utilities")
    schema_sub = p_schema.add_subparsers(dest="schema_command", required=True)
    p = schema_sub.add_parser("init", help="draft schema.yaml from a gold CSV's headers")
    p.add_argument("--project", default=".")
    p.add_argument("--from", dest="from_csv", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_schema_init)

    args = parser.parse_args(argv)
    try:
        return args.func(args, client_factory)
    except (ProjectError, GoldError, SchemaError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
