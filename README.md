# PyStormTracker-Data

This repository owns the external scientific data used by
[PyStormTracker](https://github.com/mwyau/PyStormTracker).

## Storage model

- Small parity/reference files are Git-tracked at stable paths.
- The Zarr stores under `integration/` are Git-tracked for direct HTTP access.
- Large NetCDF and GRIB files are complete GitHub Release assets, accompanied
  by `SHA256SUMS`.

Consumers pin one immutable Data tag in both URL bases:

```text
RAW_BASE=https://raw.githubusercontent.com/mwyau/PyStormTracker-Data/<tag>/
RELEASE_BASE=https://github.com/mwyau/PyStormTracker-Data/releases/download/<tag>/
```

The compact reference paths are:

```text
parity/legacy/v0.0.2/
parity/legacy/v0.5.0/
parity/ncl/
parity/track/1.5.4/
```

`parity/legacy/` contains historical PyStormTracker outputs; only
`parity/track/1.5.4/` contains TRACK 1.5.4 output.

## ERA5 acquisition

The manifest in `manifests/era5.json` contains the physical acquisition
definitions. The 2024 F320 MSL and 850 hPa vorticity definitions are active
acquisition entries. The published `v0.2.0-data` Release includes all 24
canonical monthly 2024 F320 NetCDF assets, materialized from existing annual
F320 GRIB sources. Future Data releases inherit those physical assets from the
previous Release unless they are explicitly replaced. The acquisition code
validates the request identity, F320 Gaussian geometry, units, and monthly
six-hourly time coverage.

Use Python 3.14 or newer with the locked uv project:

```bash
uv sync --locked
uv run python scripts/fetch_era5.py --list
```

F320 acquisition also requires the ecCodes command-line tools `grib_get`,
`grib_count`, and `grib_to_netcdf`. On Ubuntu:

```bash
sudo apt install libeccodes-tools
```

Configure CDS credentials outside Git. Fetched files are normally staged in
`release-data/`, which is a resumable, checksum-aware cache. Matching inherited
assets and trustworthy prior staged assets are reused; corrupt or missing files
are replaced, and `--update ID` forces that catalog entry to be acquired again.

## Release preparation

The release workflow keeps the complete large asset set in GitHub Releases and
keeps compact files and Zarr stores in Git. It uses `gh` for release discovery
and asset transfer:

```bash
uv run python scripts/release_data.py dry-run --next-tag v0.2.0-data
uv run python scripts/release_data.py download --next-tag v0.2.0-data
uv run python scripts/release_data.py release
```

The final command verifies the unchanged prepared stage, then creates the tag
and GitHub Release. If an attempted release must be discarded before retrying,
run `gh release delete <tag> --cleanup-tag --yes --repo mwyau/PyStormTracker-Data`.
Then rerun `uv run python scripts/release_data.py release` while the staged
state remains valid.
Inherited Release assets are verified against SHA-256 digests returned by the
GitHub Releases API. Each prepared release also publishes SHA256SUMS.
For corrections after publication, prepare a new patch Data release, such as
`v0.2.1-data`. Use `--update ID` with `dry-run` and `download` when a physical
dataset must be refreshed.

## NCL/Spherepack references

The static files under `parity/ncl/` are generated from the full DJF MSL
Release assets staged in `release-data/`:

```bash
ncl scripts/ncl/generate_msl_spectral_references.ncl
```

The generator checks that the first source frame is `2025-12-01 00:00` and
accepts explicit `src25`, `src025`, `release_dir`, and `outdir` overrides.

## Tests

```bash
uv run pytest
```

## License

This dataset is licensed under the
[Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE) license.
