"""Builds the debug-signed arm64-v8a APK of SpeedReader.

Run it on Linux or macOS with the Android SDK, NDK r28c and JDK 17 installed,
using the Python of a venv that has pyside6 (the same version as the Android
wheels), buildozer==1.6.0 and cython==0.29.33:

    <venv>/bin/python packaging/build_android.py --sdk-path SDK --ndk-path NDK --version 1.2.3

Intermediate files live in build/android/ (staged sources, Qt wheels, the
python-for-android checkout); buildozer's cache there is reused by the next
run. The result is <--out>/speedreader_<version>_arm64-v8a.apk, out/ by default.
"""

import argparse
import configparser
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "android"
STAGING = BUILD / "app"
WHEELS = BUILD / "wheels"
P4A = BUILD / "python-for-android"
P4A_REPO = "https://github.com/kivy/python-for-android.git"
ACTIVITY_PATH = Path("pythonforandroid/bootstraps/qt/build/src/main/java/org/kivy/android/PythonActivity.java")

QT_FOR_PYTHON = "https://download.qt.io/official_releases/QtForPython"
WHEEL_ARCH = "aarch64"

ACTIVITY_ANCHOR = '        Log.v(TAG, "About to do super onCreate");\n'
ACTIVITY_ENV_LINE = '        setEnvironmentVariable("QT_ANDROID_DISABLE_ACCESSIBILITY", "1");\n'

_BACK_CLICK_FIELD = re.compile(r"^[ \t]*(?:private[ \t]+)?long[ \t]+lastBackClick\b[^;]*;[ \t]*\n", re.MULTILINE)
_KEY_HANDLER = re.compile(
    r"^(?:[ \t]*\n)?(?P<indent>[ \t]*)(?:@Override\s+)?public[ \t]+boolean[ \t]+onKeyDown\s*\([^)]*\)\s*\{"
    r".*?\n(?P=indent)\}[ \t]*\n",
    re.MULTILINE | re.DOTALL,
)
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class PatchError(RuntimeError):
    pass


def wheel_url(package: str, version: str, arch: str) -> str:
    filename = f"{package}-{version}-{version}-cp310-abi3-android_{arch}.whl"
    return f"{QT_FOR_PYTHON}/{package}/{filename}"


def patch_activity(java: str) -> str:
    """Disable Qt's accessibility bridge and let the Back key reach Qt."""
    if java.count(ACTIVITY_ANCHOR) != 1:
        raise PatchError("anchor not found")
    java = java.replace(ACTIVITY_ANCHOR, ACTIVITY_ENV_LINE + ACTIVITY_ANCHOR)

    handlers = [m for m in _KEY_HANDLER.finditer(java) if "lastBackClick" in m.group(0)]
    fields = _BACK_CLICK_FIELD.findall(java)
    if len(handlers) != 1 or len(fields) != 1:
        raise PatchError("key handler not found")
    handler = handlers[0]
    java = java[:handler.start()] + java[handler.end():]
    java = _BACK_CLICK_FIELD.sub("", java)
    if "lastBackClick" in java:
        raise PatchError("key handler not found")
    return java


