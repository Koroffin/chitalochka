import re
import sys
from pathlib import Path

USAGE = "usage: set_version.py <tag-or-version> [init-path]"
_RELEASE = re.compile(r"(\d+\.\d+\.\d+)(?:-([A-Za-z0-9.]+))?")
_DEV = re.compile(r"(\d+\.\d+\.\d+)\.dev(\d+)")
_VERSION_LINE = re.compile(r'^__version__ = ".*"$', re.MULTILINE)


def parse(raw: str) -> tuple[str, str, bool]:
    value = raw[1:] if raw.startswith("v") else raw
    if m := _DEV.fullmatch(value):
        base, n = m.groups()
        return f"{base}~dev{n}", f"{base}.dev{n}", True
    if m := _RELEASE.fullmatch(value):
        base, suffix = m.groups()
        if suffix is None:
            return base, base, False
        return f"{base}~{suffix}", f"{base}{suffix}", True
    raise ValueError(f"not a version: {raw!r} (expected X.Y.Z, X.Y.Z-<suffix> or X.Y.Z.dev<N>)")


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        deb, pep440, prerelease = parse(argv[0])
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    init = Path(argv[1]) if len(argv) == 2 else Path(__file__).resolve().parent.parent / "speedreader" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    new_text, count = _VERSION_LINE.subn(f'__version__ = "{pep440}"', text)
    if count != 1:
        print(f"{init}: expected one __version__ line, found {count}", file=sys.stderr)
        return 2
    init.write_text(new_text, encoding="utf-8")
    print(f"deb={deb} pep440={pep440} prerelease={'true' if prerelease else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
