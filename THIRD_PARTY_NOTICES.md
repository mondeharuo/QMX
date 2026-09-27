# Third-party notices

QMX bundles open-source runtime components in its Windows distribution. Their copyright remains with their respective authors. This project does not bundle `qmdec` or FFmpeg.

| Component | Bundled version | License / notice |
| --- | --- | --- |
| Python runtime | 3.12.10 | Python Software Foundation License; <https://docs.python.org/3/license.html> |
| PySide6 and Qt 6 | 6.8.3 | LGPL-3.0-only, GPL options, or commercial terms; see `licenses/LGPL-3.0.txt` and <https://www.qt.io/licensing/> |
| Shiboken6 | 6.8.3 | LGPL-3.0-only, GPL options, or commercial terms; see `licenses/LGPL-3.0.txt` and <https://www.qt.io/licensing/> |
| PyInstaller | 6.22.3 | GPLv2-or-later with a special exception for distributing applications; see `licenses/PyInstaller-COPYING.txt` and <https://pyinstaller.org/en/stable/license.html> |

PySide6/Qt and Shiboken6 are distributed as shared libraries in the `_internal` folder, rather than statically linked into the QMX executable. Their upstream licenses permit redistribution under their stated terms. Users replacing those shared libraries should use compatible builds and follow the applicable LGPL terms.

The application invokes the separately installed `qmdec` CLI and `ffprobe.exe`. Neither program is included in this release. See the upstream `qmdec` project for its own license and requirements: <https://github.com/Sophomoresty/qmdec>.
