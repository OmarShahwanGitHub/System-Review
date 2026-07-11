"""Extraction-run orchestration (spec §6, §8): per-study checkpoints, resume
without re-extraction, clean cost-cap abort, per-study error isolation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from srx.claude_client import ClaudeError
from srx.extractor import PROMPT_VERSION, extract_study
from srx.ingest import load_pdf
from srx.models import StudyExtraction
from srx.project import Project, ProjectError
from srx.reconcile import reconcile_study
from srx.runstore import RunStore, new_run_id
from srx.schema import load_schema


@dataclass
class RunConfig:
    extract_model: str = "claude-fable-5"
    judge_model: str = "claude-sonnet-5"
    cost_cap_usd: float = 20.0
    resume: Optional[str] = None


def config_from_project(project: Project, **overrides) -> RunConfig:
    merged = {}
    for key in ("extract_model", "judge_model", "cost_cap_usd"):
        if key in project.config:
            merged[key] = project.config[key]
    merged.update({k: v for k, v in overrides.items() if v is not None})
    return RunConfig(**merged)


def run_extraction(project: Project, config: RunConfig, client, store: RunStore) -> str:
    schema = load_schema(project.schema_path)
    run_id = config.resume or new_run_id()

    if config.resume:
        row = store.run_row(run_id)
        if row is None:
            raise ProjectError(f"cannot resume: run '{run_id}' not found")
        if row["schema_hash"] != schema.hash():
            raise ProjectError(
                f"cannot resume run '{run_id}': schema.yaml has changed since the "
                "run started — start a new run so results stay comparable")
    else:
        store.create_run(
            run_id, schema_hash=schema.hash(),
            models={"extract": config.extract_model, "judge": config.judge_model},
            prompt_version=PROMPT_VERSION,
        )
    run_dir = project.runs_dir / run_id
    ck_dir = run_dir / "studies"
    ck_dir.mkdir(parents=True, exist_ok=True)

    # cost so far (resume case) comes from existing checkpoints
    total_cost = sum(
        StudyExtraction.from_json(p.read_text(encoding="utf-8")).cost_usd
        for p in ck_dir.glob("*.json")
    )
    status = "done"
    for pdf_path in sorted(project.studies_dir.glob("*.pdf")):
        study_id = pdf_path.stem
        ck = ck_dir / f"{study_id}.json"
        if ck.exists():
            continue  # resume never re-extracts (spec §8)
        if total_cost >= config.cost_cap_usd:
            status = "cost-capped"
            print(f"cost cap ${config.cost_cap_usd:.2f} reached — stopping cleanly; "
                  f"resume with: sr extract --resume {run_id}")
            break
        try:
            doc = load_pdf(pdf_path)
            pdf_bytes = pdf_path.read_bytes()
            pass_a, pass_b, usage = extract_study(
                client, config.extract_model, pdf_bytes, study_id, schema)
            # Adjudication runs on extract_model deliberately: resolving a pass
            # disagreement is the hardest read, so it gets the strongest model.
            # judge_model is bench's semantic text judge (cli._cmd_bench), not
            # the adjudicator.
            accepted, flagged, not_found, adj_usage = reconcile_study(
                schema=schema, pdf=doc, pass_a=pass_a, pass_b=pass_b,
                client=client, model=config.extract_model)
            usage.add(adj_usage)
            extraction = StudyExtraction(
                study_id=study_id, scanned=doc.scanned, accepted=accepted,
                flagged=flagged, not_found=not_found, cost_usd=usage.cost_usd)
            ck.write_text(extraction.to_json(), encoding="utf-8")
            total_cost += usage.cost_usd
            store.record_study(run_id, study_id, "done", usage.cost_usd, str(ck))
        except ClaudeError as e:
            print(f"study {study_id} errored: {e}")
            store.record_study(run_id, study_id, "errored", 0.0, "")

    extractions = sorted(
        (StudyExtraction.from_json(p.read_text(encoding="utf-8")) for p in ck_dir.glob("*.json")),
        key=lambda s: s.study_id,
    )
    (run_dir / "extraction.json").write_text(
        json.dumps([s.to_dict() for s in extractions], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    store.finish_run(run_id, status, total_cost, {"studies": len(extractions)})
    return run_id


def load_run_extractions(run_dir: Path) -> list[StudyExtraction]:
    data = json.loads((Path(run_dir) / "extraction.json").read_text(encoding="utf-8"))
    return [StudyExtraction.from_dict(d) for d in data]
