#!/usr/bin/env python3
"""Stage and publish physical PyStormTracker-Data release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fetch_era5 import (
    DEFAULT_CONFIG,
    load_catalog_document,
    parse_ids,
    select_entries,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "mwyau/PyStormTracker-Data"
DEFAULT_STAGE = ROOT / "release-data"
STATE_NAME = ".release-state.json"
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-data$")
DATA_SUFFIXES = (".nc", ".grib")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def run(*command: str, cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as error:
        if command and command[0] == "gh":
            raise SystemExit(
                "gh CLI is required for release discovery and downloads"
            ) from error
        raise


def output(*command: str, cwd: Path | None = None) -> str:
    try:
        return subprocess.run(
            command, cwd=cwd, check=True, text=True, capture_output=True
        ).stdout
    except FileNotFoundError as error:
        if command and command[0] == "gh":
            raise SystemExit(
                "gh CLI is required for release discovery and downloads"
            ) from error
        raise


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_release(repo: str) -> dict[str, Any] | None:
    """Return the newest published GitHub Release, regardless of its tag name."""
    releases = json.loads(
        output(
            "gh",
            "release",
            "list",
            "--repo",
            repo,
            "--limit",
            "100",
            "--json",
            "tagName,publishedAt,createdAt,isDraft",
        )
    )
    if not isinstance(releases, list):
        raise SystemExit("GitHub releases response was not a list")

    published = [
        release
        for release in releases
        if isinstance(release, dict)
        and not release.get("isDraft")
    ]
    if not published:
        return None
    latest = max(
        published,
        key=lambda release: (
            str(release.get("publishedAt", "")),
            str(release.get("createdAt", "")),
        ),
    )
    return {
        "tag_name": latest.get("tagName"),
        "draft": latest.get("isDraft"),
        "published_at": latest.get("publishedAt"),
        "created_at": latest.get("createdAt"),
    }


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


def release_assets(repo: str, tag: str) -> dict[str, Any]:
    release = json.loads(output("gh", "api", f"repos/{repo}/releases/tags/{tag}"))
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise SystemExit(f"release {tag} has an invalid asset list")
    return {
        asset["name"]: asset.get("digest")
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }


def parse_checksums(path: Path) -> dict[str, str]:
    """Parse a SHA256SUMS file into a filename-to-digest mapping."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise SystemExit(f"cannot read checksum manifest {path}: {error}") from error

    checksums: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split(maxsplit=1)
        if len(fields) != 2 or not SHA256_PATTERN.fullmatch(fields[0]):
            raise SystemExit(
                f"invalid SHA256SUMS entry at {path}:{line_number}: {line!r}"
            )
        filename = fields[1].strip()
        if filename.startswith("*"):
            filename = filename[1:]
        if not filename or Path(filename).name != filename:
            raise SystemExit(
                f"invalid SHA256SUMS filename at {path}:{line_number}: {filename!r}"
            )
        digest = fields[0].lower()
        previous = checksums.get(filename)
        if previous is not None and previous != digest:
            raise SystemExit(f"conflicting SHA256SUMS entries for {filename}")
        checksums[filename] = digest
    return checksums


