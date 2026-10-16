import csv
import json

from picklist_drive import (
    DEFAULT_PICKLIST_DRIVE_DIR,
    ENV_PICKLIST_DRIVE_DIR,
    PICKLIST_ERROR,
    PICKLIST_SKIPPED,
    append_run_json,
    picklist_drive_dir,
)


def _write_run(path, records):
    path.write_text(json.dumps(records), encoding="utf-8")
    return str(path)


def _record(run_id, part, trigger, **extra):
    row = {
        "audit_run_id": run_id,
        "part_id": part,
        "trigger": trigger,
        "decision_code": extra.get("decision_code", "ACCEPTED_DEFICIT_FULFILLED"),
    }
    row["raw_json"] = json.dumps({"audit_run_id": run_id, "part_id": part, "trigger": trigger})
    return row


def test_default_folder_is_the_drive_desktop_picklist():
    assert picklist_drive_dir({}) == DEFAULT_PICKLIST_DRIVE_DIR
    assert DEFAULT_PICKLIST_DRIVE_DIR == r"G:\My Drive\Picklist"


def test_two_run_json_payloads_append_into_one_csv(tmp_path):
    drive = tmp_path / "Picklist"
    drive.mkdir()
    vault = tmp_path / "audit_vault"
    (vault / "runs_json").mkdir(parents=True)
    # A decoy the append must ignore. The walk reads the JSON file, not runs_csv.
    (vault / "runs_csv").mkdir()
    (vault / "runs_csv" / "walk_a.csv").write_text("audit_run_id,part_id\nDECOY,NOPE\n", encoding="utf-8")

    walk_a = [
        _record("walk_a", "PARTA", 0, decision_code="REJECTED_SUFFICIENT_M002"),
        _record("walk_a", "PARTB", 20),
    ]
    walk_b = [
        {
            "raw_json": json.dumps({"trigger": 0, "part_id": "PARTC"}),
            "trigger": 0,
            "part_id": "PARTC",
            "audit_run_id": "walk_b",
            "decision_code": "REJECTED_COVERED_BY_STO",
        },
        {
            "decision_code": "ACCEPTED_DEFICIT_FULFILLED",
            "audit_run_id": "walk_b",
            "part_id": "PARTD",
            "trigger": 30,
            "raw_json": json.dumps({"trigger": 30, "part_id": "PARTD"}),
        },
    ]
    first = _write_run(vault / "runs_json" / "walk_a.json", walk_a)
    second = _write_run(vault / "runs_json" / "walk_b.json", walk_b)
    env = {ENV_PICKLIST_DRIVE_DIR: str(drive)}

    first_result = append_run_json(first, environ=env)
    second_result = append_run_json(second, environ=env)

    assert first_result.ok and second_result.ok
    csv_path = drive / "picklist.csv"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        parsed = list(csv.reader(handle))
    header = parsed[0]
    assert "raw_json" in header
    assert "audit_run_id" in header
    assert "trigger" in header
    assert parsed.count(header) == 1
    assert len(parsed) == 5

    rows = [dict(zip(header, row)) for row in parsed[1:]]
    assert {row["audit_run_id"] for row in rows} == {"walk_a", "walk_b"}
    assert any(row["trigger"] == "0" for row in rows)
    assert any(row["audit_run_id"] == "walk_a" and row["trigger"] == "0" for row in rows)
    assert all("DECOY" not in row["audit_run_id"] for row in rows)
    assert json.loads((drive / "walk_a.json").read_text(encoding="utf-8"))[0]["part_id"] == "PARTA"
    assert json.loads((drive / "walk_b.json").read_text(encoding="utf-8"))[0]["audit_run_id"] == "walk_b"
    # The second append must not replace the first walk's rows.
    assert [row["part_id"] for row in rows] == ["PARTA", "PARTB", "PARTC", "PARTD"]


def test_missing_folder_skips_without_raising(tmp_path):
    missing = tmp_path / "not-there"
    src = tmp_path / "walk_a.json"
    _write_run(src, [_record("walk_a", "PARTA", 0)])
    result = append_run_json(str(src), environ={ENV_PICKLIST_DRIVE_DIR: str(missing)})
    assert result.status == PICKLIST_SKIPPED
    assert str(missing) in result.message
    assert not missing.exists()
    assert not (tmp_path / "picklist.csv").exists()


def test_write_error_does_not_raise(tmp_path):
    drive = tmp_path / "Picklist"
    drive.mkdir()
    (drive / "picklist.csv").mkdir()
    src = tmp_path / "walk_a.json"
    _write_run(src, [_record("walk_a", "PARTA", 0)])
    result = append_run_json(str(src), environ={ENV_PICKLIST_DRIVE_DIR: str(drive)})
    assert result.status == PICKLIST_ERROR
    assert "failed" in result.message
