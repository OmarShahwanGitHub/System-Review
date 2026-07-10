import json

import pytest

from srx.runstore import RunStore, new_run_id


def test_new_run_id_format_and_uniqueness():
    a, b = new_run_id(), new_run_id()
    assert len(a.split("-")) == 3
    assert a != b


def test_run_lifecycle(tmp_path):
    store = RunStore(tmp_path / "sr.sqlite")
    store.create_run("r1", schema_hash="abc", models={"extract": "m1"}, prompt_version="p1")
    with pytest.raises(Exception):
        store.create_run("r1", schema_hash="abc", models={}, prompt_version="p1")
    store.record_study("r1", "smith-2019", "done", 0.42, "runs/r1/studies/smith-2019.json")
    store.record_study("r1", "jones-2020", "errored", 0.10, "")
    assert store.study_status("r1", "smith-2019") == "done"
    assert store.study_status("r1", "nope") is None
    store.finish_run("r1", "done", 0.52, {"studies": 2})
    row = store.run_row("r1")
    assert row["status"] == "done"
    assert row["cost_usd"] == pytest.approx(0.52)
    assert json.loads(row["models_json"]) == {"extract": "m1"}
    assert store.latest_run()["run_id"] == "r1"
    store.close()


def test_bench_rows_and_previous_benched(tmp_path):
    store = RunStore(tmp_path / "sr.sqlite")
    store.create_run("r1", "h", {}, "p1")
    store.finish_run("r1", "done", 0.0, {})
    store.create_run("r2", "h", {}, "p1")
    store.finish_run("r2", "done", 0.0, {})
    store.record_bench("r1", {"n_total": {"n": 10, "agreement": 0.9, "taxonomy": {"correct": 9}}})
    store.record_bench("r2", {"n_total": {"n": 10, "agreement": 1.0, "taxonomy": {"correct": 10}}})
    rows = store.bench_rows("r1")
    assert rows["n_total"]["agreement"] == pytest.approx(0.9)
    assert json.loads(rows["n_total"]["taxonomy_json"]) == {"correct": 9}
    assert store.previous_benched_run("r2") == "r1"
    assert store.previous_benched_run("r1") is None
    store.close()
