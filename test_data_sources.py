import pandas as pd
import pytest

from data_sources import (
    DATABRICKS_SETTINGS,
    DatabricksSettings,
    DatabricksSource,
    DataSourceNotConfigured,
    SapUploadSource,
)


def test_sap_upload_snapshot_id_tracks_content():
    mb52 = pd.DataFrame({"Material": ["A"], "Unrestricted": ["10"]})
    a = SapUploadSource(mb52, pd.DataFrame(), pd.DataFrame()).load()
    b = SapUploadSource(mb52.copy(), pd.DataFrame(), pd.DataFrame()).load()
    c = SapUploadSource(mb52.assign(Unrestricted=["11"]), pd.DataFrame(), pd.DataFrame()).load()
    assert a.snapshot_id == b.snapshot_id
    assert a.snapshot_id != c.snapshot_id
    assert a.mb52 is mb52


def test_databricks_settings_need_every_value():
    assert DatabricksSettings.resolve({}, environ={}) is None
    full_env = {env: "x" for env in DATABRICKS_SETTINGS.values()}
    assert DatabricksSettings.resolve({}, environ=full_env) is not None
    assert DatabricksSettings.missing({"host": "h"}, environ={}) == [
        env for key, env in DATABRICKS_SETTINGS.items() if key != "host"
    ]


def test_databricks_source_without_settings_is_not_configured():
    src = DatabricksSource(None)
    assert not src.configured
    with pytest.raises(DataSourceNotConfigured):
        src.load()
