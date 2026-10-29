import io
import json

import pandas as pd
import pytest

from drive_export import (
    DRIVE_CREATED,
    DRIVE_ERROR,
    DRIVE_FILE_ID_NAME,
    DRIVE_FILE_NAME,
    DRIVE_NOT_CONFIGURED,
    DRIVE_UPDATED,
    ENV_CREDENTIALS,
    ENV_FOLDER_ID,
    export_run_json,
    run_json_to_csv_bytes,
)

ENV = {ENV_FOLDER_ID: "folder123", ENV_CREDENTIALS: "/secrets/sa.json"}


class _Call:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class FakeFiles:
    def __init__(self, existing=None, fail=None):
        self.store = {}
        self.calls = []
        self.existing = existing
        self.fail = fail

    @staticmethod
    def _body(media):
        return media.getbytes(0, media.size()).decode("utf-8")

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Call({"files": [{"id": self.existing}] if self.existing else []})

    def create(self, body, media_body, **kwargs):
        self.calls.append(("create", body))
        if self.fail:
            return _Call(error=self.fail)
        self.store["fake-id-1"] = self._body(media_body)
        return _Call({"id": "fake-id-1"})

    def update(self, fileId, media_body, **kwargs):
        self.calls.append(("update", fileId))
        self.store[fileId] = self._body(media_body)
        return _Call({"id": fileId})


class FakeService:
    def __init__(self, **kwargs):
        self._files = FakeFiles(**kwargs)

    def files(self):
        return self._files


def write_run(path, records):
    path.write_text(json.dumps(records, indent=2))
    return str(path)


def record(part, trigger, need):
    rec = {
        "audit_run_id": "walk_1", "part_id": part, "decision_code": "ACCEPTED_DEFICIT_FULFILLED",
        "demand_to_fulfil": float(trigger), "need_to_be_picked": float(need), "pss_boxes_to_pick": 1,
    }
    rec["raw_json"] = json.dumps(rec)
    return rec


def test_run_json_becomes_csv_with_every_key_including_raw_json(tmp_path):
    path = write_run(tmp_path / "walk_1.json", [record("PARTA", 30, 50), record("PARTB", 0, 0)])
    df = pd.read_csv(io.BytesIO(run_json_to_csv_bytes(path)))
    assert list(df.columns) == [
        "audit_run_id", "part_id", "decision_code", "demand_to_fulfil",
        "need_to_be_picked", "pss_boxes_to_pick", "raw_json",
    ]
    assert df.loc[0, "demand_to_fulfil"] == 30
    assert json.loads(df.loc[0, "raw_json"])["part_id"] == "PARTA"


def test_first_call_creates_then_second_updates_same_file_id(tmp_path):
    service = FakeService()
    first = write_run(tmp_path / "walk_1.json", [record("PARTA", 30, 50)])
    r1 = export_run_json(first, str(tmp_path), environ=ENV, service=service)
    assert (r1.status, r1.file_id) == (DRIVE_CREATED, "fake-id-1")
    create_body = next(c[1] for c in service.files().calls if c[0] == "create")
    assert create_body["name"] == DRIVE_FILE_NAME and create_body["parents"] == ["folder123"]
    assert (tmp_path / DRIVE_FILE_ID_NAME).read_text() == "fake-id-1"

    second = write_run(tmp_path / "walk_2.json", [record("PARTC", 90, 100)])
    r2 = export_run_json(second, str(tmp_path), environ=ENV, service=service)
    assert (r2.status, r2.file_id) == (DRIVE_UPDATED, "fake-id-1")
    assert [c for c in service.files().calls if c[0] == "update"] == [("update", "fake-id-1")]
    assert len([c for c in service.files().calls if c[0] == "create"]) == 1
    content = pd.read_csv(io.StringIO(service.files().store["fake-id-1"]))
    assert content["part_id"].tolist() == ["PARTC"]


def test_existing_picklist_in_folder_is_adopted(tmp_path):
    service = FakeService(existing="user-made-id")
    path = write_run(tmp_path / "walk_1.json", [record("PARTA", 30, 50)])
    r = export_run_json(path, str(tmp_path), environ=ENV, service=service)
    assert (r.status, r.file_id) == (DRIVE_UPDATED, "user-made-id")
    assert (tmp_path / DRIVE_FILE_ID_NAME).read_text() == "user-made-id"


@pytest.mark.parametrize("env", [{}, {ENV_FOLDER_ID: "f"}, {ENV_CREDENTIALS: "/k.json"}])
def test_missing_settings_are_not_configured_and_do_not_raise(tmp_path, env):
    path = write_run(tmp_path / "walk_1.json", [record("PARTA", 30, 50)])
    service = FakeService()
    r = export_run_json(path, str(tmp_path), environ=env, service=service)
    assert r.status == DRIVE_NOT_CONFIGURED
    assert "not configured" in r.message
    assert service.files().calls == []


def test_drive_error_is_returned_not_raised(tmp_path):
    path = write_run(tmp_path / "walk_1.json", [record("PARTA", 30, 50)])
    r = export_run_json(path, str(tmp_path), environ=ENV, service=FakeService(fail=RuntimeError("quota")))
    assert r.status == DRIVE_ERROR
    assert "quota" in r.message
    assert not (tmp_path / DRIVE_FILE_ID_NAME).exists()
