from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from scipy.io import netcdf_file


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from materialize_f320_2024 import (  # noqa: E402
    F320_LATITUDES,
    F320_LONGITUDES,
    PRODUCTS,
    canonical_filename,
    expected_annual_times,
    expected_month_times,
    validate_f320_geometry,
    validate_monthly_values_against_source,
    validate_monthly_dataset,
    validate_times,
    write_month_from_verified_source,
)
from materialize_track_f320_2024 import LEGACY_FILENAMES  # noqa: E402


def test_canonical_f320_filenames_use_required_grammar() -> None:
    assert canonical_filename("msl", 1) == "era5_msl_2024-01_f320.nc"
    assert canonical_filename("vo850", 12) == "era5_vo850_2024-12_f320.nc"
    with pytest.raises(ValueError, match="unknown ERA5 product"):
        canonical_filename("MSL", 1)
    assert PRODUCTS["msl"]["variable"] == "msl"
    assert PRODUCTS["vo850"]["variable"] == "vo"


def test_annual_time_coverage_is_complete_leap_year_six_hourly() -> None:
    times = expected_annual_times()
    assert times.size == 1464
    assert times[0] == np.datetime64("2024-01-01T00:00:00", "ns")
    assert times[-1] == np.datetime64("2024-12-31T18:00:00", "ns")
    validate_times(times, times)
    broken = times.copy()
    broken[100] = broken[99]
    with pytest.raises(RuntimeError, match="unique six-hour timestamps"):
        validate_times(broken, times)


def test_f320_geometry_requires_full_gaussian_coordinates() -> None:
    roots, _ = np.polynomial.legendre.leggauss(F320_LATITUDES)
    dataset = xr.Dataset(
        coords={
            "latitude": np.degrees(np.arcsin(roots))[::-1],
            "longitude": np.arange(F320_LONGITUDES) * (360.0 / F320_LONGITUDES),
        }
    )
    validate_f320_geometry(dataset)
    dataset = dataset.assign_coords(latitude=np.linspace(90.0, -90.0, F320_LATITUDES))
    with pytest.raises(RuntimeError, match="full Gaussian"):
        validate_f320_geometry(dataset)


def test_monthly_time_ranges_have_expected_frame_counts() -> None:
    assert expected_month_times(1).size == 124
    assert expected_month_times(2).size == 116
    assert expected_month_times(12).size == 124


def test_track_materializer_keeps_legacy_names_outside_canonical_catalog() -> None:
    assert LEGACY_FILENAMES == {
        "msl": "ERA5_mslp_6hr_2024_DET.nc",
        "vo850": "ERA5_vo850_6hr_2024_DET.nc",
    }
    assert all("_DET" not in canonical_filename(product, 1) for product in LEGACY_FILENAMES)


def test_contiguous_splitter_copies_the_requested_time_slice(tmp_path: Path) -> None:
    source_path = tmp_path / "annual.nc"
    target_path = tmp_path / "monthly.nc.partial"
    values = np.arange(24, dtype=np.float32).reshape(4, 2, 3)
    with netcdf_file(source_path, "w", version=2) as source:
        source.createDimension("time", 4)
        source.createDimension("latitude", 2)
        source.createDimension("longitude", 3)
        time = source.createVariable("time", "i4", ("time",))
        time.units = "hours since 2024-01-01 00:00:00"
        time[:] = np.arange(4)
        latitude = source.createVariable("latitude", "f4", ("latitude",))
        latitude[:] = [45.0, -45.0]
        longitude = source.createVariable("longitude", "f4", ("longitude",))
        longitude[:] = [0.0, 120.0, 240.0]
        msl = source.createVariable("msl", "f4", ("time", "latitude", "longitude"))
        msl.units = "Pa"
        msl[:] = values

    write_month_from_verified_source(source_path, target_path, "msl", offset=1, frame_count=2)

    with netcdf_file(target_path, "r", mmap=False) as written:
        assert written.dimensions["time"] == 2
        np.testing.assert_array_equal(written.variables["time"][:], [1, 2])
        np.testing.assert_array_equal(written.variables["msl"][:], values[1:3])
        assert written.variables["msl"].units == b"Pa"


def test_value_validation_rejects_any_changed_monthly_cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = tmp_path / "annual.nc"
    values = np.arange(24, dtype=np.float32).reshape(24, 1, 1)
    with netcdf_file(source_path, "w", version=2) as source:
        source.createDimension("time", 24)
        source.createDimension("latitude", 1)
        source.createDimension("longitude", 1)
        source.createVariable("msl", "f4", ("time", "latitude", "longitude"))[:] = values
    for month in range(1, 13):
        monthly_path = tmp_path / f"month-{month}.nc"
        with netcdf_file(monthly_path, "w", version=2) as written:
            written.createDimension("time", 2)
            written.createDimension("latitude", 1)
            written.createDimension("longitude", 1)
            written.createVariable("msl", "f4", ("time", "latitude", "longitude"))[:] = values[(month - 1) * 2 : month * 2]

    monkeypatch.setattr("materialize_f320_2024.expected_month_times", lambda month: np.arange(2))
    monkeypatch.setattr("materialize_f320_2024.canonical_filename", lambda product, month: f"month-{month}.nc")
    validate_monthly_values_against_source(tmp_path, source_path, "msl")

    with netcdf_file(tmp_path / "month-4.nc", "a") as written:
        written.variables["msl"][0, 0, 0] = 2.0
    with pytest.raises(RuntimeError, match="value mismatch"):
        validate_monthly_values_against_source(tmp_path, source_path, "msl")


def test_materialized_assets_validate_when_a_local_prepared_set_exists() -> None:
    prepared = Path(__file__).resolve().parents[1] / "prepared" / "f320-2024"
    expected = [
        prepared / canonical_filename(product, month)
        for product in ("msl", "vo850")
        for month in range(1, 13)
    ]
    if not all(path.is_file() for path in expected):
        pytest.skip("full F320 assets have not been materialized locally")
    validate_monthly_dataset(prepared, "msl")
    validate_monthly_dataset(prepared, "vo850")
