# PyStormTracker-Data

This repository holds the external scientific data used by
[PyStormTracker](https://github.com/mwyau/PyStormTracker). The data contract is
intentionally based on ordinary paths and filenames.

## Storage model

- Small parity/reference files are Git-tracked and served from their exact path
  through `raw.githubusercontent.com`.
- The two Git-tracked Zarr stores under `integration/` are also served object by
  object through the raw Git URL. They are not duplicated as release archives.
- Large monolithic NetCDF and GRIB files are GitHub Release assets. The 2024
  F320 collection consists of twelve monthly NetCDF files for MSL and twelve
  for 850 hPa vorticity.

A PyStormTracker consumer pins one immutable Data tag and uses that same tag in
both URL bases:

```text
RAW_BASE=https://raw.githubusercontent.com/mwyau/PyStormTracker-Data/<tag>/
RELEASE_BASE=https://github.com/mwyau/PyStormTracker-Data/releases/download/<tag>/
```

Small files are fetched with paths such as
`parity/ncl/<file>`, `parity/legacy/v0.0.2/<file>`, and
`parity/track/1.5.4/<file>`. Large files are fetched with their release
filename, for example `era5_msl_2024-01_f320.nc`. A full-year F320 workflow
constructs the twelve monthly filenames directly and opens them lazily.

The `parity/legacy/` files are historical PyStormTracker outputs. They are not
TRACK output even when one file uses a TRACK-compatible text format. Only files
under `parity/track/1.5.4/` are genuine TRACK 1.5.4 output. NCL/Spherepack
reference files live under `parity/ncl/`; generation scripts and experiment-
specific staging belong to the sibling `PyStormTracker-Validation`
repository.

The ordinary offline integration input
`PyStormTracker/tests/data/era5/era5_msl_2025-12_2.5x2.5.nc` remains in the
software repository and does not depend on this repository or the network.

## Acquiring ERA5 F320 data

Install the tools with `uv` and configure the CDS API credentials outside
Git:

```bash
uv pip install -r requirements.txt
uv run --with-requirements requirements.txt python scripts/fetch_era5.py --list
```

F320 acquisition is month by month. For each selected entry,
`fetch_era5.py` retrieves the exact CDS ERA5 Complete MARS request, checks the
returned GRIB's variable, level, F320 geometry, and frame count, converts it to
canonical float32 NetCDF, and checks the monthly time coverage, units, geometry,
and readability before promoting the output.

The release workflow stages the physical files and writes `SHA256SUMS`:

```bash
uv run --with-requirements requirements.txt python scripts/release_data.py dry-run --next-tag <new-data-tag>
uv run --with-requirements requirements.txt python scripts/release_data.py download --next-tag <new-data-tag>
# review release-data/ and SHA256SUMS
uv run --with-requirements requirements.txt python scripts/release_data.py release --confirm-reviewed
```

The release workflow does not inspect or register parity paths. Small Git files
already have stable identity from the pinned Git tag.

## Tests

```bash
uv run --with-requirements requirements.txt pytest
```

## License

This dataset is licensed under the
[Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE) license.