def parse_release_digest(value: Any, filename: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise SystemExit(
            f"inherited release asset {filename} has no usable SHA-256 digest"
        )
    digest = value.removeprefix("sha256:")
    if not SHA256_PATTERN.fullmatch(digest):
        raise SystemExit(
            f"inherited release asset {filename} has no usable SHA-256 digest"
        )
    return digest.lower()


def _is_data_asset(filename: str) -> bool:
    return filename.endswith(DATA_SUFFIXES)


def classify(
    entries: list[dict[str, Any]],
    inherited: set[str],
    requested: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Classify physical entries against inherited release asset names."""
    known = {entry["id"] for entry in entries}
    unknown = sorted(set(requested) - known)
    if unknown:
        raise SystemExit("unknown dataset ID(s): " + ", ".join(unknown))

    new = [entry["id"] for entry in entries if entry["filename"] not in inherited]
    download_ids = list(dict.fromkeys([*requested, *new]))
    inherited_ids = [
        entry["id"] for entry in entries if entry["filename"] in inherited
    ]
    return inherited_ids, new, download_ids


def write_checksums(stage: Path) -> Path:
    """Write a deterministic checksum manifest for staged data assets."""
    assets = sorted(
        path
        for path in stage.iterdir()
        if path.is_file() and _is_data_asset(path.name)
    )
    if not assets:
        raise SystemExit("staging directory contains no .nc or .grib assets")
    manifest = stage / "SHA256SUMS"
    manifest.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in assets),
        encoding="utf-8",
    )
    return manifest


def plan(
    repo: str,
    config: Path,
    requested: list[str],
    requested_next_tag: str | None = None,
) -> dict[str, Any]:
    """Build a release plan from physical acquisition entries."""
    catalog = load_catalog_document(config)
    entries = catalog["datasets"]
    requested_entries = select_entries(entries, requested)
    requested_asset_ids = [entry["id"] for entry in requested_entries]
    base = latest_release(repo)
    base_tag = None if base is None else base["tag_name"]
    inherited = (
        {} if base_tag is None else release_assets(repo, base_tag)
    )
    inherited_ids, new_ids, download_ids = classify(
        entries, set(inherited), requested_asset_ids
    )

    if requested_next_tag is not None and not TAG_PATTERN.fullmatch(requested_next_tag):
        raise SystemExit(f"not a data tag: {requested_next_tag}")
    if requested_next_tag is None:
        if base_tag is None:
            raise SystemExit(
                "no published GitHub Release exists; provide --next-tag"
            )
        if not TAG_PATTERN.fullmatch(base_tag):
            raise SystemExit(
                "latest published GitHub Release uses a legacy tag; provide --next-tag"
            )
        target_tag = next_tag(base_tag)
    else:
        target_tag = requested_next_tag

    if base_tag is not None and TAG_PATTERN.fullmatch(base_tag):
        if tag_version(target_tag) <= tag_version(base_tag):
            raise SystemExit("next release tag must be newer than the published base tag")

    summaries = {entry["id"]: entry["summary"] for entry in entries}
    changed = [dataset_id for dataset_id in download_ids]
    note = (
        "Data update: "
        + "; ".join(summaries[dataset_id] for dataset_id in changed)
        + "."
        if changed
        else "Data release with no catalog changes."
    )
    return {
        "base_tag": base_tag,
        "next_tag": target_tag,
        "inherited_assets": sorted(inherited),
        "inherited_ids": inherited_ids,
        "new_ids": new_ids,
        "download_ids": download_ids,
        "note": note,
    }


def _read_local_checksums(stage: Path) -> dict[str, str]:
    manifest = stage / "SHA256SUMS"
    if not manifest.is_file():
        return {}
    try:
        return parse_checksums(manifest)
    except SystemExit:
        # A previous staging manifest is only a reuse hint.  An invalid one is
        # untrustworthy but should not prevent the workflow from repairing the
        # staged data and writing a fresh manifest.
        return {}


def _download_release_asset(
    repo: str, tag: str, filename: str, directory: Path
) -> Path:
    """Download one named GitHub Release asset into an empty directory."""
    run(
        "gh",
        "release",
        "download",
        tag,
        "--repo",
        repo,
        "--pattern",
        filename,
        "--dir",
        str(directory),
    )
    path = directory / filename
    if not path.is_file():
        raise SystemExit(f"gh did not download release asset {filename}")
    return path


def _stage_inherited_assets(
    repo: str,
    tag: str,
    asset_digests: dict[str, Any],
    stage: Path,
    forced_names: set[str],
) -> tuple[list[str], list[str]]:
    """Reuse or checksum-verify/download physical assets from a base release."""
    physical_assets = sorted(
        name for name in asset_digests if _is_data_asset(name)
    )
    if not physical_assets:
        return [], []

    reused: list[str] = []
    refreshed: list[str] = []
    with tempfile.TemporaryDirectory(prefix=".base-download-", dir=stage) as raw_dir:
        directory = Path(raw_dir)
        for filename in physical_assets:
            expected = parse_release_digest(asset_digests.get(filename), filename)
            if filename in forced_names:
                # An explicit update is acquired from the catalog source
                # below; do not first restore the old release copy.
                continue
            target = stage / filename
            if (
                target.is_file()
                and sha256(target) == expected
            ):
                reused.append(filename)
                continue

            downloaded = _download_release_asset(repo, tag, filename, directory)
            actual = sha256(downloaded)
            if actual != expected:
                raise SystemExit(
                    f"checksum mismatch for downloaded base asset {filename}"
                )
            downloaded.replace(target)
            refreshed.append(filename)
    return reused, refreshed


def _required_assets(
    entries: list[dict[str, Any]], inherited_assets: set[str]
) -> list[str]:
    names = {entry["filename"] for entry in entries}
    names.update(name for name in inherited_assets if _is_data_asset(name))
    return sorted(names)


def dry_run(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            plan(args.repo, args.config, parse_ids(args.update), args.next_tag),
            indent=2,
        )
    )


def download(args: argparse.Namespace) -> None:
    if output(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
        cwd=ROOT,
    ).strip():
        raise SystemExit(
            "tracked Git worktree is not clean; commit or discard changes before download"
        )
    source_commit = output("git", "rev-parse", "HEAD", cwd=ROOT).strip()
    stage = args.stage.resolve()
    stage.mkdir(parents=True, exist_ok=True)
    requested = parse_ids(args.update)
    build_plan = plan(args.repo, args.config, requested, args.next_tag)
    entries = load_catalog_document(args.config)["datasets"]
    by_id = {entry["id"]: entry for entry in entries}
    prior_checksums = _read_local_checksums(stage)
    inherited_assets = set(build_plan["inherited_assets"])
    forced_ids = set(requested)
    forced_names = {
        by_id[dataset_id]["filename"]
        for dataset_id in forced_ids
        if dataset_id in by_id
    }

    reused: list[str] = []
    refreshed: list[str] = []
    if build_plan["base_tag"] is not None:
        inherited_digests = release_assets(args.repo, build_plan["base_tag"])
        inherited_reused, inherited_refreshed = _stage_inherited_assets(
            args.repo,
            build_plan["base_tag"],
            inherited_digests,
            stage,
            forced_names,
        )
        reused.extend(inherited_reused)
        refreshed.extend(inherited_refreshed)

    acquire_ids: list[str] = []
    for dataset_id in build_plan["download_ids"]:
        entry = by_id[dataset_id]
        filename = entry["filename"]
        target = stage / filename
        if dataset_id in forced_ids:
            acquire_ids.append(dataset_id)
        elif filename in inherited_assets:
            # The inherited path above already reused or fetched this asset.
            continue
        elif target.is_file() and prior_checksums.get(filename) == sha256(target):
            reused.append(filename)
        else:
            acquire_ids.append(dataset_id)

    fetcher = ROOT / "scripts" / "fetch_era5.py"
    if acquire_ids:
        run(
            sys.executable,
            str(fetcher),
            "--config",
            str(args.config),
            "--output-dir",
            str(stage),
            "--overwrite",
            "--update",
            ",".join(acquire_ids),
        )
        refreshed.extend(by_id[dataset_id]["filename"] for dataset_id in acquire_ids)

    required = _required_assets(entries, inherited_assets)
    missing = [filename for filename in required if not (stage / filename).is_file()]
    if missing:
        raise SystemExit(
            "staging directory is missing intended release assets: "
            + ", ".join(missing)
        )

    write_checksums(stage)
    state = dict(build_plan)
    state.update(
        {
            "source_commit": source_commit,
            "required_assets": required,
            "reused_assets": sorted(set(reused)),
            "refreshed_assets": sorted(set(refreshed)),
            "acquired_ids": acquire_ids,
        }
    )
    (stage / STATE_NAME).write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {build_plan['next_tag']} in {stage}")
    if reused:
        print("Reused cached assets: " + ", ".join(sorted(set(reused))))
    if refreshed:
        print("Refreshed assets: " + ", ".join(sorted(set(refreshed))))


def release(args: argparse.Namespace) -> None:
    stage = args.stage.resolve()
    state_path = stage / STATE_NAME
    if not state_path.is_file():
        raise SystemExit(f"{state_path} is missing; run the download stage first")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    next_release_tag = state.get("next_tag")
    if not isinstance(next_release_tag, str) or not TAG_PATTERN.fullmatch(
        next_release_tag
    ):
        raise SystemExit("staged release has an invalid Data tag")

    source_commit = state.get("source_commit")
    if not isinstance(source_commit, str) or not source_commit:
        raise SystemExit("staged release has no source commit; run download again")
    required = state.get("required_assets")
    if (
        not isinstance(required, list)
        or any(
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or not _is_data_asset(filename)
            for filename in required
        )
    ):
        raise SystemExit(
            "staged release has an invalid required asset list; run download again"
        )
    if len(required) != len(set(required)):
        raise SystemExit(
            "staged release has an invalid required asset list; run download again"
        )
    required = sorted(required)

    current_commit = output("git", "rev-parse", "HEAD", cwd=ROOT).strip()
    if current_commit != source_commit:
        raise SystemExit(
            "current Git HEAD differs from the prepared source commit; run download again"
        )
    if output(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
        cwd=ROOT,
    ).strip():
        raise SystemExit("tracked Git worktree is not clean; run download again")

    manifest = stage / "SHA256SUMS"
    if not manifest.is_file():
        raise SystemExit("SHA256SUMS is missing; run the download stage again")
    checksums = parse_checksums(manifest)
    required_set = set(required)
    staged_data = {
        path.name
        for path in stage.iterdir()
        if path.is_file() and _is_data_asset(path.name)
    }
    unexpected = sorted(staged_data - required_set)
    if unexpected:
        raise SystemExit(
            "unexpected staged release assets: " + ", ".join(unexpected)
        )
    if set(checksums) != required_set:
        raise SystemExit(
            "SHA256SUMS does not match intended release assets; run download again"
        )
    missing = [filename for filename in required if not (stage / filename).is_file()]
    if missing:
        raise SystemExit(
            "staging directory is missing intended release assets: "
            + ", ".join(missing)
        )
    if any(sha256(stage / filename) != checksums[filename] for filename in required):
        raise SystemExit("staged assets changed after preparation; run download again")

    current = latest_release(args.repo)
    current_tag = None if current is None else current["tag_name"]
    if current_tag != state.get("base_tag") or (
        current_tag is not None
        and TAG_PATTERN.fullmatch(current_tag)
        and tag_version(next_release_tag) <= tag_version(current_tag)
    ):
        raise SystemExit("prepared data is stale; run dry-run and download again")

    assets = [str(stage / filename) for filename in required]
    assets.append(str(manifest))
    run(
        "gh",
        "release",
        "create",
        next_release_tag,
        *assets,
        "--repo",
        args.repo,
        "--target",
        source_commit,
        "--title",
        next_release_tag,
        "--notes",
        state["note"],
    )


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)


def add_next_tag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--next-tag",
        help="explicit vX.Y.Z-data tag; required for a legacy or absent base release",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)

    dry_parser = commands.add_parser(
        "dry-run", help="inspect a release build without writing files"
    )
    add_common(dry_parser)
    add_next_tag(dry_parser)
    dry_parser.add_argument("--update", action="append", default=[], metavar="ID[,ID]")
    dry_parser.set_defaults(handler=dry_run)

    download_parser = commands.add_parser(
        "download", help="resume or prepare a release-data staging directory"
    )
    add_common(download_parser)
    add_next_tag(download_parser)
    download_parser.add_argument(
        "--update", action="append", default=[], metavar="ID[,ID]"
    )
    download_parser.set_defaults(handler=download)

    release_parser = commands.add_parser(
        "release", help="publish a prepared release-data directory"
    )
    add_common(release_parser)
    release_parser.set_defaults(handler=release)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
