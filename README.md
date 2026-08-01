# PyStormTracker-Data

This repository hosts dataset recipes, Zarr stores, and release metadata used by [PyStormTracker](https://github.com/mwyau/PyStormTracker).

Large NetCDF (`.nc`) and GRIB (`.grib`) data files are published as GitHub Release assets, not in Git or Git LFS. Download a tagged data snapshot with:

```bash
python3 scripts/download_release.py v0.1.3-data --output-dir data
```

The command downloads the files listed in the release's `SHA256SUMS` asset and verifies every checksum. See `scripts/release_data.py --help` for the local release-building workflow.

## Datasets

### ERA5 MSL and VO850 (Dec 2025 - Feb 2026)
This dataset includes Mean Sea Level Pressure (MSL) and 850 hPa Vorticity (VO) data from the ERA5 reanalysis for the period of December 2025 to February 2026 (DJF). NetCDF and GRIB variants are release assets; the small 2.5-degree Zarr stores remain in this repository and are also bundled in new releases.

- **Variables:** `msl`, `vo`
- **Resolutions:** 
  - `0.25x0.25` degrees (High resolution)
  - `2.5x2.5` degrees (Coarse resolution)
- **Source:** [ERA5 Reanalysis](https://www.ecmwf.int/en/forecasts/dataset/ecmwf-reanalysis-v5)
- **Format:** NetCDF4 (.nc)

## License

This dataset is licensed under the [Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE) license.
