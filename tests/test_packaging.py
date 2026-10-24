import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _set_version(*args):
    return subprocess.run(
        [sys.executable, "packaging/set_version.py", *args],
        capture_output=True, text=True, cwd=ROOT,
    )


class SetVersionTest(unittest.TestCase):
    def test_set_version_rewrites_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            init = Path(tmp) / "__init__.py"
            shutil.copy(ROOT / "speedreader" / "__init__.py", init)

            result = _set_version("1.2.3-rc1", str(init))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "deb=1.2.3~rc1 pep440=1.2.3rc1 prerelease=true\n")
            self.assertIn('__version__ = "1.2.3rc1"\n', init.read_text(encoding="utf-8"))

            result = _set_version("v1.2.3", str(init))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "deb=1.2.3 pep440=1.2.3 prerelease=false\n")
            self.assertEqual(init.read_text(encoding="utf-8").count("__version__"), 1)
            self.assertIn('__version__ = "1.2.3"\n', init.read_text(encoding="utf-8"))

            result = _set_version("0.0.0.dev17", str(init))
            self.assertEqual(result.stdout, "deb=0.0.0~dev17 pep440=0.0.0.dev17 prerelease=true\n")

            result = _set_version("banana", str(init))
            self.assertEqual(result.returncode, 2)
            self.assertTrue(result.stderr)
            self.assertIn('__version__ = "0.0.0.dev17"\n', init.read_text(encoding="utf-8"))


class PackagingFilesTest(unittest.TestCase):
    def test_workflow_yaml_parses(self):
        text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: windows-latest", text)
        self.assertIn("runs-on: ubuntu-22.04", text)
        self.assertIn("\non:\n", "\n" + text)
        self.assertIn("tags:", text)
        self.assertIn("workflow_dispatch", text)

    def test_desktop_file_has_required_keys(self):
        lines = (ROOT / "packaging" / "speedreader.desktop").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "[Desktop Entry]")
        for key in ("Exec=/usr/bin/speedreader", "Icon=speedreader", "Type=Application", "Name=SpeedReader"):
            self.assertIn(key, lines)
