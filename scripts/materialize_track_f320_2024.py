#!/usr/bin/env python3
"""Create TRACK/Validation working filenames from canonical monthly F320 assets."""

from __future__ import annotations

import argparse
from pathlib import Path

import netCDF4
import xarray as xr

from materialize_f320_2024 import PRODUCTS, expected_annual_times, validate_dataset, validate_monthly_dataset


LEGACY_FILENAMES = {
    "msl": "ERA5_mslp_6hr_2024_DET.nc",
    "vo850": "ERA5_vo850_6hr_2024_DET.nc",
}


def copy_attributes(source: netCDF4.Variable, target: netCDF4.Variable) -> None:
    for name in source.ncattrs():
        if name != "_FillValue":
            target.setncattr(name, source.getncattr(name))


def materialize_track_input(input_dir: Path, output_dir: Path, product: str) -> Path:
    """Concatenate verified canonical monthly files without changing their values."""
    validate_monthly_dataset(input_dir, product)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / LEGACY_FILENAMES[product]
    if target.exists():
        raise RuntimeError(f"refusing to overwrite existing TRACK/Validation working input: {target}")
    temporary = target.with_name(target.name + ".partial")
    if temporary.exists():
        raise RuntimeError(f"remove or verify incomplete temporary output before continuing: {temporary}")

    first_path = input_dir / f"era5_{product}_2024-01_f320.nc"
    with netCDF4.Dataset(first_path) as first, netCDF4.Dataset(temporary, "w", format="NETCDF4") as output:
        output.setncatts({name: first.getncattr(name) for name in first.ncattrs()})
        output.createDimension("time", len(expected_annual_times()))
        output.createDimension("latitude", len(first.dimensions["latitude"]))
        output.createDimension("longitude", len(first.dimensions["longitude"]))
        variables: dict[str, netCDF4.Variable] = {}
        variable_name = PRODUCTS[product]["variable"]
        for name in ("time", "latitude", "longitude", variable_name):
            source = first.variables[name]
            fill_value = source.getncattr("_FillValue") if "_FillValue" in source.ncattrs() else None
            variables[name] = output.createVariable(name, source.dtype, source.dimensions, fill_value=fill_value)
            copy_attributes(source, variables[name])

        offset = 0
        for month in range(1, 13):
            path = input_dir / f"era5_{product}_2024-{month:02d}_f320.nc"
            with netCDF4.Dataset(path) as source:
                count = len(source.dimensions["time"])
                if month == 1:
                    variables["latitude"][:] = source.variables["latitude"][:]
                    variables["longitude"][:] = source.variables["longitude"][:]
                variables["time"][offset : offset + count] = source.variables["time"][:]
                variables[variable_name][offset : offset + count, :, :] = source.variables[variable_name][:]
                offset += count

    with xr.open_dataset(temporary, engine="netcdf4") as dataset:
        validate_dataset(dataset, product, expected_annual_times())
    temporary.replace(target)
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="directory containing canonical monthly F320 assets")
    parser.add_argument("--output-dir", type=Path, required=True, help="local TRACK/Validation working directory")
    parser.add_argument("--product", choices=sorted(PRODUCTS), action="append", help="product to materialize; repeatable (default: both)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for product in args.product or list(PRODUCTS):
        target = materialize_track_input(args.input_dir.resolve(), args.output_dir.resolve(), product)
        print(f"materialized {target}")


if __name__ == "__main__":
    main()
