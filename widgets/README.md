# Widgets 模块

`widgets` 模块包含应用程序中使用的所有自定义Qt控件。

## `custom_widgets.py`

- **`OutputSource`**: 一个 `Enum`，定义了输出消息的类型（`SEND`, `RECEIVE`, `SYSTEM`, `ERROR`）。
- **`SpecialCommandType`**: 一个 `Enum`，定义了特殊命令的类型（`MODE`, `DELAY`）。
- **`ExpandingTextEdit`**: 一个 `QTextEdit` 的子类，其高度可以根据内容自动扩展，提供了类似 `QLineEdit` 的单行输入体验，但又能处理多行文本。
- **`CustomTextBrowser`**: `QTextBrowser` 的子类，增加了自定义的右键上下文菜单，包括复制、全选和进制转换等功能。

## `command_widgets.py`

- **`CommentDisplayWidget`**: 一个轻量级控件，用于在 `CommandLineEdit` 上方半透明地显示注释。
- **`CommandLineEdit`**: `QLineEdit` 的子类，集成了 `CommentDisplayWidget` 来显示命令的注释，并提供了丰富的文本编辑右键菜单。
- **`CommandTableWidget`**: `QTableWidget` 的子类，是命令管理的核心UI组件。它使用 `CommandLineEdit` 作为命令输入框，并管理命令的添加、删除、编辑以及与发送相关的操作。
