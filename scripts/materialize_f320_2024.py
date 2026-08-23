#!/usr/bin/env python3
"""Materialize verified 2024 F320 ERA5 sources into canonical monthly NetCDF assets."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import netCDF4
import xarray as xr
from scipy.io import netcdf_file


YEAR = 2024
F320_LATITUDES = 640
F320_LONGITUDES = 1280
FRAMES_PER_DAY = 4
EXPECTED_FRAMES = 366 * FRAMES_PER_DAY
VALUE_CHECK_FRAMES = 8
ROOT = Path(__file__).resolve().parents[1]

PRODUCTS: dict[str, dict[str, str]] = {
    "msl": {
        "variable": "msl",
        "source_netcdf": "ERA5_mslp_6hr_2024_DET.nc",
        "source_grib": "ERA5_mslp_6hr_2024_F320.grib",
        "netcdf_sha256": "a2843cd3277da18b1b9e4c1ac5697e5785bbe65d8879aa3b793ee66280d3b6ff",
        "grib_sha256": "036dbc83a0b9c0f3d1361c6785611ec16199bff3a4f7e98bfe6b07e929f564a8",
        "units": "Pa",
        "grib_level": "0",
        "grib_level_type": "surface",
    },
    "vo850": {
        "variable": "vo",
        "source_netcdf": "ERA5_vo850_6hr_2024_DET.nc",
        "source_grib": "ERA5_vo850_6hr_2024_F320.grib",
        "netcdf_sha256": "9bb8aac8ba89398c731643fd475a157a2d421abe379e77039b8b8636c9026554",
        "grib_sha256": "c53c690ca9a98582fdbb0026d26d7296a5feb77c1de4a5b09c6d9189d09ed420",
        "units": "s**-1",
        "grib_level": "850",
        "grib_level_type": "isobaricInhPa",
    },
}


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def canonical_filename(product: str, month: int) -> str:
    if product not in PRODUCTS:
        raise ValueError(f"unknown ERA5 product: {product}")
    if month not in range(1, 13):
        raise ValueError(f"month must be in 1..12, got {month}")
    return f"era5_{product}_{YEAR}-{month:02d}_f320.nc"


def expected_month_times(month: int) -> np.ndarray[Any, np.dtype[np.datetime64]]:
    days = calendar.monthrange(YEAR, month)[1]
    start = np.datetime64(f"{YEAR}-{month:02d}-01T00:00:00", "ns")
    return start + np.arange(days * FRAMES_PER_DAY) * np.timedelta64(6, "h")


def expected_annual_times() -> np.ndarray[Any, np.dtype[np.datetime64]]:
    return np.concatenate([expected_month_times(month) for month in range(1, 13)])


def _require_coordinate(dataset: xr.Dataset, name: str) -> xr.DataArray:
    if name not in dataset.coords:
        raise RuntimeError(f"missing {name} coordinate")
    return dataset[name]


def validate_f320_geometry(dataset: xr.Dataset) -> None:
    latitude = _require_coordinate(dataset, "latitude")
    longitude = _require_coordinate(dataset, "longitude")
    if latitude.size != F320_LATITUDES or longitude.size != F320_LONGITUDES:
        raise RuntimeError(
            "expected F320 geometry with 640 latitudes and 1280 longitudes, got "
            f"{latitude.size} and {longitude.size}"
        )

    latitudes = np.asarray(latitude.values, dtype=np.float64)
    longitudes = np.asarray(longitude.values, dtype=np.float64)
    roots, _ = np.polynomial.legendre.leggauss(F320_LATITUDES)
    expected_latitudes = np.degrees(np.arcsin(roots))[::-1]
    expected_longitudes = np.arange(F320_LONGITUDES, dtype=np.float64) * (360.0 / F320_LONGITUDES)
    if not np.allclose(latitudes, expected_latitudes, rtol=0.0, atol=4e-4):
        raise RuntimeError("latitude coordinate is not the F320 full Gaussian grid")
    if not np.allclose(longitudes, expected_longitudes, rtol=0.0, atol=4e-4):
        raise RuntimeError("longitude coordinate is not the F320 full Gaussian grid")


def validate_times(times: np.ndarray[Any, np.dtype[np.datetime64]], expected: np.ndarray[Any, np.dtype[np.datetime64]]) -> None:
    actual = np.asarray(times).astype("datetime64[ns]")
    if actual.shape != expected.shape or not np.array_equal(actual, expected):
        raise RuntimeError(
            f"expected {expected.size} unique six-hour timestamps from {expected[0]} to {expected[-1]}, "
            f"got {actual.size} from {actual[0] if actual.size else None} to {actual[-1] if actual.size else None}"
        )


def validate_dataset(dataset: xr.Dataset, product: str, expected_times: np.ndarray[Any, np.dtype[np.datetime64]]) -> None:
    variable_name = PRODUCTS[product]["variable"]
    if variable_name not in dataset.data_vars:
        raise RuntimeError(f"expected {variable_name!r} data variable, found {list(dataset.data_vars)}")
    variable = dataset[variable_name]
    if tuple(variable.dims) != ("time", "latitude", "longitude"):
        raise RuntimeError(f"unexpected {variable_name} dimensions: {variable.dims}")
    if variable.dtype != np.dtype("float32"):
        raise RuntimeError(f"expected {variable_name} float32 values, got {variable.dtype}")
    if variable.attrs.get("units") != PRODUCTS[product]["units"]:
        raise RuntimeError(f"unexpected {variable_name} units: {variable.attrs.get('units')!r}")
    if dataset.sizes.get("time") != expected_times.size:
        raise RuntimeError(f"unexpected time dimension: {dataset.sizes.get('time')}")
    validate_f320_geometry(dataset)
    validate_times(_require_coordinate(dataset, "time").values, expected_times)


def validate_monthly_dataset(directory: Path, product: str) -> None:
    all_times: list[np.ndarray[Any, np.dtype[np.datetime64]]] = []
    for month in range(1, 13):
        path = directory / canonical_filename(product, month)
        if not path.is_file():
            raise RuntimeError(f"missing canonical monthly asset: {path}")
        with xr.open_dataset(path, engine="netcdf4") as dataset:
            expected = expected_month_times(month)
            validate_dataset(dataset, product, expected)
            all_times.append(np.asarray(dataset["time"].values).astype("datetime64[ns]"))
    annual_times = np.concatenate(all_times)
    if annual_times.size != EXPECTED_FRAMES:
        raise RuntimeError(f"expected {EXPECTED_FRAMES} annual frames, got {annual_times.size}")
    validate_times(annual_times, expected_annual_times())


def validate_monthly_values_against_source(directory: Path, source_path: Path, product: str) -> None:
    """Require each canonical value array to exactly equal its annual source slice."""
    variable_name = PRODUCTS[product]["variable"]

    def values_match(source_values: Any, written_values: Any, offset: int, frame_count: int) -> bool:
        for start in range(0, frame_count, VALUE_CHECK_FRAMES):
            stop = min(start + VALUE_CHECK_FRAMES, frame_count)
            if not np.array_equal(
                source_values[offset + start : offset + stop, :, :],
                written_values[start:stop, :, :],
                equal_nan=True,
            ):
                return False
        return True

    offset = 0
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Cannot close a netcdf_file opened with mmap=True",
            category=RuntimeWarning,
        )
        with netcdf_file(source_path, "r", mmap=True) as source:
            source_values = source.variables[variable_name].data
            for month in range(1, 13):
                expected = expected_month_times(month)
                path = directory / canonical_filename(product, month)
                try:
                    with netcdf_file(path, "r", mmap=True) as written:
                        matches = values_match(
                            source_values,
                            written.variables[variable_name].data,
                            offset,
                            expected.size,
                        )
                except TypeError:
                    with netCDF4.Dataset(path) as written:
                        matches = values_match(
                            source_values,
                            written.variables[variable_name],
                            offset,
                            expected.size,
                        )
                if not matches:
                    raise RuntimeError(f"value mismatch between {path.name} and verified annual source")
                offset += expected.size


def validate_grib(path: Path, product: str) -> None:
    grib_get = shutil.which("grib_get")
    grib_count = shutil.which("grib_count")
    if not grib_get or not grib_count:
        raise RuntimeError("ecCodes grib_get and grib_count are required to verify the F320 source")
    result = subprocess.run(
        [grib_get, "-w", "count=1", "-p", "shortName,units,typeOfGrid,Ni,Nj,level,typeOfLevel", str(path)],
        check=True,
        text=True,
        capture_output=True,
    )
    values = result.stdout.split()
    expected = [
        "msl" if product == "msl" else "vo",
        PRODUCTS[product]["units"],
        "regular_gg",
        str(F320_LONGITUDES),
        str(F320_LATITUDES),
        PRODUCTS[product]["grib_level"],
        PRODUCTS[product]["grib_level_type"],
    ]
    if values != expected:
        raise RuntimeError(f"unexpected GRIB identity for {path.name}: {values!r}")
    count = int(subprocess.run([grib_count, str(path)], check=True, text=True, capture_output=True).stdout)
    if count != EXPECTED_FRAMES:
        raise RuntimeError(f"expected {EXPECTED_FRAMES} GRIB messages, got {count}")


def verify_source(source_dir: Path, product: str) -> Path:
    metadata = PRODUCTS[product]
    netcdf = source_dir / metadata["source_netcdf"]
    grib = source_dir / metadata["source_grib"]
    for path in (netcdf, grib):
        if not path.is_file():
            raise RuntimeError(f"missing verified source file: {path}")
    print(f"verifying {product} source checksums", flush=True)
    if sha256(netcdf) != metadata["netcdf_sha256"]:
        raise RuntimeError(f"unexpected NetCDF checksum for {netcdf}")
    if sha256(grib) != metadata["grib_sha256"]:
        raise RuntimeError(f"unexpected GRIB checksum for {grib}")
    print(f"verifying {product} GRIB identity", flush=True)
    validate_grib(grib, product)
    print(f"verifying {product} annual NetCDF metadata", flush=True)
    with xr.open_dataset(netcdf, engine="netcdf4") as dataset:
        validate_dataset(dataset, product, expected_annual_times())
    return netcdf


def verify_written_month(
    path: Path,
    product: str,
    expected_times: np.ndarray[Any, np.dtype[np.datetime64]],
) -> None:
    """Check output metadata and exact monthly coverage before promoting it."""
    with xr.open_dataset(path, engine="netcdf4") as written:
        validate_dataset(written, product, expected_times)


def copy_netcdf3_attributes(source: Any, target: Any) -> None:
    for name, value in source._attributes.items():
        setattr(target, name, value)


def write_month_from_verified_source(
    source_path: Path,
    temporary: Path,
    product: str,
    offset: int,
    frame_count: int,
) -> None:
    """Copy a contiguous monthly slice from the checked NetCDF3 source."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Cannot close a netcdf_file opened with mmap=True",
            category=RuntimeWarning,
        )
        with netcdf_file(source_path, "r", mmap=True) as source, netcdf_file(temporary, "w", version=2) as output:
            copy_netcdf3_attributes(source, output)
            output.createDimension("time", frame_count)
            output.createDimension("latitude", source.dimensions["latitude"])
            output.createDimension("longitude", source.dimensions["longitude"])
            variable_name = PRODUCTS[product]["variable"]
            for name in ("time", "latitude", "longitude", variable_name):
                source_variable = source.variables[name]
                target_variable = output.createVariable(name, source_variable.typecode(), source_variable.dimensions)
                copy_netcdf3_attributes(source_variable, target_variable)
                if name == variable_name:
                    target_variable[:] = source_variable.data[offset : offset + frame_count, :, :]
                elif name == "time":
                    target_variable[:] = source_variable.data[offset : offset + frame_count]
                else:
                    target_variable[:] = source_variable.data[:]


