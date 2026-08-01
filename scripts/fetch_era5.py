#!/usr/bin/env python3
"""Fetch selected ERA5 requests from the repository catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "data" / "era5_requests.json"
CATALOG_SCHEMA_VERSION = 1
CDS_SOURCE = "cds"
MANUAL_SOURCE = "manual"


def load_catalog(path: Path) -> list[dict[str, Any]]:
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
    return entries


def parse_ids(values: list[str]) -> list[str]:
    return [item.strip() for value in values for item in value.split(",") if item.strip()]


def select_entries(entries: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    by_id = {entry["id"]: entry for entry in entries}
    unknown = sorted(set(ids) - by_id.keys())
    if unknown:
        raise SystemExit("unknown dataset ID(s): " + ", ".join(unknown))
    return [by_id[dataset_id] for dataset_id in ids]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--list", action="store_true", help="list available dataset IDs")
    parser.add_argument("--update", action="append", default=[], metavar="ID[,ID]",
                        help="dataset IDs to fetch; repeatable")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release-data")
    args = parser.parse_args()
    entries = load_catalog(args.config)
    if args.list:
        for entry in entries:
            print(f"{entry['id']} [{entry['source']}]: {entry['summary']} -> {entry['filename']}")
        return
    ids = parse_ids(args.update)
    if not ids:
        parser.error("--update is required unless --list is used")
    selected = select_entries(entries, ids)
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
