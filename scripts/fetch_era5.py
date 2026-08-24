#!/usr/bin/env python3
"""Acquire physical ERA5 files and validate monthly F320 outputs."""

from __future__ import annotations

import argparse
import calendar
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "data" / "era5_requests.json"
CATALOG_SCHEMA_VERSION = 1
CDS_SOURCE = "cds"
F320_LATITUDES = 640
F320_LONGITUDES = 1280
FRAMES_PER_DAY = 4
F320_ECCODES_COMMANDS = ("grib_get", "grib_count", "grib_to_netcdf")


class CDSClient(Protocol):
    """The part of the CDS client used by this script."""

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> Any:
        """Retrieve one physical asset."""


CatalogEntry = dict[str, Any]


def canonical_f320_filename(product: str, month: int) -> str:
    """Return the canonical monthly F320 NetCDF filename."""
    if product not in {"msl", "vo850"}:
        raise ValueError(f"unknown F320 product: {product}")
    if month not in range(1, 13):
        raise ValueError(f"month must be in 1..12, got {month}")
    return f"era5_{product}_2024-{month:02d}_f320.nc"


def expected_month_times(
    year: int, month: int
) -> np.ndarray[Any, np.dtype[np.datetime64]]:
    """Return the six-hourly timestamps expected for one calendar month."""
    days = calendar.monthrange(year, month)[1]
    start = np.datetime64(f"{year}-{month:02d}-01T00:00:00", "ns")
    return start + np.arange(days * FRAMES_PER_DAY) * np.timedelta64(6, "h")


def _is_f320(entry: CatalogEntry) -> bool:
    return entry.get("grid") == "F320"


def _f320_product(entry: CatalogEntry) -> str:
    return "vo850" if entry.get("variable") == "vo" else "msl"


def load_catalog_document(path: Path) -> dict[str, Any]:
    """Read and validate the physical ERA5 acquisition definitions."""
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read request catalog {path}: {error}") from error

    entries = catalog.get("datasets")
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION or not isinstance(
        entries, list
    ):
        raise SystemExit(
            f"request catalog must contain schema_version {CATALOG_SCHEMA_VERSION} "
            "and a datasets list"
        )

    ids: set[str] = set()
    filenames: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SystemExit(f"invalid request catalog entry: {entry!r}")
        required = {"id", "source", "summary", "filename", "dataset", "request"}
        if required - entry.keys():
            raise SystemExit(f"invalid request catalog entry: {entry!r}")
        if entry["source"] != CDS_SOURCE or not isinstance(entry["request"], dict):
            raise SystemExit(f"invalid CDS request catalog entry: {entry!r}")

        dataset_id = entry["id"]
        filename = entry["filename"]
        if (
            not isinstance(dataset_id, str)
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith((".nc", ".grib"))
        ):
            raise SystemExit(f"invalid physical ERA5 filename: {entry!r}")
        if dataset_id in ids or filename in filenames:
            raise SystemExit(f"duplicate dataset ID or filename: {dataset_id}")
        ids.add(dataset_id)
        filenames.add(filename)

        if _is_f320(entry):
            required_f320 = {
                "year",
                "month",
                "variable",
                "units",
                "input_format",
                "output_format",
            }
            if required_f320 - entry.keys():
                raise SystemExit(f"invalid F320 request entry: {entry!r}")
            if (
                entry["year"] != 2024
                or not isinstance(entry["month"], int)
                or entry["month"] not in range(1, 13)
                or entry["variable"] not in {"msl", "vo"}
                or entry["units"] not in {"Pa", "s**-1"}
                or entry["input_format"] != "grib"
                or entry["output_format"] != "netcdf"
            ):
                raise SystemExit(f"invalid F320 request entry: {entry!r}")
            if entry["filename"] != canonical_f320_filename(
                _f320_product(entry), entry["month"]
            ):
                raise SystemExit(f"non-canonical F320 filename: {entry!r}")
            if entry["dataset"] != "reanalysis-era5-complete":
                raise SystemExit(f"F320 entries must use ERA5 Complete: {entry!r}")
            if entry["request"].get("grid") != "F320":
                raise SystemExit(f"F320 request does not request F320: {entry!r}")
            if entry["variable"] == "vo" and entry.get("level") != 850:
                raise SystemExit(f"VO850 F320 entry must specify level 850: {entry!r}")

    return catalog


def load_catalog(path: Path) -> list[CatalogEntry]:
    """Return physical acquisition entries."""
    return load_catalog_document(path)["datasets"]


def parse_ids(values: list[str]) -> list[str]:
    """Expand comma-separated physical entry IDs without resolving aliases."""
    return [
        item.strip() for value in values for item in value.split(",") if item.strip()
    ]


