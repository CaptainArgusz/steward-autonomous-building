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


def test_occupancy_is_causal_and_matches_stays(ds):
    stay = ds.stays.dropna(subset=["end_utc"]).iloc[5]
    minute = pd.Timedelta(minutes=1)
    probe = pd.DataFrame(
        {
            "room_id": stay["room_id"],
            "timestamp_utc": [
                stay["start_utc"] - minute,
                stay["start_utc"],
                stay["end_utc"] - minute,
                stay["end_utc"],
            ],
        }
    )
    out = ds.add_occupancy(probe)
    # Before check-in the room may still hold the previous guest; from check-in on it is ours.
    assert out["occupied"].tolist()[1:] == [True, True, False]
    assert out["stay_booking_id"].tolist()[1:3] == [stay["booking_id"]] * 2
    assert list(out.index) == list(probe.index)
