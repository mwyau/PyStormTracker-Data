#!/usr/bin/env python3
"""Prepare and publish complete PyStormTracker data releases in three stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

from fetch_era5 import (
    CDS_SOURCE,
    DEFAULT_CONFIG,
    MANUAL_SOURCE,
    load_catalog,
    load_catalog_document,
    parse_ids,
    resolve_release_entries,
)


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


def tag_version(tag: str) -> tuple[int, int, int]:
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError(f"not a data tag: {tag}")
    return tuple(map(int, match.groups()))


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


def stage_manual_assets(stage: Path, entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        if entry["source"] != MANUAL_SOURCE:
            continue
        source = ROOT / entry["source_path"]
        target = stage / entry["filename"]
        if not source.exists():
            raise SystemExit(f"manual source for {entry['id']} is missing: {source}")
        if entry.get("archive") == "tar.gz":
            if not source.is_dir():
                raise SystemExit(f"manual archive source for {entry['id']} must be a directory: {source}")
            with tarfile.open(target, "w:gz") as tar:
                tar.add(source, arcname=source.name)
        elif source.is_file():
            shutil.copy2(source, target)
        else:
            raise SystemExit(f"manual source for {entry['id']} must be a file or a tar.gz directory")


def write_checksums(stage: Path) -> Path:
    assets = sorted(path for path in stage.iterdir() if path.is_file() and path.name not in {"SHA256SUMS", STATE_NAME})
    if not any(path.name.endswith(DATA_SUFFIXES) for path in assets):
        raise SystemExit("staging directory contains no .nc or .grib assets")
    manifest = stage / "SHA256SUMS"
    manifest.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in assets), encoding="utf-8")
    return manifest


def plan(
    repo: str,
    config: Path,
    requested: list[str],
    requested_next_tag: str | None = None,
) -> dict[str, Any]:
    catalog = load_catalog_document(config)
    entries = catalog["datasets"]
    requested_entries = resolve_release_entries(catalog, requested)
    requested_asset_ids = [entry["id"] for entry in requested_entries]
    base = latest_release(repo)
    inherited = release_assets(repo, base["tag_name"])
    inherited_ids, new_ids, download_ids = classify(entries, inherited, requested_asset_ids)
    summaries = {entry["id"]: entry["summary"] for entry in entries}
    changed = [dataset_id for dataset_id in download_ids]
    note = "Data update: " + "; ".join(summaries[dataset_id] for dataset_id in changed) + "."
    if requested_next_tag is not None and not TAG_PATTERN.fullmatch(requested_next_tag):
        raise SystemExit(f"not a data tag: {requested_next_tag}")
    target_tag = requested_next_tag or next_tag(base["tag_name"])
    if tag_version(target_tag) <= tag_version(base["tag_name"]):
        raise SystemExit("next release tag must be newer than the published base tag")
    return {
        "base_tag": base["tag_name"],
        "next_tag": target_tag,
        "inherited_ids": inherited_ids,
        "new_ids": new_ids,
        "download_ids": download_ids,
        "note": note,
    }


def dry_run(args: argparse.Namespace) -> None:
    print(json.dumps(plan(args.repo, args.config, parse_ids(args.update), args.next_tag), indent=2))


def download(args: argparse.Namespace) -> None:
    stage = args.stage.resolve()
    if stage.exists() and any(stage.iterdir()):
        raise SystemExit(f"{stage} is not empty; remove its previous contents before downloading")
    stage.mkdir(parents=True, exist_ok=True)
    build_plan = plan(args.repo, args.config, parse_ids(args.update), args.next_tag)
    run("gh", "release", "download", build_plan["base_tag"], "--repo", args.repo, "--dir", str(stage), "--clobber")
    (stage / "SHA256SUMS").unlink(missing_ok=True)
    fetcher = ROOT / "scripts" / "fetch_era5.py"
    entries_by_id = {entry["id"]: entry for entry in load_catalog(args.config)}
    selected = [entries_by_id[dataset_id] for dataset_id in build_plan["download_ids"]]
    cds_ids = [entry["id"] for entry in selected if entry["source"] == CDS_SOURCE]
    if cds_ids:
        run(sys.executable, str(fetcher), "--config", str(args.config), "--output-dir", str(stage), "--update", ",".join(cds_ids))
    stage_manual_assets(stage, selected)
    write_checksums(stage)
    (stage / STATE_NAME).write_text(json.dumps(build_plan, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {build_plan['next_tag']} in {stage}")


def release(args: argparse.Namespace) -> None:
    if not args.confirm_reviewed:
        raise SystemExit("review the staged assets and SHA256SUMS, then rerun with --confirm-reviewed")
    stage = args.stage.resolve()
    state_path = stage / STATE_NAME
    if not state_path.is_file():
        raise SystemExit(f"{state_path} is missing; run the download stage first")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    current = latest_release(args.repo)["tag_name"]
    if current != state["base_tag"] or tag_version(state["next_tag"]) <= tag_version(current):
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


def add_next_tag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--next-tag",
        help="explicit release tag; defaults to the next patch data tag",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    dry_parser = commands.add_parser("dry-run", help="inspect a release build without writing files")
    add_common(dry_parser)
    add_next_tag(dry_parser)
    dry_parser.add_argument("--update", action="append", default=[], metavar="ID[,ID]")
    dry_parser.set_defaults(handler=dry_run)
    download_parser = commands.add_parser("download", help="download the base release and requested ECMWF data")
    add_common(download_parser)
    add_next_tag(download_parser)
    download_parser.add_argument("--update", action="append", default=[], metavar="ID[,ID]")
    download_parser.set_defaults(handler=download)
    release_parser = commands.add_parser("release", help="publish a prepared release-data directory")
    add_common(release_parser)
    release_parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="confirm that the staged assets and SHA256SUMS have been reviewed",
    )
    release_parser.set_defaults(handler=release)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
