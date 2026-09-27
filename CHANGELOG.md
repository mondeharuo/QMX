# 更新记录 / Changelog

## 0.2.0 — 2026-09-28

### 新增

- 产品名称统一为 QMX，应用图标和双语说明页同步更新。
- 文件列表支持勾选单首或多首歌曲，分别批量转换、删除源文件或删除输出文件；删除操作会明确标出作用对象并请求确认。
- 增加批量转换待处理项目的入口。
- 增加历史状态、源文件和输出文件存在性检查，以及名称、状态、首字母和时间排序/筛选。
- 增加源文件和输出文件定位、试听与单项重试能力。

### 修复与改进

- 扫描会结合 SQLite 历史、源文件状态和输出音频有效性刷新列表，减少已成功项目被误报为失败的情况。
- 删除到回收站时改进 Windows Shell 调用与错误反馈。
- 更新批处理操作、中文项目说明和发布包名称。
- 本版提供 Windows x64 便携 ZIP 和用户级安装程序；不包含 x86 构建。

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
