"""Project directory layout (spec §5). A project is plain files the user owns:
studies/*.pdf stay local and are sent only to the model API."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ProjectError(Exception):
    pass


@dataclass
class Project:
    root: Path
    studies_dir: Path
    schema_path: Path
    gold_csv: Path
    mapping_yaml: Path
    runs_dir: Path
    db_path: Path
    config: dict = field(default_factory=dict)


def open_project(root: Path) -> Project:
    root = Path(root).resolve()
    studies = root / "studies"
    schema_path = root / "schema.yaml"
    if not studies.is_dir() or not list(studies.glob("*.pdf")):
        raise ProjectError(f"{root}: no studies/ directory with PDFs — see README project layout")
    if not schema_path.is_file():
        raise ProjectError(f"{root}: missing schema.yaml (run `sr schema init` to draft one)")
    runs = root / "runs"
    runs.mkdir(exist_ok=True)
    config_path = root / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    return Project(
        root=root, studies_dir=studies, schema_path=schema_path,
        gold_csv=root / "gold" / "gold.csv", mapping_yaml=root / "gold" / "mapping.yaml",
        runs_dir=runs, db_path=root / "sr.sqlite", config=config or {},
    )