def select_entries(entries: list[CatalogEntry], ids: list[str]) -> list[CatalogEntry]:
    """Select physical entries by their explicit acquisition IDs."""
    by_id = {entry["id"]: entry for entry in entries}
    unknown = sorted(set(ids) - by_id.keys())
    if unknown:
        raise SystemExit("unknown dataset ID(s): " + ", ".join(unknown))
    return [by_id[dataset_id] for dataset_id in ids]


def list_catalog(entries: list[CatalogEntry]) -> list[str]:
    """Format physical acquisition entries for the command-line listing."""
    return [
        f"{entry['id']} [{entry['source']}]: {entry['summary']} -> {entry['filename']}"
        for entry in entries
    ]


def _require_coordinate(dataset: xr.Dataset, name: str) -> xr.DataArray:
    if name not in dataset.coords:
        raise RuntimeError(f"missing {name} coordinate")
    return dataset[name]


def validate_f320_geometry(dataset: xr.Dataset) -> None:
    """Check the essential full-Gaussian F320 coordinates."""
    latitude = _require_coordinate(dataset, "latitude")
    longitude = _require_coordinate(dataset, "longitude")
    if latitude.size != F320_LATITUDES or longitude.size != F320_LONGITUDES:
        raise RuntimeError(
            "expected F320 geometry with 640 latitudes and 1280 longitudes, got "
            f"{latitude.size} and {longitude.size}"
        )

    roots, _ = np.polynomial.legendre.leggauss(F320_LATITUDES)
    expected_latitudes = np.degrees(np.arcsin(roots))[::-1]
    expected_longitudes = np.arange(F320_LONGITUDES) * (360.0 / F320_LONGITUDES)
    if not np.allclose(
        np.asarray(latitude.values, dtype=np.float64),
        expected_latitudes,
        rtol=0.0,
        atol=4e-4,
    ):
        raise RuntimeError("latitude coordinate is not the F320 full Gaussian grid")
    if not np.allclose(
        np.asarray(longitude.values, dtype=np.float64),
        expected_longitudes,
        rtol=0.0,
        atol=4e-4,
    ):
        raise RuntimeError("longitude coordinate is not the F320 full Gaussian grid")


def validate_f320_dataset(dataset: xr.Dataset, entry: CatalogEntry) -> None:
    """Validate one converted monthly F320 NetCDF dataset."""
    if not _is_f320(entry):
        raise ValueError(f"not an F320 entry: {entry['id']}")

    variable_name = entry["variable"]
    if variable_name not in dataset.data_vars:
        raise RuntimeError(
            f"expected {variable_name!r} data variable, found {list(dataset.data_vars)}"
        )
    variable = dataset[variable_name]
    if tuple(variable.dims) != ("time", "latitude", "longitude"):
        raise RuntimeError(f"unexpected {variable_name} dimensions: {variable.dims}")
    if variable.dtype != np.dtype("float32"):
        raise RuntimeError(
            f"expected {variable_name} float32 values, got {variable.dtype}"
        )
    if variable.attrs.get("units") != entry["units"]:
        raise RuntimeError(
            f"unexpected {variable_name} units: {variable.attrs.get('units')!r}"
        )

    expected = expected_month_times(entry["year"], entry["month"])
    if dataset.sizes.get("time") != expected.size:
        raise RuntimeError(
            f"expected {expected.size} monthly frames, got {dataset.sizes.get('time')}"
        )
    actual_times = np.asarray(_require_coordinate(dataset, "time").values).astype(
        "datetime64[ns]"
    )
    if actual_times.shape != expected.shape or not np.array_equal(
        actual_times, expected
    ):
        raise RuntimeError(
            f"unexpected time coverage for {entry['filename']}: "
            f"expected {expected[0]} through {expected[-1]}"
        )
    validate_f320_geometry(dataset)


def validate_f320_file(path: Path, entry: CatalogEntry) -> None:
    """Open and validate one monthly F320 NetCDF file."""
    with xr.open_dataset(path) as dataset:
        validate_f320_dataset(dataset, entry)


