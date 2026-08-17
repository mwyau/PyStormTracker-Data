# NCL/Spherepack parity references

These compact NetCDF files support numerical-parity comparisons for
PyStormTracker's spherical-harmonic filtering and are kept outside the main
software source distribution except for the single bundled smoke-test case.

The fields use the ERA5 mean-sea-level-pressure frame at
`2025-12-01 00:00`. The 2.5-degree frame is selected from the December 2025
monthly input; the 0.25-degree frame is a one-frame source extracted from the
corresponding ERA5 high-resolution source. The NCL outputs were generated with
NCL 6.6.2 using `shaeC`/`shseC` and the documented T0-42 or T5-42 truncation.

Reference-generation methodology is maintained in the sibling
`PyStormTracker-Validation/scripts/ncl/` directory. These files are direct
reference inputs for the package parity tests; no manifest or checksum
registry is required here.
