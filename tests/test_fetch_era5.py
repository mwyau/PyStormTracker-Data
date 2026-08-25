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

CATALOG = Path(__file__).resolve().parents[1] / "manifests" / "era5.json"


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


def test_catalog_integrity_and_f320_request_identity() -> None:
    entries = load_catalog(CATALOG)

    assert len(entries) == 34
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert len({entry["filename"] for entry in entries}) == len(entries)

    for variable, product, parameter, level_type, level in (
        ("msl", "msl", "151.128", "sfc", None),
        ("vo", "vo850", "138.128", "pl", "850"),
    ):
        selected = [
            entry
            for entry in entries
            if entry.get("grid") == "F320" and entry["variable"] == variable
        ]
        assert [entry["month"] for entry in selected] == list(range(1, 13))
        assert [entry["filename"] for entry in selected] == [
            canonical_f320_filename(product, month) for month in range(1, 13)
        ]
        assert all(entry["year"] == 2024 for entry in selected)
        assert all(entry["request"]["grid"] == "F320" for entry in selected)
        assert all(entry["request"]["param"] == parameter for entry in selected)
        assert all(entry["request"]["levtype"] == level_type for entry in selected)
        assert all(entry["request"]["time"] == "00/06/12/18" for entry in selected)
        if level is None:
            assert all("levelist" not in entry["request"] for entry in selected)
        else:
            assert all(entry["request"]["levelist"] == level for entry in selected)


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


def test_f320_acquisition_preflights_eccodes_before_cds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry("msl", 1)
    available = {
        "grib_get": "/usr/bin/grib_get",
        "grib_count": None,
        "grib_to_netcdf": "/usr/bin/grib_to_netcdf",
    }
    monkeypatch.setattr(
        fetch_era5.shutil, "which", lambda command: available.get(command)
    )
    monkeypatch.setattr(
        fetch_era5,
        "_create_cds_client",
        lambda: pytest.fail("CDS client must not be created before ecCodes preflight"),
    )

    with pytest.raises(RuntimeError, match="grib_count"):
        fetch_era5.fetch_entries([entry], tmp_path)


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


def test_failed_f320_validation_cleans_intermediate_files_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry("msl", 1)
    validation_attempts = 0

    class FakeClient:
        def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> None:
            Path(target).write_bytes(b"GRIB")

    monkeypatch.setattr(fetch_era5, "validate_f320_grib", lambda path, item: None)
    monkeypatch.setattr(
        fetch_era5,
        "convert_grib_to_netcdf",
        lambda source, target: target.write_bytes(b"NetCDF"),
    )

    def validate_once(path: Path, item: dict[str, Any]) -> None:
        nonlocal validation_attempts
        validation_attempts += 1
        if validation_attempts == 1:
            raise RuntimeError("invalid converted result")

    monkeypatch.setattr(fetch_era5, "validate_f320_file", validate_once)

    with pytest.raises(RuntimeError, match="invalid converted result"):
        fetch_era5.fetch_f320_month(entry, tmp_path, FakeClient())

    target = tmp_path / entry["filename"]
    assert not target.exists()
    assert not list(tmp_path.glob("*.partial"))

    output = fetch_era5.fetch_f320_month(entry, tmp_path, FakeClient())
    assert output == target
    assert output.read_bytes() == b"NetCDF"
    assert not list(tmp_path.glob("*.partial"))
