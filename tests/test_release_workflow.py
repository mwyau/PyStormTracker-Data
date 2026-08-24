from __future__ import annotations

import hashlib
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
    list_catalog,
    load_catalog,
    select_entries,
)
from release_data import (
    classify,
    next_tag,
    parse_checksums,
    parse_release_digest,
    plan,
    write_checksums,
)

CATALOG = Path(__file__).resolve().parents[1] / "data" / "era5_requests.json"
ROOT = CATALOG.parents[1]


@pytest.fixture(autouse=True)
def clean_tracked_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep release tests independent of the checkout's test-file edits."""
    real_output = release_data.output

    def output(*command: str, cwd: Path | None = None) -> str:
        if command[:2] == ("git", "status"):
            return ""
        return real_output(*command, cwd=cwd)

    monkeypatch.setattr(release_data, "output", output)


@pytest.fixture(scope="module")
def entries() -> list[dict[str, Any]]:
    return load_catalog(CATALOG)


def test_important_git_files_and_zarr_stores_exist() -> None:
    expected_files = [
        ROOT / "parity/ncl/README.md",
        ROOT / "parity/legacy/v0.0.2/era5_msl_2025-2026_djf_2.5x2.5_imilast.txt",
        ROOT / "parity/legacy/v0.5.0/era5_msl_2025-2026_djf_2.5x2.5.trackjson",
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


def test_release_digest_parser_accepts_sha256_metadata() -> None:
    assert parse_release_digest("sha256:" + "A" * 64, "asset.nc") == "a" * 64


@pytest.mark.parametrize("value", [None, "md5:" + "a" * 32, "sha256:not-a-digest"])
def test_release_digest_parser_rejects_missing_or_invalid_metadata(
    value: Any,
) -> None:
    with pytest.raises(SystemExit, match="no usable SHA-256 digest"):
        parse_release_digest(value, "asset.nc")


def test_release_assets_preserves_api_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    monkeypatch.setattr(
        release_data,
        "output",
        lambda *command: json.dumps(
            {"assets": [{"name": "asset.nc", "digest": digest}]}
        ),
    )

    assert release_data.release_assets("owner/repo", "v0.1.0-data") == {
        "asset.nc": digest
    }


def _minimal_config(tmp_path: Path, entry_ids: list[str]) -> Path:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    document["datasets"] = [
        entry for entry in document["datasets"] if entry["id"] in entry_ids
    ]
    path = tmp_path / "era5_requests.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _download_args(
    stage: Path,
    config: Path,
    update: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        repo="owner/repo",
        config=config,
        stage=stage,
        update=[] if update is None else update,
        next_tag="v0.2.0-data",
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _release_digests(assets: dict[str, bytes]) -> dict[str, str]:
    return {
        filename: f"sha256:{_digest(data)}"
        for filename, data in assets.items()
    }


def _fake_gh_download(
    base_assets: dict[str, bytes],
    calls: list[tuple[str, ...]],
    acquired: dict[str, bytes] | None = None,
):
    def fake_run(*command: str, cwd: Path | None = None) -> None:
        calls.append(command)
        if command[0] != "gh":
            if acquired is not None and any("fetch_era5.py" in part for part in command):
                output_dir = Path(command[command.index("--output-dir") + 1])
                for filename, data in acquired.items():
                    (output_dir / filename).write_bytes(data)
            return
        directory = Path(command[command.index("--dir") + 1])
        pattern = command[command.index("--pattern") + 1]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / pattern).write_bytes(base_assets[pattern])

    return fake_run


def _mock_base(
    monkeypatch: pytest.MonkeyPatch,
    assets: dict[str, Any],
    tag: str = "legacy-2025",
) -> None:
    monkeypatch.setattr(
        release_data,
        "latest_release",
        lambda repo: {"tag_name": tag, "published_at": "2025-01-01T00:00:00Z"},
    )
    monkeypatch.setattr(release_data, "release_assets", lambda repo, tag: assets)


