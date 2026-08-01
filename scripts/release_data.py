#!/usr/bin/env python3
"""Prepare and publish complete PyStormTracker data releases in three stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from fetch_era5 import DEFAULT_CONFIG, load_catalog, parse_ids


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "mwyau/PyStormTracker-Data"
DEFAULT_STAGE = ROOT / "release-data"
STATE_NAME = ".release-state.json"
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-data$")
DATA_SUFFIXES = (".nc", ".grib")


def run(*command: str, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def output(*command: str) -> str:
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_release(repo: str) -> dict[str, Any]:
    releases = json.loads(output("gh", "api", f"repos/{repo}/releases?per_page=100"))
    candidates = []
    for release in releases:
        match = TAG_PATTERN.fullmatch(release.get("tag_name", ""))
        if match and not release.get("draft"):
            candidates.append((tuple(map(int, match.groups())), release))
    if not candidates:
        raise SystemExit(f"no published vX.Y.Z-data Release found in {repo}")
    return max(candidates, key=lambda item: item[0])[1]


def next_tag(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError(f"not a data tag: {tag}")
    major, minor, patch = map(int, match.groups())
    return f"v{major}.{minor}.{patch + 1}-data"


def release_assets(repo: str, tag: str) -> set[str]:
    release = json.loads(output("gh", "api", f"repos/{repo}/releases/tags/{tag}"))
    return {asset["name"] for asset in release["assets"]}


def classify(entries: list[dict[str, Any]], inherited: set[str], requested: list[str]) -> tuple[list[str], list[str], list[str]]:
    known = {entry["id"] for entry in entries}
    unknown = sorted(set(requested) - known)
    if unknown:
        raise SystemExit("unknown dataset ID(s): " + ", ".join(unknown))
    new = [entry["id"] for entry in entries if entry["filename"] not in inherited]
    download_ids = list(dict.fromkeys([*requested, *new]))
    inherited_ids = [entry["id"] for entry in entries if entry["id"] not in download_ids]
    return inherited_ids, new, download_ids


def archive_zarr(stage: Path) -> None:
    for store in sorted(ROOT.glob("*.zarr")):
        archive = stage / f"{store.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(store, arcname=store.name)


def write_checksums(stage: Path) -> Path:
    assets = sorted(path for path in stage.iterdir() if path.is_file() and path.name not in {"SHA256SUMS", STATE_NAME})
    if not any(path.name.endswith(DATA_SUFFIXES) for path in assets):
        raise SystemExit("staging directory contains no .nc or .grib assets")
    manifest = stage / "SHA256SUMS"
    manifest.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in assets), encoding="utf-8")
    return manifest


def plan(repo: str, config: Path, requested: list[str]) -> dict[str, Any]:
    entries = load_catalog(config)
    base = latest_release(repo)
    inherited = release_assets(repo, base["tag_name"])
    inherited_ids, new_ids, download_ids = classify(entries, inherited, requested)
    summaries = {entry["id"]: entry["summary"] for entry in entries}
    changed = [dataset_id for dataset_id in download_ids]
    note = "Data update: " + "; ".join(summaries[dataset_id] for dataset_id in changed) + "."
    return {
        "base_tag": base["tag_name"],
        "next_tag": next_tag(base["tag_name"]),
        "inherited_ids": inherited_ids,
        "new_ids": new_ids,
        "download_ids": download_ids,
        "note": note,
    }


def dry_run(args: argparse.Namespace) -> None:
    print(json.dumps(plan(args.repo, args.config, parse_ids(args.update)), indent=2))


def download(args: argparse.Namespace) -> None:
    stage = args.stage.resolve()
    if stage.exists() and any(stage.iterdir()):
        raise SystemExit(f"{stage} is not empty; remove its previous contents before downloading")
    stage.mkdir(parents=True, exist_ok=True)
    build_plan = plan(args.repo, args.config, parse_ids(args.update))
    run("gh", "release", "download", build_plan["base_tag"], "--repo", args.repo, "--dir", str(stage), "--clobber")
    (stage / "SHA256SUMS").unlink(missing_ok=True)
    fetcher = ROOT / "scripts" / "fetch_era5.py"
    if build_plan["download_ids"]:
        run("python3", str(fetcher), "--config", str(args.config), "--output-dir", str(stage), "--update", ",".join(build_plan["download_ids"]))
    archive_zarr(stage)
    write_checksums(stage)
    (stage / STATE_NAME).write_text(json.dumps(build_plan, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {build_plan['next_tag']} in {stage}")


def release(args: argparse.Namespace) -> None:
    stage = args.stage.resolve()
    state_path = stage / STATE_NAME
    if not state_path.is_file():
        raise SystemExit(f"{state_path} is missing; run the download stage first")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    current = latest_release(args.repo)["tag_name"]
    if current != state["base_tag"] or next_tag(current) != state["next_tag"]:
        raise SystemExit("prepared data is stale; run dry-run and download again")
    manifest = stage / "SHA256SUMS"
    if not manifest.is_file():
        raise SystemExit("SHA256SUMS is missing; run the download stage again")
    write_checksums(stage)
    if subprocess.run(["git", "rev-parse", "--verify", "--quiet", state["next_tag"]], capture_output=True).returncode == 0:
        raise SystemExit(f"local tag already exists: {state['next_tag']}")
    run("git", "tag", "-a", state["next_tag"], "-m", state["note"])
    run("git", "push", "origin", state["next_tag"])
    assets = sorted(str(path) for path in stage.iterdir() if path.is_file() and path.name != STATE_NAME)
    run("gh", "release", "create", state["next_tag"], *assets, "--repo", args.repo,
        "--title", state["next_tag"], "--notes", state["note"])


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    dry_parser = commands.add_parser("dry-run", help="inspect a release build without writing files")
    add_common(dry_parser)
    dry_parser.add_argument("--update", action="append", default=[], metavar="ID[,ID]")
    dry_parser.set_defaults(handler=dry_run)
    download_parser = commands.add_parser("download", help="download the base release and requested ECMWF data")
    add_common(download_parser)
    download_parser.add_argument("--update", action="append", default=[], metavar="ID[,ID]")
    download_parser.set_defaults(handler=download)
    release_parser = commands.add_parser("release", help="publish a prepared release-data directory")
    add_common(release_parser)
    release_parser.set_defaults(handler=release)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
