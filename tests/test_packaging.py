import configparser
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _deploy_scanner():
    try:
        import PySide6
    except ImportError:
        return None
    # pyside6-deploy imports its siblings (project_lib) as top-level modules.
    sys.path.append(str(Path(PySide6.__file__).parent / "scripts"))
    try:
        from deploy_lib.dependency_util import find_pyside_modules
    except ImportError:
        return None
    return find_pyside_modules


find_pyside_modules = _deploy_scanner()

_spec = importlib.util.spec_from_file_location("build_android", ROOT / "packaging" / "build_android.py")
ba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ba)


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

    def test_workflow_builds_android(self):
        text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("build-android:", text)
        self.assertIn("runs-on: ubuntu-24.04", text)
        self.assertIn("ndk;$ANDROID_NDK_VERSION", text)
        self.assertIn('ANDROID_NDK_VERSION: "28.2.13676358"', text)
        self.assertIn("packaging/android/debug.keystore", text)
        self.assertIn("*.apk", text)
        self.assertIn("needs: [build-windows, build-linux, build-android]", text)
        self.assertIn("io.github.koroffin.speedreader", text)
        self.assertIn("apksigner\" verify --print-certs out/*.apk", text)
        self.assertIn("DEBUG_CERT_SHA256: e88b05bc425cc06cf405a11a2d7ec3b03b375a9474a93f7b7dea1925e085f9c4", text)

    def test_desktop_file_has_required_keys(self):
        lines = (ROOT / "packaging" / "speedreader.desktop").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "[Desktop Entry]")
        for key in ("Exec=/usr/bin/speedreader", "Icon=speedreader", "Type=Application", "Name=SpeedReader"):
            self.assertIn(key, lines)


ACTIVITY = """public class PythonActivity extends QtActivity {
    long lastBackClick = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        Log.v(TAG, "About to do super onCreate");
        super.onCreate(savedInstanceState);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (SystemClock.elapsedRealtime() - lastBackClick > 2000) {
                lastBackClick = SystemClock.elapsedRealtime();
                Toast.makeText(this, "Click again to close the app", Toast.LENGTH_LONG).show();
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }
}
"""


class DeployScanTest(unittest.TestCase):
    @unittest.skipUnless(find_pyside_modules, "pyside6 deploy tools not installed")
    def test_deploy_scan_finds_only_the_bundled_modules(self):
        self.assertEqual(set(find_pyside_modules(ROOT / "speedreader")), {"Core", "Gui", "Widgets"})