def test_latest_published_release_is_not_filtered_by_data_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [
        {
            "tagName": "v0.1.4-data",
            "isDraft": False,
            "publishedAt": "2025-01-01T00:00:00Z",
        },
        {
            "tagName": "release-2025-djf",
            "isDraft": False,
            "publishedAt": "2025-02-01T00:00:00Z",
        },
        {
            "tagName": "draft-data",
            "isDraft": True,
            "publishedAt": None,
        },
    ]
    monkeypatch.setattr(
        release_data, "output", lambda *command: json.dumps(releases)
    )

    assert release_data.latest_release("owner/repo")["tag_name"] == "release-2025-djf"


def test_plan_accepts_legacy_base_with_explicit_data_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited = {
        entry["filename"]: "sha256:" + "a" * 64
        for entry in load_catalog(CATALOG)
    }
    _mock_base(monkeypatch, inherited)

    build_plan = plan("owner/repo", CATALOG, [], "v0.2.0-data")

    assert build_plan["base_tag"] == "legacy-2025"
    assert build_plan["next_tag"] == "v0.2.0-data"
    assert len(build_plan["inherited_ids"]) == len(inherited)
    assert build_plan["new_ids"] == []


def test_plan_requires_data_target_for_legacy_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_base(monkeypatch, {})

    with pytest.raises(SystemExit, match="legacy tag"):
        plan("owner/repo", CATALOG, [])


def test_matching_inherited_asset_is_reused_without_asset_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _minimal_config(tmp_path, ["msl-25-netcdf"])
    filename = "era5_msl_2025-2026_djf_2.5x2.5.nc"
    content = b"inherited"
    stage = tmp_path / "release-data"
    stage.mkdir()
    (stage / filename).write_bytes(content)
    calls: list[tuple[str, ...]] = []
    _mock_base(monkeypatch, _release_digests({filename: content}))
    monkeypatch.setattr(
        release_data,
        "run",
        _fake_gh_download({filename: content}, calls),
    )

    release_data.download(_download_args(stage, config))

    patterns = [
        command[command.index("--pattern") + 1]
        for command in calls
        if command[0] == "gh"
    ]
    assert patterns == []
    assert (stage / filename).read_bytes() == content
    state = json.loads((stage / release_data.STATE_NAME).read_text())
    assert state["reused_assets"] == [filename]
    assert state["source_commit"] == release_data.output(
        "git", "rev-parse", "HEAD", cwd=release_data.ROOT
    ).strip()