def validate_f320_grib(path: Path, entry: CatalogEntry) -> None:
    """Validate the essential identity and frame count of one F320 GRIB."""
    grib_get = shutil.which("grib_get")
    grib_count = shutil.which("grib_count")
    if not grib_get or not grib_count:
        raise RuntimeError(
            "ecCodes grib_get and grib_count are required to verify an F320 GRIB"
        )

    result = subprocess.run(
        [
            grib_get,
            "-w",
            "count=1",
            "-p",
            "shortName,units,typeOfGrid,Ni,Nj,level,typeOfLevel",
            str(path),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    expected_identity = [
        entry["variable"],
        entry["units"],
        "regular_gg",
        str(F320_LONGITUDES),
        str(F320_LATITUDES),
        "0" if entry["variable"] == "msl" else "850",
        "surface" if entry["variable"] == "msl" else "isobaricInhPa",
    ]
    actual_identity = result.stdout.split()
    if actual_identity != expected_identity:
        raise RuntimeError(
            f"unexpected GRIB identity for {path.name}: {actual_identity!r}"
        )

    count = int(
        subprocess.run(
            [grib_count, str(path)],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    )
    expected_count = expected_month_times(entry["year"], entry["month"]).size
    if count != expected_count:
        raise RuntimeError(
            f"expected {expected_count} GRIB messages for {path.name}, got {count}"
        )


def convert_grib_to_netcdf(source: Path, target: Path) -> None:
    """Convert one checked GRIB to float32 NetCDF with ecCodes."""
    converter = shutil.which("grib_to_netcdf")
    if converter is None:
        raise RuntimeError(
            "grib_to_netcdf is required to create canonical F320 NetCDF assets"
        )
    subprocess.run(
        [converter, "-D", "NC_FLOAT", "-o", str(target), str(source)],
        check=True,
    )


def _require_nonempty_file(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"{label} did not produce a readable file: {path}")


def fetch_f320_month(
    entry: CatalogEntry,
    output_dir: Path,
    client: CDSClient,
    *,
    overwrite: bool = False,
) -> Path:
    """Retrieve, validate, convert, and promote one monthly F320 asset."""
    if not _is_f320(entry):
        raise ValueError(f"not an F320 entry: {entry['id']}")

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / entry["filename"]
    if target.exists() and not overwrite:
        try:
            validate_f320_file(target, entry)
        except Exception:
            # A failed validation must not make a bad canonical file look
            # usable on the next run.
            target.unlink(missing_ok=True)
        else:
            return target

    source = output_dir / f"{entry['filename']}.grib.partial"
    converted = output_dir / f"{entry['filename']}.partial"
    # Partial files are disposable workflow state.  Remove leftovers from a
    # failed prior attempt so retrying does not require manual cleanup.
    source.unlink(missing_ok=True)
    converted.unlink(missing_ok=True)
    try:
        client.retrieve(entry["dataset"], entry["request"], str(source))
        _require_nonempty_file(source, "CDS retrieval")
        validate_f320_grib(source, entry)
        convert_grib_to_netcdf(source, converted)
        _require_nonempty_file(converted, "NetCDF conversion")
        validate_f320_file(converted, entry)
        converted.replace(target)
    finally:
        source.unlink(missing_ok=True)
        converted.unlink(missing_ok=True)
    return target


def fetch_entry(
    entry: CatalogEntry,
    output_dir: Path,
    client: CDSClient,
    *,
    overwrite: bool = False,
) -> Path:
    """Retrieve one physical catalog entry."""
    if _is_f320(entry):
        return fetch_f320_month(entry, output_dir, client, overwrite=overwrite)

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / entry["filename"]
    if target.exists() and not overwrite:
        try:
            _require_nonempty_file(target, "existing CDS asset")
        except RuntimeError:
            target.unlink(missing_ok=True)
        else:
            return target

    temporary = output_dir / f"{entry['filename']}.partial"
    temporary.unlink(missing_ok=True)
    try:
        client.retrieve(entry["dataset"], entry["request"], str(temporary))
        _require_nonempty_file(temporary, "CDS retrieval")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _create_cds_client() -> CDSClient:
    try:
        import cdsapi
    except ImportError as error:
        raise SystemExit("cdsapi is required; run: uv sync --locked") from error
    return cdsapi.Client()


def require_f320_eccodes_tools() -> None:
    """Fail before acquisition when required ecCodes executables are unavailable."""
    missing = [
        command for command in F320_ECCODES_COMMANDS if shutil.which(command) is None
    ]
    if missing:
        raise RuntimeError(
            "F320 acquisition requires ecCodes command-line tools; missing: "
            + ", ".join(missing)
        )


def fetch_entries(
    entries: list[CatalogEntry],
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Retrieve a selected list of physical entries with one CDS client."""
    if any(_is_f320(entry) for entry in entries):
        require_f320_eccodes_tools()
    client = _create_cds_client()
    return [
        fetch_entry(entry, output_dir, client, overwrite=overwrite) for entry in entries
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--list", action="store_true", help="list physical ERA5 entries"
    )
    parser.add_argument(
        "--update",
        action="append",
        default=[],
        metavar="ID[,ID]",
        help="physical acquisition IDs to fetch; repeatable",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release-data")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing output files after validation",
    )
    args = parser.parse_args()

    entries = load_catalog(args.config)
    if args.list:
        for line in list_catalog(entries):
            print(line)
        return

    ids = parse_ids(args.update)
    if not ids:
        parser.error("--update is required unless --list is used")
    selected = select_entries(entries, ids)
    for path in fetch_entries(
        selected, args.output_dir.resolve(), overwrite=args.overwrite
    ):
        print(f"fetched {path.name}")


if __name__ == "__main__":
    main()
