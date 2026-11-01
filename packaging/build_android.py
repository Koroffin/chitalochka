"""Build the SpeedReader debug APK with pyside6-android-deploy and buildozer.

Linux or macOS only. Needs the Android SDK, NDK r28c and JDK 17+. Run from any
folder with a Python that has pyside6, buildozer==1.6.0 and cython==0.29.33:

    python packaging/build_android.py --sdk-path <sdk> --ndk-path <ndk> --version <pep440>

The APK is written to --out (default: out/).
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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "android"
STAGING = BUILD / "app"
WHEELS = BUILD / "wheels"
P4A = BUILD / "python-for-android"
P4A_REPO = "https://github.com/kivy/python-for-android.git"
ACTIVITY_PATH = Path("pythonforandroid/bootstraps/qt/build/src/main/java/org/kivy/android/PythonActivity.java")

WHEEL_URL = (
    "https://download.qt.io/official_releases/QtForPython/{package}/"
    "{package}-{version}-{version}-cp310-abi3-android_{arch}.whl"
)
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
    return WHEEL_URL.format(package=package, version=version, arch=arch)


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


_SLIM_RECIPE = '''

import shutil
from pathlib import Path


class SlimRecipe(PySideRecipe):
    qt_modules = {qt_modules}
    keep_libs = {keep_libs}

    def build_arch(self, arch):
        super().build_arch(arch)
        libs_dir = Path(self.ctx.get_libs_dir(arch.arch))
        package = Path(self.ctx.get_python_install_dir(arch.arch)) / "PySide6"
        keep = {{f"lib{{name}}_{{arch.arch}}.so" for name in self.keep_libs}}
        for lib in (package / "Qt" / "lib").glob("lib*.so"):
            if lib.name not in keep:
                (libs_dir / lib.name).unlink(missing_ok=True)
        for folder in ("Qt", "include", "typesystems", "glue", "scripts"):
            shutil.rmtree(package / folder, ignore_errors=True)
        for binding in package.glob("Qt*.abi3.so"):
            module = binding.name[:-len(".abi3.so")]
            if module[len("Qt"):] not in self.qt_modules:
                binding.unlink()
                (package / f"{{module}}.pyi").unlink(missing_ok=True)


recipe = SlimRecipe()
'''


def slim_recipe_code(qt_modules: list[str], plugins: list[str], local_libs: list[str]) -> str:
    """Source appended to the generated PySide6 recipe; it drops Qt parts the app does not use."""
    keep = {f"Qt6{m}" for m in qt_modules} | {f"plugins_{p}" for p in plugins if p} | {lib for lib in local_libs if lib}
    return _SLIM_RECIPE.format(qt_modules=repr(list(qt_modules)), keep_libs=repr(sorted(keep)))


def apk_name(version: str) -> str:
    return f"speedreader_{version}_arm64-v8a.apk"


log = logging.getLogger("build_android")


def _bin(name: str) -> str:
    return str(Path(sys.executable).parent / name)


def stage_sources() -> None:
    STAGING.mkdir(parents=True, exist_ok=True)
    for item in STAGING.iterdir():
        if item.name == ".buildozer":
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()
    shutil.copy2(ROOT / "packaging" / "android" / "main.py", STAGING / "main.py")
    shutil.copytree(
        ROOT / "speedreader", STAGING / "speedreader",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    log.info("staged sources in %s", STAGING)


def download_wheel(package: str, version: str, arch: str) -> Path:
    url = wheel_url(package, version, arch)
    path = WHEELS / url.rsplit("/", 1)[-1]
    if not path.is_file():
        WHEELS.mkdir(parents=True, exist_ok=True)
        log.info("downloading %s", url)
        partial = path.with_name(path.name + ".part")
        urllib.request.urlretrieve(url, partial)
        partial.replace(path)
    return path


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = sys.prefix
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    return env


def init_deployment(pyside_wheel: Path, shiboken_wheel: Path, sdk: Path, ndk: Path) -> None:
    subprocess.run(
        [
            _bin("pyside6-android-deploy"), "--init", "--force", "--verbose",
            "--keep-deployment-files", "--no-install", "--name=speedreader",
            f"--wheel-pyside={pyside_wheel}", f"--wheel-shiboken={shiboken_wheel}",
            f"--sdk-path={sdk}", f"--ndk-path={ndk}",
        ],
        cwd=STAGING, env=build_env(), check=True,
    )


def slim_recipe() -> None:
    qt_modules, plugins, local_libs = parse_pysidedeploy(
        (STAGING / "pysidedeploy.spec").read_text(encoding="utf-8"))
    recipe = STAGING / "deployment" / "recipes" / "PySide6" / "__init__.py"
    with recipe.open("a", encoding="utf-8") as file:
        file.write(slim_recipe_code(qt_modules, plugins, local_libs))
    log.info("Qt modules %s, plugins %s, local libs %s", qt_modules, plugins, local_libs)


def prepare_p4a(commit: str) -> Path:
    if not (P4A / ".git").is_dir():
        P4A.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=P4A, check=True)
        subprocess.run(["git", "remote", "add", "origin", P4A_REPO], cwd=P4A, check=True)
    subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", commit], cwd=P4A, check=True)
    subprocess.run(["git", "checkout", "-q", "--force", "FETCH_HEAD"], cwd=P4A, check=True)
    activity = P4A / ACTIVITY_PATH
    try:
        patched = patch_activity(activity.read_text(encoding="utf-8"))
    except PatchError as error:
        raise PatchError(f"{activity}: {error}") from error
    activity.write_text(patched, encoding="utf-8")
    log.info("python-for-android %s patched in %s", commit, P4A)
    return P4A


def write_buildozer_spec(version: str) -> None:
    spec = STAGING / "buildozer.spec"
    text = spec.read_text(encoding="utf-8")
    config = _new_config()
    config.read_string(text)
    p4a_dir = prepare_p4a(config["app"]["p4a.commit"])
    spec.write_text(edit_buildozer_spec(text, version, str(p4a_dir)), encoding="utf-8")


def build_apk(version: str, out: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "buildozer", "android", "debug"],
        cwd=STAGING, env=build_env(), check=True,
    )
    bin_dir = STAGING / "bin"
    apks = sorted(bin_dir.glob("*.apk"), key=lambda apk: apk.stat().st_mtime)
    if not apks:
        raise FileNotFoundError(f"no APK in {bin_dir}")
    out.mkdir(parents=True, exist_ok=True)
    target = out / apk_name(version)
    shutil.copy2(apks[-1], target)
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

    stage_sources()
    pyside_wheel = download_wheel("pyside6", PySide6.__version__, WHEEL_ARCH)
    shiboken_wheel = download_wheel("shiboken6", PySide6.__version__, WHEEL_ARCH)
    init_deployment(pyside_wheel, shiboken_wheel, args.sdk_path.resolve(), args.ndk_path.resolve())
    slim_recipe()
    write_buildozer_spec(args.version)
    apk = build_apk(args.version, args.out.resolve())
    print(apk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
