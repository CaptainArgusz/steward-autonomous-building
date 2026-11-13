import pandas as pd
import pytest

from steward.cli import TABLES
from steward.dataset import Dataset, DatasetError


def test_every_table_loads_with_utc_timestamps(ds):
    for name in TABLES:
        df = getattr(ds, name)
        assert len(df) > 0, name
        for col in (c for c in df.columns if c.endswith("_utc")):
            assert isinstance(df[col].dtype, pd.DatetimeTZDtype), f"{name}.{col}"
            assert str(df[col].dt.tz) == "UTC", f"{name}.{col}"


def test_missing_values_stay_missing(ds):
    # The dataset deliberately contains gaps; a loader that fills them with 0 would
    # manufacture cold rooms and dead meters.
    telemetry = ds.room_telemetry
    assert telemetry["temperature_c"].isna().any()
    assert (telemetry["temperature_c"].dropna() != 0).all()


def test_building_metadata(ds):
    assert len(ds.room_ids) == 40
    assert ds.comfort_band_c == (20.0, 23.0)
    assert {"R101", "HP1", "WM_MAIN", "WM_R101", "ROBOT_1", "GW_F1", "BUILDING"} <= ds.known_ids
    assert "status" in ds.equipment_wide("HP1").columns


def test_incomplete_directory_is_reported(tmp_path):
    with pytest.raises(DatasetError, match="missing dataset files"):
        Dataset(tmp_path)
