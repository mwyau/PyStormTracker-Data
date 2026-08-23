#!/usr/bin/env python3
"""Inspect and fetch release-backed ERA5 assets from the data catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "data" / "era5_requests.json"
CATALOG_SCHEMA_VERSION = 2
CDS_SOURCE = "cds"
MANUAL_SOURCE = "manual"


def load_catalog_document(path: Path) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read request catalog {path}: {error}") from error
    entries = catalog.get("datasets")
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION or not isinstance(entries, list):
        raise SystemExit(
            f"request catalog must contain schema_version {CATALOG_SCHEMA_VERSION} and a datasets list"
        )
    common_required = {"id", "source", "summary", "filename"}
    ids: set[str] = set()
    filenames: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or common_required - entry.keys():
            raise SystemExit(f"invalid request catalog entry: {entry!r}")
        source = entry["source"]
        if source == CDS_SOURCE:
            required = {"dataset", "request"}
            if required - entry.keys() or not isinstance(entry["request"], dict):
                raise SystemExit(f"invalid CDS request catalog entry: {entry!r}")
        elif source == MANUAL_SOURCE:
            if not isinstance(entry.get("source_path"), str):
                raise SystemExit(f"manual entry {entry['id']} must contain a source_path")
            if entry.get("archive") not in {None, "tar.gz"}:
                raise SystemExit(f"manual entry {entry['id']} has unsupported archive type")
        else:
            raise SystemExit(f"invalid source for {entry['id']}: {source!r}")
        if entry["id"] in ids or entry["filename"] in filenames:
            raise SystemExit(f"duplicate dataset ID or filename: {entry['id']}")
        ids.add(entry["id"])
        filenames.add(entry["filename"])

    git_references = catalog.get("git_references", [])
    if not isinstance(git_references, list):
        raise SystemExit("git_references must be a list")
    for reference in git_references:
        if not isinstance(reference, dict) or {"id", "path", "summary", "provenance"} - reference.keys():
            raise SystemExit(f"invalid Git reference entry: {reference!r}")
        path_value = reference["path"]
        if not isinstance(path_value, str) or Path(path_value).is_absolute() or ".." in Path(path_value).parts:
            raise SystemExit(f"Git reference {reference['id']} has an invalid relative path")
        if reference["id"] in ids:
            raise SystemExit(f"duplicate catalog ID: {reference['id']}")
        ids.add(reference["id"])

    logical_datasets = catalog.get("logical_datasets", [])
    if not isinstance(logical_datasets, list):
        raise SystemExit("logical_datasets must be a list")
    logical_ids: set[str] = set()
    for logical in logical_datasets:
        if not isinstance(logical, dict) or {"id", "summary", "assets", "provenance"} - logical.keys():
            raise SystemExit(f"invalid logical dataset entry: {logical!r}")
        asset_ids = logical["assets"]
        if not isinstance(asset_ids, list) or not asset_ids or not all(isinstance(asset_id, str) for asset_id in asset_ids):
            raise SystemExit(f"logical dataset {logical['id']} must contain a non-empty assets list")
        if len(asset_ids) != len(set(asset_ids)) or set(asset_ids) - ids:
            raise SystemExit(f"logical dataset {logical['id']} has unknown or duplicate assets")
        if logical["id"] in ids or logical["id"] in logical_ids:
            raise SystemExit(f"duplicate catalog ID: {logical['id']}")
        logical_ids.add(logical["id"])

    return catalog


def load_catalog(path: Path) -> list[dict[str, Any]]:
    """Return physical release assets for compatibility with release tooling."""
    return load_catalog_document(path)["datasets"]


def parse_ids(values: list[str]) -> list[str]:
    return [item.strip() for value in values for item in value.split(",") if item.strip()]


def select_entries(entries: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    by_id = {entry["id"]: entry for entry in entries}
    unknown = sorted(set(ids) - by_id.keys())
    if unknown:
        raise SystemExit("unknown dataset ID(s): " + ", ".join(unknown))
    return [by_id[dataset_id] for dataset_id in ids]


def resolve_release_entries(catalog: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    """Resolve physical asset or logical dataset IDs to release-backed assets."""
    entries = catalog["datasets"]
    by_id = {entry["id"]: entry for entry in entries}
    logical_by_id = {entry["id"]: entry for entry in catalog.get("logical_datasets", [])}
    git_ids = {entry["id"] for entry in catalog.get("git_references", [])}
    selected: list[dict[str, Any]] = []
    for dataset_id in ids:
        if dataset_id in by_id:
            selected.append(by_id[dataset_id])
            continue
        if dataset_id in logical_by_id:
            assets = logical_by_id[dataset_id]["assets"]
            unsupported = [asset_id for asset_id in assets if asset_id in git_ids]
            if unsupported:
                raise SystemExit(
                    f"logical dataset {dataset_id} contains Git-tracked references and cannot be fetched: "
                    + ", ".join(unsupported)
                )
            selected.extend(by_id[asset_id] for asset_id in assets)
            continue
        if dataset_id in git_ids:
            raise SystemExit(f"Git-tracked reference {dataset_id} cannot be fetched from CDS or a Release")
        raise SystemExit("unknown dataset ID(s): " + dataset_id)
    return list({entry["id"]: entry for entry in selected}.values())


def list_catalog(catalog: dict[str, Any]) -> list[str]:
    lines = []
    for entry in catalog["datasets"]:
        lines.append(f"{entry['id']} [release/{entry['source']}]: {entry['summary']} -> {entry['filename']}")
    for entry in catalog.get("git_references", []):
        lines.append(f"{entry['id']} [git]: {entry['summary']} -> {entry['path']}")
    for entry in catalog.get("logical_datasets", []):
        lines.append(f"{entry['id']} [logical]: {entry['summary']} -> {', '.join(entry['assets'])}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--list", action="store_true", help="list available dataset IDs")
    parser.add_argument("--update", action="append", default=[], metavar="ID[,ID]",
                        help="dataset IDs to fetch; repeatable")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release-data")
    args = parser.parse_args()
    catalog = load_catalog_document(args.config)
    entries = catalog["datasets"]
    if args.list:
        for line in list_catalog(catalog):
            print(line)
        return
    ids = parse_ids(args.update)
    if not ids:
        parser.error("--update is required unless --list is used")
    selected = resolve_release_entries(catalog, ids)
    manual_ids = [entry["id"] for entry in selected if entry["source"] == MANUAL_SOURCE]
    if manual_ids:
        raise SystemExit(
            "manual dataset ID(s) cannot be fetched from CDS: " + ", ".join(manual_ids)
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import cdsapi
    except ImportError as error:
        raise SystemExit('cdsapi is required; run: uv pip install "cdsapi>=0.7.7"') from error
    client = cdsapi.Client()
    for entry in selected:
        target = args.output_dir / entry["filename"]
        print(f"Downloading {entry['id']} to {target}")
        client.retrieve(entry["dataset"], entry["request"], str(target))


if __name__ == "__main__":
    main()
