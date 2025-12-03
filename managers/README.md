# Managers 模块

`managers` 模块包含用于处理特定任务的管理类。

## `output_manager.py`

- **`OutputManager`**: 负责统一管理到接收显示区域的文本输出。它处理时间戳的添加、文本颜色的格式化（例如，用于发送、接收、错误和系统消息），并确保文本正确显示。

## `special_command_manager.py`

- **`SpecialCommandManager`**: 处理在命令序列中遇到的特殊指令（例如，`mode:xxx` 或 `delay:100`）。它提供了一个可扩展的系统，用于定义和执行这些命令。
