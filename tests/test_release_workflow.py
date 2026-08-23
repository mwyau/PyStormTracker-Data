from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import release_data
from fetch_era5 import (
    canonical_f320_filename,
    list_catalog,
    load_catalog,
    select_entries,
)
from release_data import classify, next_tag, plan, write_checksums

CATALOG = Path(__file__).resolve().parents[1] / "data" / "era5_requests.json"
ROOT = CATALOG.parents[1]


@pytest.fixture(scope="module")
def entries() -> list[dict[str, Any]]:
    return load_catalog(CATALOG)


def test_catalog_describes_only_physical_era5_entries(
    entries: list[dict[str, Any]],
) -> None:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))

    assert document["schema_version"] == 1
    assert len(entries) == 34
    assert all(entry["source"] == "cds" for entry in entries)
    assert set(document) == {"schema_version", "description", "datasets"}
    assert all("source_path" not in entry for entry in entries)


def test_catalog_has_unique_ids_and_filenames(entries: list[dict[str, Any]]) -> None:
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert len({entry["filename"] for entry in entries}) == len(entries)
    assert all(Path(entry["filename"]).name == entry["filename"] for entry in entries)


def test_f320_entries_are_explicit_monthly_requests(
    entries: list[dict[str, Any]],
) -> None:
    f320 = [entry for entry in entries if entry.get("grid") == "F320"]
    assert len(f320) == 24

    for product, variable in (("msl", "msl"), ("vo850", "vo")):
        product_entries = sorted(
            (entry for entry in f320 if entry["variable"] == variable),
            key=lambda entry: entry["month"],
        )
        assert [entry["month"] for entry in product_entries] == list(range(1, 13))
        assert [entry["filename"] for entry in product_entries] == [
            canonical_f320_filename(product, month) for month in range(1, 13)
        ]
        assert all(
            entry["dataset"] == "reanalysis-era5-complete" for entry in product_entries
        )
        assert all(entry["request"]["grid"] == "F320" for entry in product_entries)
        assert all(
            entry["request"]["time"] == "00/06/12/18" for entry in product_entries
        )

    vo_entry = next(entry for entry in f320 if entry["variable"] == "vo")
    assert vo_entry["level"] == 850
    assert vo_entry["request"]["levelist"] == "850"
    assert vo_entry["request"]["param"] == "138.128"


def test_important_git_files_and_zarr_stores_exist() -> None:
    expected_files = [
        ROOT / "parity/ncl/README.md",
        ROOT / "parity/ncl/era5_msl_2025-12-01_0000_0.25x0.25.nc",
        ROOT / "parity/legacy/v0.0.2/era5_msl_2025-2026_djf_2.5x2.5_imilast.txt",
        ROOT / "parity/legacy/v0.5.0.dev/era5_msl_2025-2026_djf_2.5x2.5.trackjson",
        ROOT / "parity/track/1.5.4/era5_msl_2024-01_f320-t42_final-positive.txt",
    ]
    assert all(path.is_file() for path in expected_files)
    assert not (ROOT / "reference").exists()

    for store in sorted((ROOT / "integration").glob("*.zarr")):
        assert (store / ".zgroup").is_file()
        assert (store / ".zmetadata").is_file()
        assert any(
            path.is_file()
            for path in store.rglob("*")
            if path.name not in {".zgroup", ".zmetadata", ".zattrs"}
        )


def test_catalog_listing_contains_physical_entries_only(
    entries: list[dict[str, Any]],
) -> None:
    lines = list_catalog(entries)
    assert any(line.startswith("msl-f320-2024-01 [cds]") for line in lines)
    assert not any("[logical]" in line or "[git]" in line for line in lines)


def test_select_entries_rejects_unknown_id(entries: list[dict[str, Any]]) -> None:
    with pytest.raises(SystemExit, match="unknown dataset ID"):
        select_entries(entries, ["not-a-dataset"])


def test_classify_inherits_by_physical_filename(entries: list[dict[str, Any]]) -> None:
    inherited = {entry["filename"] for entry in entries}
    inherited_ids, new_ids, download_ids = classify(
        entries, inherited, ["msl-f320-2024-01"]
    )

    assert new_ids == []
    assert download_ids == ["msl-f320-2024-01"]
    assert "msl-025-netcdf" in inherited_ids


def test_write_checksums_covers_release_assets(tmp_path: Path) -> None:
    (tmp_path / "a.nc").write_bytes(b"NetCDF")
    (tmp_path / "b.grib").write_bytes(b"GRIB")
    (tmp_path / "ignored.txt").write_text("not an asset", encoding="utf-8")

    manifest = write_checksums(tmp_path)
    lines = manifest.read_text(encoding="utf-8").splitlines()

    assert [line.split(maxsplit=1)[1] for line in lines] == ["a.nc", "b.grib"]


def test_initial_release_plan_has_no_fake_base_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("release_data.latest_release", lambda repo: None)

    build_plan = plan("owner/repo", CATALOG, [], "v0.2.0-data")

    assert build_plan["base_tag"] is None
    assert build_plan["next_tag"] == "v0.2.0-data"
    assert len(build_plan["download_ids"]) == 34


def test_initial_release_requires_explicit_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("release_data.latest_release", lambda repo: None)

    with pytest.raises(SystemExit, match="--next-tag"):
        plan("owner/repo", CATALOG, [])


def test_explicit_update_regenerates_an_inherited_entry(
    entries: list[dict[str, Any]],
) -> None:
    inherited = {entry["filename"] for entry in entries}
    inherited_ids, new_ids, download_ids = classify(
        entries, inherited, ["uv850-025-netcdf"]
    )

    assert "msl-025-netcdf" in inherited_ids
    assert new_ids == []
    assert download_ids == ["uv850-025-netcdf"]


def test_patch_tag_increment() -> None:
    assert next_tag("v0.1.3-data") == "v0.1.4-data"
    with pytest.raises(ValueError, match="not a data tag"):
        next_tag("v0.1-data")


def test_release_requires_explicit_staging_review_confirmation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--confirm-reviewed"):
        release_data.release(SimpleNamespace(stage=tmp_path, confirm_reviewed=False))
