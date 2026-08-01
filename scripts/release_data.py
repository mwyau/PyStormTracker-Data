#!/usr/bin/env python3
"""Stage, verify, and publish complete PyStormTracker data releases locally."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_SUFFIXES = (".nc", ".grib")


def run(*command: str, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_checksums(stage: Path) -> Path:
    assets = sorted(path for path in stage.iterdir()
                    if path.is_file() and path.name != "SHA256SUMS")
    if not any(path.name.endswith(DATA_SUFFIXES) for path in assets):
        raise SystemExit("staging directory contains no .nc or .grib assets")
    output = stage / "SHA256SUMS"
    output.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in assets), encoding="utf-8")
    return output


def archive_zarr(stage: Path) -> None:
    for store in sorted(ROOT.glob("*.zarr")):
        archive = stage / f"{store.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(store, arcname=store.name)


def prepare(args: argparse.Namespace) -> None:
    stage = args.stage.resolve()
    stage.mkdir(parents=True, exist_ok=True)
    if args.from_release:
        repo, tag = args.from_release.split("@", maxsplit=1)
        run("gh", "release", "download", tag, "--repo", repo, "--dir", str(stage), "--clobber")
        (stage / "SHA256SUMS").unlink(missing_ok=True)
    for script in args.run:
        run("python3", str((ROOT / script).resolve()), cwd=stage)
    if args.include_zarr:
        archive_zarr(stage)
    write_checksums(stage)
    print(f"Prepared release assets in {stage}")


def publish(args: argparse.Namespace) -> None:
    stage = args.stage.resolve()
    checksums = stage / "SHA256SUMS"
    if not checksums.is_file():
        raise SystemExit("run prepare first; SHA256SUMS is missing")
    write_checksums(stage)
    run("git", "tag", "-a", args.tag, "-m", args.message)
    run("git", "push", "origin", args.tag)
    assets = sorted(str(path) for path in stage.iterdir() if path.is_file())
    run("gh", "release", "create", args.tag, *assets, "--repo", args.repo,
        "--title", args.tag, "--notes", args.message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    prepare_parser = commands.add_parser("prepare", help="make a complete local release snapshot")
    prepare_parser.add_argument("--stage", type=Path, required=True)
    prepare_parser.add_argument("--from-release", metavar="REPO@TAG",
                                help="copy all prior release assets before overlaying outputs")
    prepare_parser.add_argument("--run", action="append", default=[],
                                help="repository-relative CDS script to run; repeatable")
    prepare_parser.add_argument("--include-zarr", action="store_true")
    prepare_parser.set_defaults(handler=prepare)
    publish_parser = commands.add_parser("publish", help="tag, push, and upload a prepared snapshot")
    publish_parser.add_argument("tag")
    publish_parser.add_argument("--stage", type=Path, required=True)
    publish_parser.add_argument("--repo", default="mwyau/PyStormTracker-Data")
    publish_parser.add_argument("--message", default="PyStormTracker data release")
    publish_parser.set_defaults(handler=publish)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
