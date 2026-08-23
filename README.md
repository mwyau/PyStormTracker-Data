# PyStormTracker-Data

This repository is the canonical home for PyStormTracker's external scientific
and reference data. It hosts dataset recipes, compact Git-tracked parity
references, and release metadata used by [PyStormTracker](https://github.com/mwyau/PyStormTracker).

The deliberate exception is
`PyStormTracker/tests/data/era5/era5_msl_2025-12_2.5x2.5.nc`. That December
NetCDF remains in the software repository as its ordinary fully-offline
integration-test input.

## Building a data release

Install the repository tools with `uv`:

```bash
uv pip install -r requirements.txt
```

Create an ECMWF/CDS account and accept the terms for each ERA5 dataset used by this repository. While signed in, copy the API settings shown by ECMWF into `~/.cdsapirc`:

```yaml
url: https://ecds.ecmwf.int/api
key: <PERSONAL-ACCESS-TOKEN>
```

See the [ECMWF CDS API setup guide](https://ecds.ecmwf.int/how-to-api) for account, token, and dataset-licence details. Do not commit this file or its token.

The release workflow has three separate stages. `release-data/` is ignored by Git and holds all downloaded/generated assets.

1. Inspect the next complete snapshot without changing files or publishing anything. Use `--next-tag` for an intentional minor or major release (the current recovery release is `v0.2.0-data`):

   ```bash
   uv run python scripts/release_data.py dry-run --next-tag v0.2.0-data --update uv850-025-netcdf
   ```

2. Download the latest Release into `release-data/`, then regenerate the selected data from ECMWF. New request-catalog entries are downloaded automatically because no prior Release asset exists for them:

   ```bash
   uv run python scripts/release_data.py download --next-tag v0.2.0-data --update uv850-025-netcdf
   ```

3. Review the prepared assets and `SHA256SUMS`, then explicitly confirm that review to publish. It uses the tag recorded during staging and does not call ECMWF:

   ```bash
   uv run python scripts/release_data.py release --confirm-reviewed
   ```

Run `uv run python scripts/fetch_era5.py --list` to see release asset, Git
reference, and logical dataset IDs. Definitions live in
[data/era5_requests.json](data/era5_requests.json). The file is formatted one
field per line so the request parameters are easy to review.

### 2024 F320 assets

The logical datasets `era5-msl-2024-f320` and `era5-vo850-2024-f320` are
distributed as twelve monthly NetCDF assets each. Their canonical filenames
use `era5_<variable>_<period>_<grid>.nc`, for example
`era5_msl_2024-01_f320.nc` and `era5_vo850_2024-01_f320.nc`.

Materialize a staged set only from the verified full-year F320 source pair
(annual NetCDF plus GRIB) with:

```bash
uv run --with-requirements requirements.txt python scripts/materialize_f320_2024.py \
  --source-dir ../PyStormTracker-Reference-Data/era5-2024
```

The command verifies source checksums, GRIB variable/level/grid identity,
F320 geometry, and timestamps. It writes direct contiguous monthly value
slices without regridding or resampling under `prepared/f320-2024/`. It does
not contact CDS and refuses to overwrite an existing monthly file that does
not match the expected metadata. Validate an already prepared set with
`--verify-only`.

TRACK or Validation workflows that retain their historic working filenames can
materialize local annual inputs from the canonical monthly assets without
renaming the Data assets:

```bash
uv run --with-requirements requirements.txt python scripts/materialize_track_f320_2024.py \
  --input-dir prepared/f320-2024 --output-dir /path/to/local-track-inputs
```

This produces `ERA5_mslp_6hr_2024_DET.nc` and
`ERA5_vo850_6hr_2024_DET.nc` only in the specified working directory.

### Manual assets

Use a `"source": "manual"` catalog entry for a locally prepared asset, such as a Zarr archive or a spatially filtered dataset. `source_path` is relative to the repository root; set `archive` to `"tar.gz"` for a directory store. A new manual entry is included automatically in the next `release_data.py download` stage, and an existing entry can be replaced with `--update <id>`.

```json
{
  "id": "vo850-n320-north-atlantic-zarr",
  "source": "manual",
  "summary": "850 hPa vorticity on N320, spatially filtered to the North Atlantic (Zarr archive)",
  "filename": "era5_vo850_2025-2026_djf_n320_north-atlantic.zarr.tar.gz",
  "source_path": "prepared/era5_vo850_2025-2026_djf_n320_north-atlantic.zarr",
  "archive": "tar.gz"
}
```

The staged release always generates `SHA256SUMS` from its final assets. Do not add per-release hashes to the catalog; they change whenever a manual asset is rebuilt.

## Tests

Run the test suite with pytest:

```bash
uv run --with-requirements requirements.txt pytest
```

## Datasets

### ERA5 MSL and VO850 (Dec 2025 - Feb 2026)
This dataset includes Mean Sea Level Pressure (MSL) and 850 hPa Vorticity (VO) data from the ERA5 reanalysis for the period of December 2025 to February 2026 (DJF). NetCDF and GRIB variants are release assets; the small 2.5-degree Zarr stores remain in this repository and are also bundled in new releases.

- **Variables:** `msl`, `vo`, `u`, `v`
- **Resolutions:** 
  - `0.25x0.25` degrees (High resolution)
  - `2.5x2.5` degrees (Coarse resolution)
- **Source:** [ERA5 Reanalysis](https://www.ecmwf.int/en/forecasts/dataset/ecmwf-reanalysis-v5)
- **Format:** NetCDF4 (.nc)

### Compact parity references

Small reference trajectories are tracked in `parity/`, organized by their
actual producer. TRACK 1.5.4 trajectories and historical PyStormTracker
version-parity outputs are distinct. The `reference/ncl/` files are retained
NCL/Spherepack reference fields; their reproduction methodology is maintained
in `PyStormTracker-Validation`, not the software test suite.

See [data/provenance/2026-08-23-recovery-audit.md](data/provenance/2026-08-23-recovery-audit.md)
for recovered-data disposition and provenance.

## License

This dataset is licensed under the [Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE) license.
