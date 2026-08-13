from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import frozen_bootstrap  # noqa: E402


class FrozenUpgradeTests(unittest.TestCase):
    def test_existing_install_gets_code_update_but_keeps_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            seed = bundle / "project_seed"
            project = root / "installed" / "project"
            (seed / "scripts").mkdir(parents=True)
            (seed / "secrets").mkdir()
            (seed / "scripts" / "feature.py").write_text("new", encoding="utf-8")
            (seed / ".env").write_text("BUNDLED=1", encoding="utf-8")
            (seed / "secrets" / "key.json").write_text("bundled", encoding="utf-8")

            (project / "scripts").mkdir(parents=True)
            (project / "secrets").mkdir()
            (project / "scripts" / "feature.py").write_text("old", encoding="utf-8")
            (project / ".env").write_text("USER=1", encoding="utf-8")
            (project / "secrets" / "key.json").write_text("user", encoding="utf-8")

            old_project = frozen_bootstrap.PROJECT_ROOT
            old_bundle_root = frozen_bootstrap._bundle_root
            try:
                frozen_bootstrap.PROJECT_ROOT = project
                frozen_bootstrap._bundle_root = lambda: bundle
                frozen_bootstrap._install_project_if_needed()

                self.assertEqual(
                    (project / "scripts" / "feature.py").read_text(encoding="utf-8"),
                    "new")
                self.assertEqual((project / ".env").read_text(encoding="utf-8"),
                                 "USER=1")
                self.assertEqual(
                    (project / "secrets" / "key.json").read_text(encoding="utf-8"),
                    "user")
                self.assertEqual(
                    (project / frozen_bootstrap.VERSION_MARKER).read_text(
                        encoding="utf-8").strip(),
                    frozen_bootstrap.BUNDLED_PROJECT_VERSION)

                # Innerhalb derselben DMG-Version duerfen spaetere Git-Updates
                # nicht bei jedem Start wieder vom Seed ueberschrieben werden.
                (project / "scripts" / "feature.py").write_text("git-update",
                                                                  encoding="utf-8")
                frozen_bootstrap._install_project_if_needed()
                self.assertEqual(
                    (project / "scripts" / "feature.py").read_text(encoding="utf-8"),
                    "git-update")
            finally:
                frozen_bootstrap.PROJECT_ROOT = old_project
                frozen_bootstrap._bundle_root = old_bundle_root


if __name__ == "__main__":
    unittest.main()