def materialize_product(source_dir: Path, output_dir: Path, product: str) -> None:
    source_path = verify_source(source_dir, product)
    output_dir.mkdir(parents=True, exist_ok=True)
    offset = 0
    for month in range(1, 13):
        expected = expected_month_times(month)
        target = output_dir / canonical_filename(product, month)
        if target.exists():
            verify_written_month(target, product, expected)
            print(f"verified existing {target.name}")
            offset += expected.size
            continue
        temporary = target.with_name(target.name + ".partial")
        if temporary.exists():
            raise RuntimeError(f"remove or verify incomplete temporary output before continuing: {temporary}")
        print(f"writing {target.name}", flush=True)
        write_month_from_verified_source(source_path, temporary, product, offset, expected.size)
        verify_written_month(temporary, product, expected)
        temporary.replace(target)
        print(f"materialized {target.name}")
        offset += expected.size
    validate_monthly_dataset(output_dir, product)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="directory containing verified annual F320 NetCDF and GRIB files")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "prepared" / "f320-2024")
    parser.add_argument("--product", choices=sorted(PRODUCTS), action="append", help="product to materialize; repeatable (default: both)")
    parser.add_argument("--verify-only", action="store_true", help="validate existing canonical monthly assets without creating them")
    parser.add_argument(
        "--verify-values",
        action="store_true",
        help="compare every canonical value to the verified annual source; requires --verify-only",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    products = args.product or list(PRODUCTS)
    if args.verify_values and not args.verify_only:
        raise SystemExit("--verify-values requires --verify-only")
    if args.verify_only:
        for product in products:
            validate_monthly_dataset(output_dir, product)
            if args.verify_values:
                source_path = verify_source(source_dir, product)
                validate_monthly_values_against_source(output_dir, source_path, product)
                print(f"validated {product} 2024 F320 values against the annual source")
            print(f"validated {product} 2024 F320 monthly dataset")
        return
    for product in products:
        materialize_product(source_dir, output_dir, product)


if __name__ == "__main__":
    main()
