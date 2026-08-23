from __future__ import annotations

import json
import sys
import tarfile
from types import SimpleNamespace
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_era5 import (  # noqa: E402
    list_catalog,
    load_catalog,
    load_catalog_document,
    resolve_release_entries,
    select_entries,
)
import release_data  # noqa: E402
from release_data import classify, next_tag, plan, stage_manual_assets, write_checksums  # noqa: E402


CATALOG = Path(__file__).resolve().parents[1] / "data" / "era5_requests.json"


def test_catalog_is_valid_json() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 2
    assert isinstance(catalog["datasets"], list)


@pytest.fixture(scope="module")
def entries() -> list[dict[str, object]]:
    return load_catalog(CATALOG)


@pytest.fixture(scope="module")
def catalog_document() -> dict[str, object]:
    return load_catalog_document(CATALOG)


def test_catalog_has_unique_ids_and_filenames(entries: list[dict[str, object]]) -> None:
    assert len(entries) == 36
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert len({entry["filename"] for entry in entries}) == len(entries)


def test_git_references_exist_and_have_provenance(catalog_document: dict[str, object]) -> None:
    root = CATALOG.parents[1]
    references = catalog_document["git_references"]
    assert references
    assert len({reference["id"] for reference in references}) == len(references)
    for reference in references:
        assert (root / reference["path"]).is_file()
        assert reference["provenance"]


def test_logical_f320_datasets_are_ordered_monthly_release_assets(
    catalog_document: dict[str, object],
) -> None:
    logical = {entry["id"]: entry for entry in catalog_document["logical_datasets"]}
    for logical_id, variable in (("era5-msl-2024-f320", "msl"), ("era5-vo850-2024-f320", "vo850")):
        assets = logical[logical_id]["assets"]
        assert assets == [f"era5-{variable}-2024-f320-{month:02d}" for month in range(1, 13)]


def test_logical_release_id_resolves_to_its_twelve_assets(catalog_document: dict[str, object]) -> None:
    resolved = resolve_release_entries(catalog_document, ["era5-msl-2024-f320"])
    assert [entry["filename"] for entry in resolved] == [
        f"era5_msl_2024-{month:02d}_f320.nc" for month in range(1, 13)
    ]


def test_list_includes_physical_git_and_logical_entries(catalog_document: dict[str, object]) -> None:
    lines = list_catalog(catalog_document)
    assert any(line.startswith("era5-msl-2024-f320-01 [release/manual]") for line in lines)
    assert any(line.startswith("pystormtracker-v0.5.0.dev-msl-imilast [git]") for line in lines)
    assert any(line.startswith("era5-msl-2024-f320 [logical]") for line in lines)


def test_n320_entries_use_complete_era5_grib_requests(entries: list[dict[str, object]]) -> None:
    n320_entries = [entry for entry in entries if "-n320-" in entry["id"]]
    assert {entry["id"] for entry in n320_entries} == {
        "msl-n320-grib",
        "vo850-n320-grib",
    }
    assert all(entry["source"] == "cds" for entry in n320_entries)
    assert all(entry["dataset"] == "reanalysis-era5-complete" for entry in n320_entries)
    assert all(entry["request"]["grid"] == "N320" for entry in n320_entries)


def test_manual_zarr_entry_is_archived_from_its_configured_source(
    entries: list[dict[str, object]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = next(entry for entry in entries if entry["id"] == "msl-25-zarr")
    store = tmp_path / entry["source_path"]
    store.mkdir()
    (store / ".zgroup").write_text('{"zarr_format": 2}\n', encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setattr(release_data, "ROOT", tmp_path)

    stage_manual_assets(stage, [entry])

    archive = stage / entry["filename"]
    with tarfile.open(archive, "r:gz") as contents:
        assert f"{store.name}/.zgroup" in contents.getnames()


def test_manual_f320_asset_is_staged_and_checksums_are_written(
    entries: list[dict[str, object]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = next(entry for entry in entries if entry["id"] == "era5-msl-2024-f320-01")
    source = tmp_path / entry["source_path"]
    source.parent.mkdir(parents=True)
    source.write_bytes(b"small NetCDF stand-in")
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setattr(release_data, "ROOT", tmp_path)

    stage_manual_assets(stage, [entry])
    manifest = write_checksums(stage)

    assert (stage / entry["filename"]).read_bytes() == b"small NetCDF stand-in"
    assert entry["filename"] in manifest.read_text(encoding="utf-8")


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


def test_explicit_next_tag_overrides_the_patch_default(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([
        '[{"tag_name":"v0.1.4-data","draft":false}]',
        '{"assets":[]}',
    ])
    monkeypatch.setattr("release_data.output", lambda *command: next(responses))

    build_plan = plan("owner/repo", CATALOG, [], "v0.2.0-data")

    assert build_plan["next_tag"] == "v0.2.0-data"


def test_release_requires_explicit_staging_review_confirmation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--confirm-reviewed"):
        release_data.release(SimpleNamespace(stage=tmp_path, confirm_reviewed=False))


def test_reviewed_minor_release_uses_the_staged_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / release_data.STATE_NAME).write_text(
        json.dumps(
            {
                "base_tag": "v0.1.4-data",
                "next_tag": "v0.2.0-data",
                "note": "Data update: verified F320 assets.",
            }
        ),
        encoding="utf-8",
    )
    (stage / "SHA256SUMS").write_text("checksum  asset.nc\n", encoding="utf-8")
    monkeypatch.setattr(release_data, "latest_release", lambda repo: {"tag_name": "v0.1.4-data"})
    monkeypatch.setattr(release_data, "write_checksums", lambda stage: stage / "SHA256SUMS")
    monkeypatch.setattr(release_data.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(release_data, "run", lambda *command, **kwargs: commands.append(command))

    release_data.release(SimpleNamespace(stage=stage, repo="owner/repo", confirm_reviewed=True))

    assert commands[0] == ("git", "tag", "-a", "v0.2.0-data", "-m", "Data update: verified F320 assets.")


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


def test_dry_run_logical_update_expands_without_downloading(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited_assets = json.dumps(
        {"assets": [{"name": entry["filename"]} for entry in load_catalog(CATALOG)]}
    )
    responses = iter([
        '[{"tag_name":"v0.1.3-data","draft":false}]',
        inherited_assets,
    ])
    monkeypatch.setattr("release_data.output", lambda *command: next(responses))

    build_plan = plan("owner/repo", CATALOG, ["era5-vo850-2024-f320"])

    assert build_plan["download_ids"][-12:] == [
        f"era5-vo850-2024-f320-{month:02d}" for month in range(1, 13)
    ]
