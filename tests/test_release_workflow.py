from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_era5 import load_catalog, select_entries  # noqa: E402
from release_data import classify, next_tag, plan  # noqa: E402


CATALOG = Path(__file__).resolve().parents[1] / "data" / "era5_requests.json"


@pytest.fixture(scope="module")
def entries() -> list[dict[str, object]]:
    return load_catalog(CATALOG)


def test_catalog_has_unique_ids_and_filenames(entries: list[dict[str, object]]) -> None:
    assert len(entries) == 8
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert len({entry["filename"] for entry in entries}) == len(entries)


def test_select_entries_rejects_unknown_id(entries: list[dict[str, object]]) -> None:
    with pytest.raises(SystemExit, match="unknown dataset ID"):
        select_entries(entries, ["not-a-dataset"])


def test_new_catalog_entry_is_downloaded_without_update(entries: list[dict[str, object]]) -> None:
    inherited_ids, new_ids, download_ids = classify(entries, set(), [])
    assert inherited_ids == []
    assert new_ids == [entry["id"] for entry in entries]
    assert download_ids == new_ids


def test_explicit_update_regenerates_an_inherited_entry(entries: list[dict[str, object]]) -> None:
    inherited = {entry["filename"] for entry in entries}
    inherited_ids, new_ids, download_ids = classify(entries, inherited, ["uv850-025-netcdf"])
    assert "msl-025-netcdf" in inherited_ids
    assert new_ids == []
    assert download_ids == ["uv850-025-netcdf"]


def test_patch_tag_increment() -> None:
    assert next_tag("v0.1.3-data") == "v0.1.4-data"
    with pytest.raises(ValueError, match="not a data tag"):
        next_tag("v0.1-data")


def test_dry_run_plan_uses_latest_release_without_downloading(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([
        '[{"tag_name":"v0.1.3-data","draft":false}]',
        '{"assets":[{"name":"era5_msl_2025-2026_djf_0.25x0.25.nc"}]}',
    ])
    monkeypatch.setattr("release_data.output", lambda *command: next(responses))
    build_plan = plan("owner/repo", CATALOG, [])
    assert build_plan["base_tag"] == "v0.1.3-data"
    assert build_plan["next_tag"] == "v0.1.4-data"
    assert "msl-25-netcdf" in build_plan["new_ids"]
