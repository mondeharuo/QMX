# 更新记录 / Changelog

## 0.1.0 — 2026-09-25

### 修复

- 每次扫描同时核对源文件版本、数据库状态和输出音频有效性。有效输出会恢复显示为已完成，避免重试时因拒绝覆盖而重复标记失败。
- 输出丢失或无效时同步更新扫描状态；源文件已变化而旧输出仍存在时明确报告冲突，不覆盖旧输出。

### 中文

- 首个 Windows x64 图形界面版本。
- 应用图标和中英文项目说明页加入主界面截图。
- 支持递归扫描，并保留子目录和原音频 basename。
- 使用 SQLite 增量记录、输出校验、LRC 原样复制和安全跳过。
- 本地调用 `qmdec` 与 `ffprobe`；Windows 下隐藏子进程命令行窗口。
- 提供便携 ZIP 和用户级安装程序。

### English

- First Windows x64 GUI release.
- Added the application icon and a main-window screenshot to the bilingual project pages.
- Recursive scanning preserves subdirectories and original audio basenames.
- SQLite incremental state, output validation, byte-for-byte LRC copying, and safe skipping.
- Invokes local `qmdec` and `ffprobe`; hides subprocess console windows on Windows.
- Includes portable ZIP and per-user installer distributions.
