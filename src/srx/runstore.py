"""Append-only run registry (spec §6.7). Every run records inputs hash, models,
prompt version, cost, and benchmark scores so any prompt/model change can be
diffed run-over-run. Rows are never deleted."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  schema_hash TEXT NOT NULL,
  models_json TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS study_runs (
  run_id TEXT NOT NULL,
  study_id TEXT NOT NULL,
  status TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  json_path TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, study_id)
);
CREATE TABLE IF NOT EXISTS bench_scores (
  run_id TEXT NOT NULL,
  field_id TEXT NOT NULL,
  n INTEGER NOT NULL,
  agreement REAL,
  taxonomy_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, field_id)
);
"""


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class RunStore:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def create_run(self, run_id: str, schema_hash: str, models: dict, prompt_version: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, created_at, status, schema_hash, models_json, prompt_version)"
            " VALUES (?, ?, 'running', ?, ?, ?)",
            (run_id, datetime.now(timezone.utc).isoformat(), schema_hash,
             json.dumps(models), prompt_version),
        )
        self._conn.commit()

    def finish_run(self, run_id: str, status: str, cost_usd: float, summary: dict) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, cost_usd = ?, summary_json = ? WHERE run_id = ?",
            (status, cost_usd, json.dumps(summary), run_id),
        )
        self._conn.commit()

    def record_study(self, run_id: str, study_id: str, status: str,
                     cost_usd: float, json_path: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO study_runs (run_id, study_id, status, cost_usd, json_path)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, study_id, status, cost_usd, json_path),
        )
        self._conn.commit()

    def study_status(self, run_id: str, study_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT status FROM study_runs WHERE run_id = ? AND study_id = ?",
            (run_id, study_id),
        ).fetchone()
        return row["status"] if row else None

    def record_bench(self, run_id: str, rows: dict[str, dict]) -> None:
        for field_id, r in rows.items():
            self._conn.execute(
                "INSERT OR REPLACE INTO bench_scores (run_id, field_id, n, agreement, taxonomy_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, field_id, r["n"], r["agreement"], json.dumps(r["taxonomy"])),
            )
        self._conn.commit()

    def bench_rows(self, run_id: str) -> dict[str, sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT * FROM bench_scores WHERE run_id = ?", (run_id,)
        ).fetchall()
        return {r["field_id"]: r for r in rows}

    def run_row(self, run_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def latest_run(self) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    def previous_benched_run(self, before_run_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT r.run_id FROM runs r"
            " WHERE r.created_at < (SELECT created_at FROM runs WHERE run_id = ?)"
            "   AND EXISTS (SELECT 1 FROM bench_scores b WHERE b.run_id = r.run_id)"
            " ORDER BY r.created_at DESC LIMIT 1",
            (before_run_id,),
        ).fetchone()
        return row["run_id"] if row else None
