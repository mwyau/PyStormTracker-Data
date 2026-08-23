from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import xarray as xr

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fetch_era5
from fetch_era5 import (
    F320_LATITUDES,
    F320_LONGITUDES,
    canonical_f320_filename,
    expected_month_times,
    load_catalog,
    validate_f320_dataset,
    validate_f320_geometry,
)

CATALOG = Path(__file__).resolve().parents[1] / "data" / "era5_requests.json"


def _entry(product: str = "msl", month: int = 1) -> dict[str, Any]:
    return next(
        entry
        for entry in load_catalog(CATALOG)
        if entry.get("grid") == "F320"
        and entry["filename"] == canonical_f320_filename(product, month)
    )


def _valid_dataset(entry: dict[str, Any]) -> xr.Dataset:
    times = expected_month_times(entry["year"], entry["month"])
    roots, _ = np.polynomial.legendre.leggauss(F320_LATITUDES)
    latitude = np.degrees(np.arcsin(roots))[::-1]
    longitude = np.arange(F320_LONGITUDES, dtype=np.float64) * (360.0 / F320_LONGITUDES)
    values = np.broadcast_to(
        np.asarray(0.0, dtype=np.float32),
        (times.size, F320_LATITUDES, F320_LONGITUDES),
    )
    return xr.Dataset(
        {
            entry["variable"]: (
                ("time", "latitude", "longitude"),
                values,
                {"units": entry["units"]},
            )
        },
        coords={"time": times, "latitude": latitude, "longitude": longitude},
    )


def test_expected_month_times_cover_leap_year() -> None:
    january = expected_month_times(2024, 1)
    february = expected_month_times(2024, 2)

    assert january.size == 124
    assert february.size == 116
    assert january[0] == np.datetime64("2024-01-01T00:00:00", "ns")
    assert february[-1] == np.datetime64("2024-02-29T18:00:00", "ns")


def test_f320_geometry_requires_gaussian_coordinates() -> None:
    dataset = _valid_dataset(_entry())
    validate_f320_geometry(dataset)

    broken = dataset.assign_coords(latitude=np.linspace(90.0, -90.0, F320_LATITUDES))
    with pytest.raises(RuntimeError, match="full Gaussian"):
        validate_f320_geometry(broken)


def test_f320_dataset_validation_checks_identity_and_time() -> None:
    entry = _entry("vo850", 2)
    dataset = _valid_dataset(entry)
    validate_f320_dataset(dataset, entry)

    invalid_units = dataset.assign(
        {entry["variable"]: dataset[entry["variable"]].assign_attrs(units="Pa")}
    )
    with pytest.raises(RuntimeError, match="units"):
        validate_f320_dataset(invalid_units, entry)


def test_one_month_f320_acquisition_promotes_only_validated_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry("msl", 1)
    calls: list[tuple[str, str]] = []

    class FakeClient:
        def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
            calls.append((dataset, target))
            Path(target).write_bytes(b"GRIB")

    monkeypatch.setattr(fetch_era5, "validate_f320_grib", lambda path, item: None)

    def fake_convert(source: Path, target: Path) -> None:
        target.write_bytes(b"NetCDF")

    monkeypatch.setattr(fetch_era5, "convert_grib_to_netcdf", fake_convert)
    monkeypatch.setattr(fetch_era5, "validate_f320_file", lambda path, item: None)

    output = fetch_era5.fetch_f320_month(entry, tmp_path, FakeClient())

    assert output == tmp_path / entry["filename"]
    assert output.read_bytes() == b"NetCDF"
    assert calls[0][0] == "reanalysis-era5-complete"
    assert not list(tmp_path.glob("*.partial"))
