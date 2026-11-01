"""Build the SpeedReader debug APK with pyside6-android-deploy and buildozer.

Linux or macOS only. Needs the Android SDK, NDK r28c and JDK 17+. Run from any
folder with a Python that has pyside6, buildozer==1.6.0 and cython==0.29.33:

    python packaging/build_android.py --sdk-path <sdk> --ndk-path <ndk> --version <pep440>

The APK is written to --out (default: out/).
"""

import configparser
import io
import re

WHEEL_URL = (
    "https://download.qt.io/official_releases/QtForPython/{package}/"
    "{package}-{version}-{version}-cp310-abi3-android_{arch}.whl"
)
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


def main(argv=None) -> int:
    pass


if __name__ == "__main__":
    raise SystemExit(main())
