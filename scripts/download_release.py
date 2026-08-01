#!/usr/bin/env python3
"""Download and verify the non-Zarr data assets for a GitHub data release."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        checksums[name.lstrip(" *")] = digest
    return checksums


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="GitHub Release tag, e.g. v0.1.3-data")
    parser.add_argument("--repo", default="mwyau/PyStormTracker-Data")
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["gh", "release", "download", args.tag, "SHA256SUMS", "--repo", args.repo,
         "--dir", str(args.output_dir), "--clobber"],
        check=True,
    )
    checksums = parse_checksums(args.output_dir / "SHA256SUMS")
    assets = [name for name in checksums if name.endswith((".nc", ".grib"))]
    if not assets:
        raise SystemExit("SHA256SUMS contains no NetCDF or GRIB assets")
    subprocess.run(
        ["gh", "release", "download", args.tag, *assets, "--repo", args.repo,
         "--dir", str(args.output_dir), "--clobber"],
        check=True,
    )
    failures = [name for name in assets if sha256(args.output_dir / name) != checksums[name]]
    if failures:
        raise SystemExit("checksum verification failed: " + ", ".join(failures))
    print(f"Downloaded and verified {len(assets)} assets in {args.output_dir}")


if __name__ == "__main__":
    main()
