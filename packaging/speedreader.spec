import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
onefile = os.environ.get("SPEEDREADER_ONEFILE") == "1"

a = Analysis(
    [os.path.join(ROOT, "speedreader", "__main__.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "speedreader", "assets"), "speedreader/assets")],
    excludes=[
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtXml",
        "PySide6.QtConcurrent",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "tkinter",
        "unittest",
        "pydoc",
    ],
)
pyz = PYZ(a.pure)
icon = os.path.join(ROOT, "assets", "icon.ico") if sys.platform == "win32" else None

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        name="SpeedReader",
        console=False,
        icon=icon,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name="SpeedReader",
        console=False,
        icon=icon,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="SpeedReader")