def numeric_version(pep440: str) -> int:
    match = _VERSION.match(pep440)
    if not match:
        raise ValueError(f"not a X.Y.Z version: {pep440!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return 1 + major * 1_000_000 + minor * 10_000 + patch * 100


def _new_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    return config


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def edit_buildozer_spec(text: str, version: str, p4a_dir: str) -> str:
    config = _new_config()
    config.read_string(text)
    app = config["app"]
    requirements = _split(app.get("requirements", ""))
    if "certifi" not in requirements:
        requirements.append("certifi")
    app.pop("version.regex", None)
    app.pop("version.filename", None)
    app.update({
        "title": "SpeedReader",
        "package.name": "speedreader",
        "package.domain": "io.github.koroffin",
        "version": version,
        "android.numeric_version": str(numeric_version(version)),
        "requirements": ",".join(requirements),
        "android.permissions": "android.permission.INTERNET",
        "android.wakelock": "True",
        "orientation": "portrait",
        "icon.filename": "%(source.dir)s/speedreader/assets/icon.png",
        "android.archs": "arm64-v8a",
        "android.api": "35",
        "android.minapi": "28",
        "android.accept_sdk_license": "True",
        "android.skip_update": "True",
        "android.allow_backup": "False",
        "source.exclude_dirs": "deployment,bin,.buildozer",
        "p4a.source_dir": p4a_dir,
    })
    out = io.StringIO()
    config.write(out)
    return out.getvalue()


def parse_pysidedeploy(text: str) -> tuple[list[str], list[str], list[str]]:
    config = _new_config()
    config.read_string(text)
    modules = _split(config.get("qt", "modules", fallback=""))
    plugins = _split(config.get("android", "plugins", fallback=""))
    local_libs = _split(config.get("buildozer", "local_libs", fallback=""))
    if "Widgets" not in modules:
        raise RuntimeError("Widgets not in qt.modules")
    return modules, plugins, local_libs


_RECIPE_SUBCLASS = """

import shutil as _shutil
from pathlib import Path as _Path


class TrimmedPySideRecipe(PySideRecipe):
    used_modules = frozenset(@MODULES@)
    native_keep = frozenset(@NATIVE@)

    def build_arch(self, arch):
        super().build_arch(arch)
        site = _Path(self.ctx.get_python_install_dir(arch.arch)) / "PySide6"
        self.drop_unused_native(_Path(self.ctx.get_libs_dir(arch.arch)), site / "Qt" / "lib", arch.arch)
        self.drop_unused_python(site)

    def drop_unused_native(self, libs_dir, wheel_libs, abi):
        wanted = {"lib%s_%s.so" % (stem, abi) for stem in self.native_keep}
        copied = {lib.name for lib in wheel_libs.glob("lib*.so")}
        for name in sorted(copied - wanted):
            target = libs_dir / name
            if target.exists():
                target.unlink()

    def drop_unused_python(self, site):
        for sub in ("Qt", "include", "typesystems", "glue", "scripts"):
            if (site / sub).is_dir():
                _shutil.rmtree(site / sub)
        for path in sorted(site.iterdir()):
            module, _, suffix = path.name.partition(".")
            if suffix in ("abi3.so", "pyi") and module.startswith("Qt") and module[2:] not in self.used_modules:
                path.unlink()


recipe = TrimmedPySideRecipe()
"""


def slim_recipe_code(qt_modules: list[str], plugins: list[str], local_libs: list[str]) -> str:
    """Python appended to the generated PySide6 recipe: drops the Qt parts the app does not use."""
    native = {f"Qt6{module}" for module in qt_modules}
    native.update(f"plugins_{plugin}" for plugin in plugins if plugin)
    native.update(lib for lib in local_libs if lib)
    code = _RECIPE_SUBCLASS.replace("@MODULES@", repr(sorted(qt_modules)))
    return code.replace("@NATIVE@", repr(sorted(native)))


def apk_name(version: str) -> str:
    return f"speedreader_{version}_arm64-v8a.apk"


log = logging.getLogger("build_android")


def run(cmd: list, cwd: Path, env: dict[str, str] | None = None) -> None:
    cmd = [str(part) for part in cmd]
    log.info("%s$ %s", cwd, " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def venv_environment() -> dict[str, str]:
    scripts = Path(sys.executable).parent
    env = dict(os.environ, VIRTUAL_ENV=sys.prefix)
    env["PATH"] = f"{scripts}{os.pathsep}{env.get('PATH', '')}"
    return env


def refresh_staging() -> None:
    previous = BUILD / "app.previous"
    shutil.rmtree(previous, ignore_errors=True)
    if STAGING.exists():
        STAGING.rename(previous)
    STAGING.mkdir(parents=True)
    if (previous / ".buildozer").is_dir():
        (previous / ".buildozer").rename(STAGING / ".buildozer")
    shutil.rmtree(previous, ignore_errors=True)
    shutil.copyfile(ROOT / "packaging" / "android" / "main.py", STAGING / "main.py")
    shutil.copytree(
        ROOT / "speedreader", STAGING / "speedreader",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def fetch_wheel(package: str, version: str) -> Path:
    url = wheel_url(package, version, WHEEL_ARCH)
    target = WHEELS / Path(urllib.parse.urlsplit(url).path).name
    if target.is_file():
        return target
    WHEELS.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    log.info("downloading %s", url)
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as sink:
        shutil.copyfileobj(response, sink, 1 << 20)
    partial.replace(target)
    return target


def generate_deployment(wheels: dict[str, Path], sdk: Path, ndk: Path, env: dict[str, str]) -> None:
    run(
        [
            "pyside6-android-deploy", "--init", "--force", "--verbose", "--keep-deployment-files",
            "--no-install", "--name=speedreader",
            f"--wheel-pyside={wheels['pyside6']}", f"--wheel-shiboken={wheels['shiboken6']}",
            f"--sdk-path={sdk}", f"--ndk-path={ndk}",
        ],
        STAGING, env,
    )


def trim_pyside_recipe() -> None:
    modules, plugins, local_libs = parse_pysidedeploy((STAGING / "pysidedeploy.spec").read_text(encoding="utf-8"))
    log.info("keeping Qt modules %s, plugins %s, local libs %s", modules, plugins, local_libs)
    recipe = STAGING / "deployment" / "recipes" / "PySide6" / "__init__.py"
    generated = recipe.read_text(encoding="utf-8")
    recipe.write_text(generated + slim_recipe_code(modules, plugins, local_libs), encoding="utf-8")


def checkout_p4a(commit: str, env: dict[str, str]) -> Path:
    if not (P4A / ".git").is_dir():
        shutil.rmtree(P4A, ignore_errors=True)
        run(["git", "clone", "--quiet", "--depth", "1", "--no-checkout", P4A_REPO, P4A], BUILD, env)
    run(["git", "fetch", "--quiet", "--depth", "1", "origin", commit], P4A, env)
    run(["git", "checkout", "--quiet", "--force", "FETCH_HEAD"], P4A, env)
    activity = P4A / ACTIVITY_PATH
    try:
        activity.write_text(patch_activity(activity.read_text(encoding="utf-8")), encoding="utf-8")
    except PatchError as error:
        raise PatchError(f"{activity}: {error}") from error
    return P4A


def configure_buildozer(version: str, env: dict[str, str]) -> None:
    spec = STAGING / "buildozer.spec"
    text = spec.read_text(encoding="utf-8")
    config = _new_config()
    config.read_string(text)
    p4a = checkout_p4a(config["app"]["p4a.commit"], env)
    spec.write_text(edit_buildozer_spec(text, version, str(p4a)), encoding="utf-8")


def package_apk(version: str, out: Path, env: dict[str, str]) -> Path:
    run([sys.executable, "-m", "buildozer", "android", "debug"], STAGING, env)
    built = sorted((STAGING / "bin").glob("*.apk"))
    if len(built) != 1:
        raise RuntimeError(f"expected one APK in {STAGING / 'bin'}, found {[apk.name for apk in built]}")
    out.mkdir(parents=True, exist_ok=True)
    target = out / apk_name(version)
    shutil.copyfile(built[0], target)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the SpeedReader debug APK.")
    parser.add_argument("--sdk-path", type=Path, required=True, help="Android SDK folder")
    parser.add_argument("--ndk-path", type=Path, required=True, help="Android NDK r28c folder")
    parser.add_argument("--version", required=True, help="PEP 440 version, as set_version.py prints it")
    parser.add_argument("--out", type=Path, default=ROOT / "out", help="folder for the APK (default: out/)")
    args = parser.parse_args(argv)
    try:
        numeric_version(args.version)
    except ValueError as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO)

    import PySide6

    env = venv_environment()
    refresh_staging()
    wheels = {package: fetch_wheel(package, PySide6.__version__) for package in ("pyside6", "shiboken6")}
    generate_deployment(wheels, args.sdk_path.resolve(), args.ndk_path.resolve(), env)
    trim_pyside_recipe()
    configure_buildozer(args.version, env)
    print(package_apk(args.version, args.out.resolve(), env))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
