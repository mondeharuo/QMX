from __future__ import annotations

import os
import sys
from pathlib import Path

# PySide6's wheel places the Shiboken runtime in a sibling directory; Windows
# needs that directory registered before loading Qt extension modules.
_dll_directory_handles = []
_loaded_qt_libraries = []
if getattr(sys, "frozen", False) and hasattr(os, "add_dll_directory"):
    import ctypes
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for dll_dir in (bundle / "shiboken6", bundle / "PySide6"):
        if dll_dir.is_dir():
            _dll_directory_handles.append(os.add_dll_directory(str(dll_dir)))
            os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    for library in (bundle / "shiboken6" / "shiboken6.abi3.dll",
                    bundle / "PySide6" / "pyside6.abi3.dll",
                    bundle / "PySide6" / "Qt6Core.dll",
                    bundle / "PySide6" / "Qt6Gui.dll",
                    bundle / "PySide6" / "Qt6Widgets.dll"):
        if library.is_file():
            _loaded_qt_libraries.append(ctypes.WinDLL(str(library), winmode=0x00001100))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow

APP_VERSION = "0.2.0"


def main() -> int:
    if "--smoke-test" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv)
    app.setApplicationName("QMX")
    app.setOrganizationName("QMX")
    app.setApplicationVersion(APP_VERSION)
    if getattr(sys, "frozen", False):
        icon_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "assets" / "qmx-icon.ico"
    else:
        icon_path = Path(__file__).resolve().parent / "docs" / "assets" / "qmx-icon.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))
    if "--smoke-test" in sys.argv:
        print("QMX UI initialized")
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
