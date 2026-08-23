# 2026-08-23 scientific-data recovery audit

This record captures the local audit used to establish the Data repository as
the canonical home for external PyStormTracker scientific and reference data.
It is provenance, not a claim that the historical implementations are current
regression baselines.

## Repository audit baseline

| Location | State observed | Disposition |
| --- | --- | --- |
| `PyStormTracker-Data` | `main` at `6228bc9b38f27e512e17bdce6e32b8ad36db95e6`; one local NCL-reference commit ahead of `origin/main`; migration work was untracked | Canonical data, catalog, and release preparation live here. |
| `PyStormTracker` | `benchmarks/pst-native-reconciliation-progress` at `81763710aabee7f79cec9948504cab3446986011`; working tree contained benchmark changes | Read-only source and history audit; no production-code change. |
| `PyStormTracker-Validation` | unborn `master` with untracked validation material | Read-only audit source for F320 recovery, TRACK outputs, and NCL methodology. |
| `PyStormTracker-Private` | absent at the audited path | No source data available. |

The `PyStormTracker` history was inspected with `git log --all -- tests/data`
and `git log --all --name-status -- tests/data`, including commits `4243962`,
`2559cfc`, `7b57156`, `b1ea516`, `d26ed76`, `274df54`, `e039353`, and
`b966762`. The validation backup
`history/track-reconciliation-20260816-pre-cleanup` was also inspected.

## Canonical F320 recovery

The verified local recovery source was
`PyStormTracker-Reference-Data/era5-2024/`:

| Product | Verified annual NetCDF SHA-256 | Verified annual GRIB SHA-256 |
| --- | --- | --- |
| MSL | `a2843cd3277da18b1b9e4c1ac5697e5785bbe65d8879aa3b793ee66280d3b6ff` | `036dbc83a0b9c0f3d1361c6785611ec16199bff3a4f7e98bfe6b07e929f564a8` |
| VO850 | `9bb8aac8ba89398c731643fd475a157a2d421abe379e77039b8b8636c9026554` | `c53c690ca9a98582fdbb0026d26d7296a5feb77c1de4a5b09c6d9189d09ed420` |

Both annual NetCDF files contain 1,464 six-hour frames from `2024-01-01
00:00` through `2024-12-31 18:00`, float32 values, 640 Gaussian latitudes,
and 1,280 longitudes. GRIB inspection confirmed `regular_gg` F320 geometry;
MSL is `msl` in Pa at the surface, and VO850 is `vo` in `s**-1` at 850 hPa.
Each annual GRIB hash equals the ordered concatenation of its twelve retained
monthly GRIB source pieces.

`scripts/materialize_f320_2024.py` verifies that source and writes only the
canonical release assets, split by month. The logical catalog datasets are
`era5-msl-2024-f320` and `era5-vo850-2024-f320`; each contains its ordered
twelve `era5_<variable>_2024-<MM>_f320.nc` release assets.

## Recovered historical references

| Historical item | Final disposition | Rationale |
| --- | --- | --- |
| `era5_msl_2025-2026_djf_2.5x2.5_v0.0.2_imilast.txt` | `parity/pystormtracker-v0.0.2/` | Retained as a PyStormTracker v0.0.2 MSL parity trajectory. |
| `era5_vo_2025-2026_djf_2.5x2.5_1e-4_v0.0.2_imilast.txt` | `parity/pystormtracker-v0.0.2/` | Retained as a PyStormTracker v0.0.2 VO850 parity trajectory. |
| `*_hodges_imilast.txt` | `parity/pystormtracker-v0.5.0.dev/..._imilast.txt` | Retained as PyStormTracker v0.5.0.dev output; the old name was an algorithm label, not TRACK provenance. |
| `*_hodges.txt` | `parity/pystormtracker-v0.5.0.dev/..._track-format.txt` | Retained as PyStormTracker v0.5.0.dev output in Hodges/TRACK text format, not a TRACK result. |
| `*_hodges.trackjson` | `parity/pystormtracker-v0.5.0.dev/...trackjson` | Retained as the compact historical TrackJSON reference; adding commit was in the v0.5.0.dev lineage. |
| `ff_trs_{neg,pos}` from the F320-to-T42 January campaign | `parity/track-1.5.4/` | Retained as final positive/negative TRACK 1.5.4 RSPLICE text trajectories. |
| One-frame 0.25-degree ERA5 MSL and retained NCL T0-42/T5-42 products | `reference/ncl/` | Retained for NCL/Spherepack parity; generation methodology belongs in Validation. |
| Historic one-frame UV/VO, VODV, SFVP, and redundant raw MSL files | Not restored | They have no current consumer. The full-season release datasets supersede raw ERA5 inputs; unused NCL products remain recoverable from history. |

The historical 2.5-degree T5-42 NCL smoke reference intentionally remains in
the software repository. The December MSL NetCDF integration input also
remains there and was not moved or deleted.
