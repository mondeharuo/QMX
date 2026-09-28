<p align="center"><img src="docs/assets/qmx-icon.png" alt="QMX application icon" width="144"></p>

<h1 align="center">QMX</h1>

<p align="center"><strong>Windows x64 local music library manager and batch processor</strong></p>

<p align="center"><a href="README.md">中文说明</a></p>

<p align="center"><img src="docs/assets/qmx-main.png" alt="QMX main window screenshot" width="100%"></p>

**Version 0.2.0 · QMX is a Windows 10/11 x64 desktop app for local music library management and batch file processing. It recursively scans folders, preserves artist and album directories, copies matching LRC lyrics, tracks processing history, and uses the separately installed `qmdec` command-line tool.**

QMX recursively scans a source tree, preserves relative folders and filenames, copies matching LRC files byte-for-byte, validates output audio with `ffprobe`, and tracks work in SQLite so completed items can be skipped on later runs. Source files are treated as read-only.

Search terms: Windows local music library manager, local music file organizer, batch music file processor, music library management for Windows, LRC lyrics copy, SQLite processing history, and a desktop GUI for `qmdec`. QMX is a local management interface and does not include the `qmdec` processing engine.

> Use only with files and accounts you are authorized to access. QMX does not include or reimplement `qmdec` or its file-processing algorithms. QQ Music and `qmdec` are third-party projects and are not affiliated with this project.

## Downloads

Download the latest release from the [GitHub Releases page](https://github.com/mondeharuo/QMX/releases):

- **`QMX-windows-x64-v0.2.0.zip`** — portable folder; extract the full archive and run `QMX.exe`.
- **`QMX-Setup-x64-v0.2.0.exe`** — per-user Windows installer; no administrator prompt is required.

The application is built for 64-bit Windows. It is not signed with a commercial code-signing certificate, so Windows SmartScreen may show its standard first-run warning.

## Requirements

- Windows 10 or later, x64
- `qmdec` installed and configured separately. Follow its official instructions: <https://github.com/Sophomoresty/qmdec>
- `ffprobe.exe` from FFmpeg. QMX checks `PATH` and common WinGet locations; if it cannot find `ffprobe`, choose it in **Settings**.

QMX never asks you to send account credentials to this project. Any authentication needed by `qmdec` is handled by that separate program on your own computer.

## Quick start

1. Install the prerequisites above.
2. Extract the ZIP or run the installer.
3. Open QMX and confirm the source and output folders.
4. Click **Scan** and review the discovered items.
5. Click **Start Processing** when ready.

Default folders are `G:\Music\VipSongsDownload` and `G:\Music\Decoded`. Settings are saved per Windows user. The local processing database and rotating logs are kept beside the app in `data` and `logs`.

## Safety and behavior

- Conversion leaves source files unchanged and writes generated files to the output folder. Separate manual delete actions identify source versus output targets and require confirmation.
- Relative folder structure and audio basenames are preserved; only the output audio extension changes to match the generated format.
- Existing valid output is not overwritten.
- LRC files are copied without text decoding or modification.
- SQLite records make repeated scans incremental. The database is local to this PC.
- Audio output is checked with `ffprobe` before it is recorded as successful.
- Select one or several rows to convert them, or separately delete selected source files or outputs after a confirmation that identifies the target.
- Filter by status, prioritize failures, inspect processing history, open file locations, and audition playable files.

## Build from source

Use Python 3.10 or newer (64-bit recommended):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\build.bat
```

To build both distribution formats, install Inno Setup 6 and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_release.ps1
```

The script writes the portable ZIP and installer to `release`. It does not include local databases, logs, song files, `qmdec`, or FFmpeg.

## Project status

This is an early release. Version 0.2.0 targets Windows x64 and does not include an x86 build. Please report reproducible issues with the application version and a sanitized error message; do not attach encrypted songs, cookies, tokens, or account credentials.

## License

No license has been selected for QMX yet. Until a license is added, the source is publicly viewable but reuse and redistribution are not granted. See `THIRD_PARTY_NOTICES.md` for the licenses of bundled dependencies.