class BuildAndroidTest(unittest.TestCase):
    def test_patch_activity_inserts_env_and_drops_key_handler(self):
        out = ba.patch_activity(ACTIVITY)
        self.assertEqual(out.count('setEnvironmentVariable("QT_ANDROID_DISABLE_ACCESSIBILITY", "1");'), 1)
        self.assertLess(out.index("QT_ANDROID_DISABLE_ACCESSIBILITY"), out.index("About to do super onCreate"))
        self.assertNotIn("lastBackClick", out)
        self.assertNotIn("onKeyDown", out)
        self.assertIn("super.onCreate(savedInstanceState);", out)

    def test_patch_activity_fails_loudly(self):
        with self.assertRaisesRegex(ba.PatchError, "anchor"):
            ba.patch_activity(ACTIVITY.replace("About to do super onCreate", "x"))
        with self.assertRaisesRegex(ba.PatchError, "key handler"):
            ba.patch_activity(ACTIVITY.replace("lastBackClick", "other"))

    def test_numeric_version(self):
        self.assertEqual(ba.numeric_version("1.2.3"), 1_020_301)
        self.assertEqual(ba.numeric_version("1.2.0rc1"), 1_020_001)
        self.assertEqual(ba.numeric_version("0.0.0.dev17"), 1)

    def test_edit_buildozer_spec(self):
        spec = (
            "[app]\ntitle = speedreader\npackage.name = speedreader\npackage.domain = org.test\n"
            "version.regex = x\nversion.filename = y\nrequirements = python3,shiboken6,PySide6\n"
            "android.archs = arm64-v8a,x86_64\np4a.commit = abc\n\n[buildozer]\nlog_level = 2\n"
        )
        out = ba.edit_buildozer_spec(spec, "1.2.3", "/p4a")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.optionxform = str
        cfg.read_string(out)
        app = cfg["app"]
        self.assertEqual(app["title"], "SpeedReader")
        self.assertEqual(app["package.domain"], "io.github.koroffin")
        self.assertEqual(app["version"], "1.2.3")
        self.assertNotIn("version.regex", app)
        self.assertNotIn("version.filename", app)
        self.assertEqual(app["android.numeric_version"], "1020301")
        self.assertEqual(app["requirements"], "python3,shiboken6,PySide6,certifi")
        self.assertEqual(app["android.permissions"], "android.permission.INTERNET")
        self.assertEqual(app["android.wakelock"], "True")
        self.assertEqual(app["android.archs"], "arm64-v8a")
        self.assertEqual(app["android.api"], "35")
        self.assertEqual(app["android.minapi"], "28")
        self.assertEqual(app["android.allow_backup"], "False")
        self.assertEqual(app["icon.filename"], "%(source.dir)s/speedreader/assets/icon.png")
        self.assertEqual(app["p4a.source_dir"], "/p4a")
        self.assertEqual(app["p4a.commit"], "abc")
        self.assertEqual(cfg["buildozer"]["log_level"], "2")

    def test_requirements_not_duplicated(self):
        out = ba.edit_buildozer_spec("[app]\nrequirements = python3,certifi\n", "1.0.0", "/p")
        self.assertIn("requirements = python3,certifi\n", out)

    def test_missing_version_keys_ok(self):
        out = ba.edit_buildozer_spec("[app]\nrequirements = python3\n", "1.0.0", "/p")
        self.assertIn("version = 1.0.0", out)

    def test_parse_pysidedeploy(self):
        text = (
            "[qt]\nmodules = Core,Gui,Widgets\n[android]\nplugins = platforms_qtforandroid,\n"
            "[buildozer]\nlocal_libs = plugins_platforms_qtforandroid\n"
        )
        self.assertEqual(
            ba.parse_pysidedeploy(text),
            (["Core", "Gui", "Widgets"], ["platforms_qtforandroid"], ["plugins_platforms_qtforandroid"]),
        )
        with self.assertRaises(RuntimeError):
            ba.parse_pysidedeploy(text.replace("Widgets", "Network"))

    def test_slim_recipe_code_compiles(self):
        code = ba.slim_recipe_code(["Core", "Widgets"], ["platforms_qtforandroid"], [])
        compile(code, "recipe", "exec")
        self.assertIn("recipe = TrimmedPySideRecipe()", code)
        self.assertIn("'Widgets'", code)
        self.assertIn("Qt6Widgets", code)

    def test_slim_recipe_prunes_unused_qt(self):
        class PySideRecipe:
            def build_arch(self, arch):
                pass

        namespace = {"PySideRecipe": PySideRecipe}
        exec(ba.slim_recipe_code(["Core", "Widgets"], ["platforms_qtforandroid"], []), namespace)
        native = [
            "libQt6Core_arm64-v8a.so", "libQt6Widgets_arm64-v8a.so", "libQt6Network_arm64-v8a.so",
            "libplugins_platforms_qtforandroid_arm64-v8a.so", "libc++_shared.so",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            libs, site = Path(tmp) / "libs", Path(tmp) / "site"
            package = site / "PySide6"
            (package / "Qt" / "lib").mkdir(parents=True)
            (package / "glue").mkdir()
            libs.mkdir()
            for name in native:
                (libs / name).touch()
                (package / "Qt" / "lib" / name).touch()
            bindings = ["QtCore.abi3.so", "QtCore.pyi", "QtNetwork.abi3.so", "QtNetwork.pyi", "QtWidgets.abi3.so"]
            for name in bindings:
                (package / name).touch()
            recipe = namespace["recipe"]
            recipe.ctx = mock.Mock(
                get_libs_dir=lambda arch: str(libs), get_python_install_dir=lambda arch: str(site))

            recipe.build_arch(mock.Mock(arch="arm64-v8a"))

            self.assertEqual(
                sorted(p.name for p in libs.iterdir()),
                ["libQt6Core_arm64-v8a.so", "libQt6Widgets_arm64-v8a.so",
                 "libplugins_platforms_qtforandroid_arm64-v8a.so"],
            )
            self.assertEqual(sorted(p.name for p in package.iterdir()), ["QtCore.abi3.so", "QtCore.pyi", "QtWidgets.abi3.so"])

    def test_apk_name_and_wheel_url(self):
        self.assertEqual(ba.apk_name("1.2.3"), "speedreader_1.2.3_arm64-v8a.apk")
        self.assertEqual(
            ba.wheel_url("shiboken6", "6.12.0", "aarch64"),
            "https://download.qt.io/official_releases/QtForPython/shiboken6/"
            "shiboken6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl",
        )

    def test_debug_keystore_present(self):
        self.assertGreater((ROOT / "packaging" / "android" / "debug.keystore").stat().st_size, 1000)

    def test_build_android_help(self):
        result = subprocess.run(
            [sys.executable, "packaging/build_android.py", "--help"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--sdk-path", "--ndk-path", "--version", "--out"):
            self.assertIn(flag, result.stdout)
        self.assertNotIn("--arch", result.stdout)

    def test_build_android_rejects_bad_version_before_building(self):
        argv = ["--sdk-path", "sdk", "--ndk-path", "ndk", "--version", "banana"]
        with mock.patch.object(ba, "refresh_staging", side_effect=AssertionError("build started")), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit) as caught:
                ba.main(argv)
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("banana", stderr.getvalue())
