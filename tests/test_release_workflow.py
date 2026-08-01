from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch_era5 import load_catalog, select_entries  # noqa: E402
from release_data import classify, next_tag, plan  # noqa: E402


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = load_catalog(Path(__file__).resolve().parents[1] / "data" / "era5_requests.json")

    def test_catalog_has_unique_ids_and_filenames(self) -> None:
        self.assertEqual(len(self.entries), 8)
        self.assertEqual(len({entry["id"] for entry in self.entries}), len(self.entries))
        self.assertEqual(len({entry["filename"] for entry in self.entries}), len(self.entries))

    def test_select_entries_rejects_unknown_id(self) -> None:
        with self.assertRaises(SystemExit):
            select_entries(self.entries, ["not-a-dataset"])

    def test_new_catalog_entry_is_downloaded_without_update(self) -> None:
        inherited_ids, new_ids, download_ids = classify(self.entries, set(), [])
        self.assertEqual(inherited_ids, [])
        self.assertEqual(new_ids, [entry["id"] for entry in self.entries])
        self.assertEqual(download_ids, new_ids)

    def test_explicit_update_regenerates_an_inherited_entry(self) -> None:
        inherited = {entry["filename"] for entry in self.entries}
        inherited_ids, new_ids, download_ids = classify(self.entries, inherited, ["uv850-025-netcdf"])
        self.assertIn("msl-025-netcdf", inherited_ids)
        self.assertEqual(new_ids, [])
        self.assertEqual(download_ids, ["uv850-025-netcdf"])

    def test_patch_tag_increment(self) -> None:
        self.assertEqual(next_tag("v0.1.3-data"), "v0.1.4-data")
        with self.assertRaises(ValueError):
            next_tag("v0.1-data")

    @patch("release_data.output")
    def test_dry_run_plan_uses_latest_release_without_downloading(self, mock_output) -> None:
        mock_output.side_effect = [
            '[{"tag_name":"v0.1.3-data","draft":false}]',
            '{"assets":[{"name":"era5_msl_2025-2026_djf_0.25x0.25.nc"}]}',
        ]
        config = Path(__file__).resolve().parents[1] / "data" / "era5_requests.json"
        build_plan = plan("owner/repo", config, [])
        self.assertEqual(build_plan["base_tag"], "v0.1.3-data")
        self.assertEqual(build_plan["next_tag"], "v0.1.4-data")
        self.assertIn("msl-25-netcdf", build_plan["new_ids"])
        self.assertEqual(mock_output.call_count, 2)


if __name__ == "__main__":
    unittest.main()