@pytest.mark.parametrize("initial", [b"wrong", None])
def test_mismatched_or_missing_inherited_asset_is_replaced(
    initial: bytes | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _minimal_config(tmp_path, ["msl-25-netcdf"])
    filename = "era5_msl_2025-2026_djf_2.5x2.5.nc"
    content = b"base-release"
    stage = tmp_path / "release-data"
    stage.mkdir()
    if initial is not None:
        (stage / filename).write_bytes(initial)
    calls: list[tuple[str, ...]] = []
    _mock_base(monkeypatch, _release_digests({filename: content}))
    monkeypatch.setattr(
        release_data,
        "run",
        _fake_gh_download({filename: content}, calls),
    )

    release_data.download(_download_args(stage, config))

    patterns = [
        command[command.index("--pattern") + 1]
        for command in calls
        if command[0] == "gh"
    ]
    assert patterns == [filename]
    assert (stage / filename).read_bytes() == content


@pytest.mark.parametrize("digest", [None, "md5:" + "a" * 32, "sha256:not-a-digest"])
def test_inherited_asset_requires_usable_release_digest(
    digest: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _minimal_config(tmp_path, ["msl-25-netcdf"])
    filename = "era5_msl_2025-2026_djf_2.5x2.5.nc"
    stage = tmp_path / "release-data"
    stage.mkdir()
    calls: list[tuple[str, ...]] = []
    _mock_base(monkeypatch, {filename: digest})
    monkeypatch.setattr(release_data, "run", _fake_gh_download({}, calls))

    with pytest.raises(SystemExit, match="no usable SHA-256 digest"):
        release_data.download(_download_args(stage, config))
    assert calls == []


def test_explicit_update_replaces_valid_inherited_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _minimal_config(tmp_path, ["msl-25-netcdf"])
    filename = "era5_msl_2025-2026_djf_2.5x2.5.nc"
    stage = tmp_path / "release-data"
    stage.mkdir()
    (stage / filename).write_bytes(b"old")
    calls: list[tuple[str, ...]] = []
    _mock_base(monkeypatch, _release_digests({filename: b"base"}))
    monkeypatch.setattr(
        release_data,
        "run",
        _fake_gh_download(
            {filename: b"base"}, calls, acquired={filename: b"refreshed"}
        ),
    )

    release_data.download(_download_args(stage, config, ["msl-25-netcdf"]))

    patterns = [
        command[command.index("--pattern") + 1]
        for command in calls
        if command[0] == "gh"
    ]
    assert patterns == []
    assert (stage / filename).read_bytes() == b"refreshed"
    assert any(
        any("fetch_era5.py" in part for part in command) for command in calls
    )


def test_prior_staging_checksum_reuses_new_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _minimal_config(tmp_path, ["msl-25-netcdf"])
    filename = "era5_msl_2025-2026_djf_2.5x2.5.nc"
    content = b"prior-stage"
    stage = tmp_path / "release-data"
    stage.mkdir()
    (stage / filename).write_bytes(content)
    (stage / "SHA256SUMS").write_text(
        f"{_digest(content)}  {filename}\n", encoding="utf-8"
    )
    calls: list[tuple[str, ...]] = []
    _mock_base(monkeypatch, {"SHA256SUMS": None})
    monkeypatch.setattr(release_data, "run", _fake_gh_download({}, calls))

    release_data.download(_download_args(stage, config))

    assert calls == []
    assert (stage / filename).read_bytes() == content


def test_sha256sums_parser_accepts_binary_mode_entries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "SHA256SUMS"
    path.write_text("a" * 64 + "  *asset.nc\n", encoding="utf-8")

    assert parse_checksums(path) == {"asset.nc": "a" * 64}


def test_final_checksums_are_complete_and_deterministic(tmp_path: Path) -> None:
    (tmp_path / "z.nc").write_bytes(b"z")
    (tmp_path / "a.grib").write_bytes(b"a")
    (tmp_path / "extra.nc").write_bytes(b"extra")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")

    first = write_checksums(tmp_path).read_text(encoding="utf-8")
    second = write_checksums(tmp_path).read_text(encoding="utf-8")

    assert first == second
    assert [line.split(maxsplit=1)[1] for line in first.splitlines()] == [
        "a.grib",
        "extra.nc",
        "z.nc",
    ]
    assert len(parse_checksums(tmp_path / "SHA256SUMS")) == 3


def _release_args(stage: Path) -> SimpleNamespace:
    return SimpleNamespace(stage=stage, repo="owner/repo")


def _write_release_state(stage: Path, required: list[str], source_commit: str) -> None:
    (stage / release_data.STATE_NAME).write_text(
        json.dumps(
            {
                "base_tag": None,
                "next_tag": "v0.2.0-data",
                "note": "update",
                "source_commit": source_commit,
                "required_assets": required,
            }
        ),
        encoding="utf-8",
    )


def test_release_rejects_changed_staged_asset_before_github_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "release-data"
    stage.mkdir()
    asset = stage / "asset.nc"
    asset.write_bytes(b"prepared")
    write_checksums(stage)
    manifest_before = (stage / "SHA256SUMS").read_text(encoding="utf-8")
    _write_release_state(
        stage,
        [asset.name],
        release_data.output("git", "rev-parse", "HEAD", cwd=release_data.ROOT).strip(),
    )
    asset.write_bytes(b"changed")
    monkeypatch.setattr(
        release_data,
        "latest_release",
        lambda repo: pytest.fail("GitHub release discovery should not run"),
    )

    with pytest.raises(SystemExit, match="staged assets changed after preparation"):
        release_data.release(_release_args(stage))
    assert (stage / "SHA256SUMS").read_text(encoding="utf-8") == manifest_before


def test_release_rejects_unexpected_staged_data_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "release-data"
    stage.mkdir()
    asset = stage / "asset.nc"
    asset.write_bytes(b"prepared")
    write_checksums(stage)
    _write_release_state(
        stage,
        [asset.name],
        release_data.output("git", "rev-parse", "HEAD", cwd=release_data.ROOT).strip(),
    )
    (stage / "old-experiment.nc").write_bytes(b"stale")
    monkeypatch.setattr(
        release_data,
        "latest_release",
        lambda repo: pytest.fail("GitHub release discovery should not run"),
    )

    with pytest.raises(SystemExit, match="unexpected staged release assets"):
        release_data.release(_release_args(stage))


def test_release_uploads_exact_prepared_assets_to_prepared_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "release-data"
    stage.mkdir()
    asset = stage / "asset.nc"
    asset.write_bytes(b"prepared")
    write_checksums(stage)
    source_commit = release_data.output(
        "git", "rev-parse", "HEAD", cwd=release_data.ROOT
    ).strip()
    _write_release_state(stage, [asset.name], source_commit)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(release_data, "latest_release", lambda repo: None)

    def fake_run(*command: str, cwd: Path | None = None) -> None:
        calls.append(command)

    monkeypatch.setattr(release_data, "run", fake_run)

    release_data.release(_release_args(stage))

    assert calls == [
        (
            "gh",
            "release",
            "create",
            "v0.2.0-data",
            str(asset),
            str(stage / "SHA256SUMS"),
            "--repo",
            "owner/repo",
            "--target",
            source_commit,
            "--title",
            "v0.2.0-data",
            "--notes",
            "update",
        )
    ]


def test_download_requires_clean_tracked_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_output = release_data.output

    def dirty_output(*command: str, cwd: Path | None = None) -> str:
        if command[:2] == ("git", "status"):
            return " M README.md\n"
        return real_output(*command, cwd=cwd)

    monkeypatch.setattr(release_data, "output", dirty_output)

    with pytest.raises(SystemExit, match="tracked Git worktree is not clean"):
        release_data.download(_download_args(tmp_path / "stage", CATALOG))


def test_release_rejects_different_prepared_source_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "release-data"
    stage.mkdir()
    asset = stage / "asset.nc"
    asset.write_bytes(b"prepared")
    write_checksums(stage)
    _write_release_state(stage, [asset.name], "0" * 40)
    monkeypatch.setattr(
        release_data,
        "latest_release",
        lambda repo: pytest.fail("GitHub release discovery should not run"),
    )

    with pytest.raises(SystemExit, match="prepared source commit"):
        release_data.release(_release_args(stage))


def test_release_rejects_stale_base_after_another_release_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "release-data"
    stage.mkdir()
    asset = stage / "asset.nc"
    asset.write_bytes(b"asset")
    write_checksums(stage)
    (stage / release_data.STATE_NAME).write_text(
        json.dumps(
            {
                "base_tag": "legacy-2025",
                "next_tag": "v0.2.0-data",
                "note": "update",
                "required_assets": [asset.name],
                "source_commit": release_data.output(
                    "git", "rev-parse", "HEAD", cwd=release_data.ROOT
                ).strip(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release_data,
        "latest_release",
        lambda repo: {"tag_name": "v0.2.0-data", "published_at": "2025-03-01"},
    )

    with pytest.raises(SystemExit, match="stale"):
        release_data.release(
            SimpleNamespace(
                stage=stage,
                repo="owner/repo",
            )
        )


def test_semantic_ordering_is_enforced_after_data_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_base(monkeypatch, {}, tag="v0.2.0-data")

    with pytest.raises(SystemExit, match="newer"):
        plan("owner/repo", CATALOG, [], "v0.2.0-data")

    with pytest.raises(SystemExit, match="newer"):
        plan("owner/repo", CATALOG, [], "v0.1.9-data")


def test_target_tag_must_use_data_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("release_data.latest_release", lambda repo: None)

    with pytest.raises(SystemExit, match="not a data tag"):
        plan("owner/repo", CATALOG, [], "release-2025")
