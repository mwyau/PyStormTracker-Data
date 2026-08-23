# NCL/Spherepack parity references

These compact NetCDF files are the Data-repository NCL/Spherepack reference
collection for PyStormTracker's spherical-harmonic filtering checks.

The fields use the ERA5 mean-sea-level-pressure frame at
`2025-12-01 00:00`. The 2.5-degree frame is selected from the December 2025
monthly input; the 0.25-degree frame is a one-frame source extracted from the
corresponding ERA5 high-resolution source. The NCL outputs were generated with
NCL 6.6.2 using `shaeC`/`shseC` and the documented T0-42 or T5-42 truncation.

Reference-generation methodology is maintained in the sibling
`PyStormTracker-Validation/scripts/ncl/` directory. Consumers use these files
by their direct paths under `parity/ncl/`; no catalog or checksum registry is
needed. The separate 2.5-degree T5-42 smoke reference remains bundled with
`PyStormTracker/tests/data/ncl/`.
