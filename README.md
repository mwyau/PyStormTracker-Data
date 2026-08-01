# PyStormTracker-Data

This repository hosts dataset recipes, Zarr stores, and release metadata used by [PyStormTracker](https://github.com/mwyau/PyStormTracker).

## Building a data release

Install the ECMWF CDS client:

```bash
pip install -r requirements.txt
```

Create an ECMWF/CDS account and accept the terms for each ERA5 dataset used by this repository. While signed in, copy the API settings shown by ECMWF into `~/.cdsapirc`:

```yaml
url: https://ecds.ecmwf.int/api
key: <PERSONAL-ACCESS-TOKEN>
```

See the [ECMWF CDS API setup guide](https://ecds.ecmwf.int/how-to-api) for account, token, and dataset-licence details. Do not commit this file or its token.

The release workflow has three separate stages. `release-data/` is ignored by Git and holds all downloaded/generated assets.

1. Inspect the next complete snapshot without changing files or publishing anything:

   ```bash
   python3 scripts/release_data.py dry-run --update uv850-025-netcdf
   ```

2. Download the latest Release into `release-data/`, then regenerate the selected data from ECMWF. New request-catalog entries are downloaded automatically because no prior Release asset exists for them:

   ```bash
   python3 scripts/release_data.py download --update uv850-025-netcdf
   ```

3. Verify and publish the prepared snapshot. This automatically increments the patch tag (for example, `v0.1.3-data` becomes `v0.1.4-data`) and does not call ECMWF:

   ```bash
   python3 scripts/release_data.py release
   ```

Run `python3 scripts/fetch_era5.py --list` to see dataset IDs. Request definitions live in [data/era5_requests.json](data/era5_requests.json). The file is formatted one field per line so the request parameters are easy to review.

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
pytest
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

## License

This dataset is licensed under the [Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE) license.
