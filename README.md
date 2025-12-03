# 串口调试助手 (Serial Tool)

这是一个功能丰富的串口调试工具, 基于 PyQt5 构建, 旨在为嵌入式开发和硬件调试提供一个强大且易于使用的界面。

## 主要功能

- **完整的串口配置**: 支持所有标准配置, 包括端口、波特率、数据位、校验位和停止位。
- **实时数据收发**: 在独立的线程中处理串口通信, 确保UI界面的流畅性, 并能实时显示发送和接收的数据。
- **命令序列与模板**:
    - 支持创建、编辑和发送多条命令。
    - 可以将命令序列保存为模板 (`.csv` 或 `.txt` 文件) 并随时导入, 方便在不同项目间复用。
    - 支持通过 `mode:` 指令对命令进行模块化分组。
- **连续发送**: 可以按设定的时间间隔, 自动连续发送选中的命令或整个模块的命令。
- **特殊指令支持**:
    - `delay:[ms]`: 在命令序列中插入一个指定毫秒数的非阻塞延迟。
    - `mode:[name]`: 定义一个新的命令模块, 用于组织和管理命令。
- **外部工具集成**:
    - 自动检测并调用独立的 **数字进制转换器** 工具。
    - 在接收区或命令输入框中右键点击, 可以快速启动转换器进行 HEX/DEC 计算。
- **友好的UI体验**:
    - 自动刷新串口列表。
    - 发送和接收字节数统计。
    - 可定制的显示选项, 如时间戳、发送数据显示等。

## 如何运行

该工具被设计为一个Python包。可以通过以下命令从项目根目录启动:

```bash
python -m serial_tool_project.main
```

## 外部工具依赖

该串口工具会自动查找并集成 `number_converter_project`。为了使进制转换功能可用, 请确保 `number_converter_project` 目录与 `serial_tool_project` 目录位于同一个父目录下。

**目录结构示例:**

```
/your_workspace
    /serial_tool_project
        /core
        /managers
        /widgets
        /utils
        main.py
        main_window.py
        ...
    /number_converter_project
        number_conversion_dialog.py
        ...
```

## 文件结构

- `main.py`: 应用程序的主入口。
- `main_window.py`: 主窗口的UI布局和核心逻辑。
- `/core`: 包含核心功能, 如 `serial_thread.py` (串口通信线程)。
- `/managers`: 包含用于管理特定功能的模块, 如 `output_manager.py` (输出显示管理)。
- `/widgets`: 包含所有自定义的UI控件, 如 `command_widgets.py` (命令表格) 和 `custom_widgets.py` (自定义文本框)。
- `/utils`: 包含辅助工具和函数, 如 `ui_utils.py`。
- `/dialogs`: 包含各种对话框, 如 `comment_edit_dialog.py`。
- `/resources`: 存放应用程序所需的资源文件, 如图标。
