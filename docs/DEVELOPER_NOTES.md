# 开发者笔记 (Developer Notes)

本文档面向开发者，提供项目架构、设计原则、扩展指南和技术细节。

---

## 目录

- [项目架构](#项目架构)
- [核心设计原则](#核心设计原则)
- [模块详解](#模块详解)
- [扩展开发指南](#扩展开发指南)
- [代码规范](#代码规范)
- [调试技巧](#调试技巧)
- [性能优化](#性能优化)
- [已知问题](#已知问题)

---

## 项目架构

### 目录结构

```
serial_tool_project/
├── main.py                          # 应用程序主入口, 负责 GUI/CLI 启动分流
├── main_window.py                   # 主窗口 UI 和核心交互逻辑
├── main.spec                        # PyInstaller 打包配置
├── main_config.json                 # 应用程序配置文件 (自动生成)
├── resources/                       # 资源文件
│   └── HOWE_LOGO.ico               # 应用程序图标
├── core/                            # 核心功能模块
│   ├── __init__.py
│   ├── cli_runner.py               # CLI 模式入口和命令执行
│   ├── command_executor.py         # GUI/CLI 共用的命令模块解析
│   ├── output_rules.py             # GUI/CLI 共用的输出规则和文案
│   ├── remote_control.py           # 局域网远程串口控制
│   └── serial_thread.py            # 串口通信线程
├── managers/                        # 功能管理模块
│   ├── __init__.py
│   ├── config_manager.py           # 配置管理器 (工具注册表)
│   ├── output_manager.py           # Qt 接收区输出写入器
│   └── special_command_manager.py  # 特殊指令管理器
├── widgets/                         # 自定义 UI 控件
│   ├── __init__.py
│   ├── base_widgets.py             # 基础控件混入类
│   ├── custom_widgets.py           # 自定义控件库
│   └── command_widgets.py          # 命令表格控件
├── utils/                           # 辅助工具和 UI 样式
│   ├── __init__.py
│   └── ui_utils.py                 # UI 工具类、枚举、颜色常量
├── dialogs/                         # 交互对话框
│   ├── __init__.py
│   ├── config_dialog.py            # 配置对话框 (工具设置)
│   └── comment_edit_dialog.py      # 注释编辑对话框
└── docs/                            # 文档目录
    ├── VERSION_HISTORY.md          # 版本更新记录
    ├── COMMAND_LINE_USAGE.md       # 命令行使用指南
    └── DEVELOPER_NOTES.md          # 开发者笔记 (本文档)
```

## 核心设计原则

### 1. 工具注册表机制

**设计目标**: 单一数据源 (Single Source of Truth)

所有工具的元数据和默认配置统一管理在 `ConfigManager.REGISTERED_TOOLS` 中。

#### 注册表结构

[managers/config_manager.py](../managers/config_manager.py)

```python
class ConfigManager:
    """管理应用程序的配置"""

    # 工具注册表：统一管理所有可用工具
    REGISTERED_TOOLS = {
        "tool_key": {
            "display_name": "工具显示名称",
            "button_text": "按钮文本",
            "requires_serial_port": False,  # 是否需要独占串口
            "default_config": {
                "path": "",
                "enabled": True,
                # ... 工具特定参数
            }
        }
    }
```

#### 设计优势

- ✅ **单一数据源** - 默认配置集中定义，避免分散和不一致
- ✅ **自动同步** - 修改注册表中的默认值，所有引用处自动同步
- ✅ **易于扩展** - 添加新工具只需在注册表中注册，无需修改多处代码
- ✅ **DRY 原则** - 消除重复代码，移除 150+ 行重复
- ✅ **版本兼容** - 自动迁移旧版本配置，补充缺失字段

#### 工作流程

```
启动应用
   ↓
加载配置文件 (main_config.json)
   ↓
检测配置版本
   ↓
缺失字段? → 从 REGISTERED_TOOLS 补充默认值
   ↓
合并用户配置 + 默认配置
   ↓
应用运行
```

### 2. 特殊指令系统

**设计目标**: 非阻塞异步执行

所有特殊指令采用异步设计，确保 UI 响应性。

#### 指令类型

[managers/special_command_manager.py](../managers/special_command_manager.py)

| 指令 | 格式 | 异步机制 | 实现方式 |
|------|------|----------|----------|
| delay | `delay:500` | QTimer.singleShot | 非阻塞延迟 |
| SendHex | `SendHex:FF 00` | 直接发送 | 十六进制转换 |
| BaudRate | `BaudRate:9600` | 异步重连 | 断开 → 重连 |
| SetEndlog | `SetEndlog:\r\n` | 立即生效 | 修改全局变量 |
| SendMode | `SendMode:模块` | 完成回调 | 递归执行 |
| mode | `mode:模块名` | 立即生效 | 模块标记 |

#### SendMode 执行流程

```python
def execute_sendmode_inline(self, module_name, context, completion_callback=None):
    """执行 SendMode 指令 (内联跳转)"""

    def send_module_command(index=0):
        if index >= len(commands_to_send):
            # 所有命令发送完毕
            if completion_callback:
                completion_callback()  # 回调通知完成
            return

        # 发送当前命令
        command_data = commands_to_send[index]
        # ... 发送逻辑

        # 递归发送下一条命令 (使用 QTimer 实现非阻塞)
        QTimer.singleShot(interval, lambda: send_module_command(index + 1))

    send_module_command(0)  # 开始执行
```

**关键点**:
- 使用 **完成回调** 机制确保顺序执行
- 使用 **QTimer.singleShot** 实现非阻塞
- 支持 **嵌套跳转** (但不建议过度嵌套)

### 3. 配置持久化

**设计目标**: 增量保存，按需更新

采用增量保存策略，只在配置变更时写入文件。

#### 配置文件结构

```json
{
  "tool_version": "1.1.1",
  "tool_update_time": "2025-12-02 00:00:00",
  "config_version": "1.1.1",
  "config_last_updated": "2026-01-22 10:00:00",
  "tools": {
    "tool_key": {
      "path": "D:/Tools/tool.exe",
      "enabled": true,
      "custom_param": "value"
    }
  },
  "last_used_directory": "D:/Projects/",
  "state": {
    "basic_settings": {...},
    "receive_settings": {...},
    "send_settings": {...},
    "ui_settings": {...},
    "commands": [...]
  }
}
```

`last_state` 是旧版本字段, 加载配置时会自动迁移为 `state`。

#### 版本兼容性机制

```python
def load_config(self):
    """加载配置文件，自动迁移旧版本"""

    # 检测配置版本
    config_version = config_data.get("config_version", "0.0.0")

    # 版本过高，拒绝加载
    if config_version > CURRENT_VERSION:
        raise ValueError("配置文件版本过高")

    # 版本过低，自动迁移
    if config_version < CURRENT_VERSION:
        self.migrate_config(config_data)

    # 补充缺失的默认值
    for tool_key, tool_info in REGISTERED_TOOLS.items():
        if tool_key not in config_data["tools"]:
            config_data["tools"][tool_key] = tool_info["default_config"].copy()
```

### 4. 模块化设计

**设计目标**: 高内聚，低耦合

每个模块职责单一，通过明确的接口交互。

#### 模块依赖关系

```
main.py
   ↓
main_window.py (主窗口)
   ↓
┌──────────────────────────────────────────┐
│                                          │
├─ managers/                               │
│  ├─ config_manager.py (配置管理)         │
│  ├─ output_manager.py (输出管理)         │
│  └─ special_command_manager.py (指令管理)│
│                                          │
├─ widgets/                                │
│  ├─ custom_widgets.py (自定义控件)       │
│  └─ command_widgets.py (命令表格)        │
│                                          │
├─ core/                                   │
│  └─ serial_thread.py (串口线程)          │
│                                          │
├─ dialogs/                                │
│  ├─ config_dialog.py (配置对话框)        │
│  └─ comment_edit_dialog.py (注释对话框)  │
│                                          │
└─ utils/                                  │
   └─ ui_utils.py (UI 工具类)              │
                                           │
```

---

## 模块详解

本节替代原来各目录下的 README, 用于集中说明目录职责和每个 Python 文件的职责边界。目录级 README 不再维护, 避免说明分散和过期。

### 顶层入口

| 文件 | 职责 |
|------|------|
| `main.py` | 应用程序主入口, 根据启动参数进入 GUI 模式或 CLI 模式, 并维护工具版本信息。 |
| `main_window.py` | GUI 主窗口, 负责界面布局, 串口操作, 远程控制协调, 命令发送流程和状态保存。 |

### core

`core/` 放置可被 GUI 或 CLI 复用的核心能力, 尽量不直接依赖具体界面控件。

| 文件 | 职责 |
|------|------|
| `core/serial_thread.py` | 后台串口线程, 负责串口打开, 读取, 写入, 波特率切换和错误信号上报。 |
| `core/remote_control.py` | 局域网远程控制通信层, 实现主控端和远程端的 TCP JSON 消息交互。 |
| `core/cli_runner.py` | CLI 模式执行器, 负责命令行参数, 配置读取, 串口发送, 模块执行和 CLI 输出。 |
| `core/command_executor.py` | 命令序列解析器, 将 GUI 表格或配置文件中的命令统一解析为可执行模块命令。 |
| `core/output_rules.py` | 输出策略中心, 统一管理输出过滤, 时间戳, 颜色, ANSI 映射和共享提示文案。 |

### managers

`managers/` 放置面向应用流程的管理类, 通常由主窗口持有并调用。

| 文件 | 职责 |
|------|------|
| `managers/config_manager.py` | 配置管理器, 负责 JSON 配置加载保存, 版本检查, 旧字段迁移和工具注册表默认值补齐。 |
| `managers/output_manager.py` | Qt 输出写入器, 负责把分类后的输出文本按 `OutputRules` 写入接收显示区。 |
| `managers/special_command_manager.py` | 特殊指令管理器, 负责 GUI 流程中的 `delay`, `SendHex`, `BaudRate`, `ComPort`, `SetEndlog`, `SendMode`, `StopContinuous` 执行。 |

### widgets

`widgets/` 放置可复用的 PyQt 控件和控件混入逻辑。

| 文件 | 职责 |
|------|------|
| `widgets/custom_widgets.py` | 通用自定义控件, 包括接收区文本浏览器, 可折叠分组框和可点击下拉框。 |
| `widgets/command_widgets.py` | 命令表格相关控件, 负责命令输入框, 注释显示, 行编辑, 特殊命令菜单和批量操作。 |
| `widgets/base_widgets.py` | 基础控件混入类, 负责外部工具启动, 进制转换器调用和跨控件复用逻辑。 |

### dialogs

`dialogs/` 放置独立对话框, 不承担主业务状态管理。

| 文件 | 职责 |
|------|------|
| `dialogs/config_dialog.py` | 工具配置对话框, 负责外部工具路径, 启用状态和工具参数设置。 |
| `dialogs/comment_edit_dialog.py` | 命令注释编辑对话框, 提供多行注释编辑界面。 |

### utils

`utils/` 放置轻量公共工具, 避免依赖主窗口或管理器。

| 文件 | 职责 |
|------|------|
| `utils/ui_utils.py` | UI 通用定义, 包括输出来源枚举, 特殊命令枚举, 颜色常量, 资源路径和特殊命令解析函数。 |

### 输出相关边界

| 模块 | 职责边界 |
|------|----------|
| `core/output_rules.py` | 决定某类输出是否显示, 使用什么颜色, 是否加时间戳, 以及共享文案内容。 |
| `managers/output_manager.py` | 只负责把文本写入 Qt 控件, 不新增业务文案规则。 |
| `core/cli_runner.py::CliOutput` | 只负责把文本写入 stdout/stderr, 复用 `OutputRules`。 |

### 命令执行相关边界

| 模块 | 职责边界 |
|------|----------|
| `core/command_executor.py` | 只解析命令行归属和特殊命令类型, 不打开串口, 不操作 UI。 |
| `managers/special_command_manager.py` | GUI 运行态特殊命令执行, 需要通过主窗口上下文操作串口和控件状态。 |
| `core/cli_runner.py` | CLI 运行态特殊命令执行, 以配置文件 `state` 为数据源。 |

---
## 扩展开发指南

### 添加新的特殊指令

#### 步骤 1: 在 SpecialCommandType 中注册指令

[utils/ui_utils.py](../utils/ui_utils.py)

```python
class SpecialCommandType(Enum):
    """特殊指令类型枚举"""
    DELAY = "delay"
    SEND_HEX = "SendHex"
    BAUD_RATE = "BaudRate"
    SET_ENDLOG = "SetEndlog"
    SEND_MODE = "SendMode"
    MODE = "mode"
    YOUR_NEW_COMMAND = "YourCommand"  # 新增指令
```

#### 步骤 2: 在 SpecialCommandManager 中实现解析

[managers/special_command_manager.py](../managers/special_command_manager.py)

```python
def is_special_command(self, command):
    """判断是否为特殊指令"""
    return (... or
            command.startswith("YourCommand:"))

def parse_command(self, command):
    """解析特殊指令"""
    if command.startswith("YourCommand:"):
        param = command.split(":", 1)[1]
        return (SpecialCommandType.YOUR_NEW_COMMAND, param)
    # ...
```

#### 步骤 3: 实现执行逻辑

```python
def execute_your_command(self, param, context, callback):
    """执行你的新指令"""
    # 实现逻辑
    # ...

    # 完成后调用回调
    if callback:
        callback()
```

#### 步骤 4: 在 main_window.py 中集成

[main_window.py](../main_window.py)

```python
def execute_special_command(self, command, callback):
    """执行特殊指令"""
    cmd_type, param = self.special_command_manager.parse_command(command)

    if cmd_type == SpecialCommandType.YOUR_NEW_COMMAND:
        self.special_command_manager.execute_your_command(
            param, context, callback
        )
```

### 添加新的外部工具

#### 步骤 1: 在 REGISTERED_TOOLS 中注册

[managers/config_manager.py](../managers/config_manager.py)

```python
REGISTERED_TOOLS = {
    "your_tool_key": {
        "display_name": "你的工具名称",
        "button_text": "工具按钮",
        "requires_serial_port": False,  # 是否需要独占串口
        "default_config": {
            "path": "",
            "enabled": True,
            "custom_param1": "default_value1",
            "custom_param2": 123,
            # ... 添加工具特定参数
        }
    }
}
```

#### 步骤 2: 在 main_window.py 中添加按钮

[main_window.py](../main_window.py)

```python
def create_tool_buttons(self):
    """创建工具按钮"""

    # 添加你的工具按钮
    self.your_tool_button = QPushButton("你的工具")
    self.your_tool_button.clicked.connect(self.open_your_tool)
    tool_layout.addWidget(self.your_tool_button)
```

#### 步骤 3: 实现工具打开方法

```python
def open_your_tool(self):
    """打开你的工具"""
    tool_config = self.config_manager.get_tool_config("your_tool_key")

    if not tool_config.get("enabled", False):
        QMessageBox.warning(self, "提示", "工具未启用，请在配置中启用")
        return

    tool_path = tool_config.get("path", "")
    if not tool_path or not os.path.exists(tool_path):
        QMessageBox.warning(self, "提示", "工具路径无效")
        return

    # 启动工具
    import subprocess
    subprocess.Popen([tool_path])
```

#### 步骤 4: 在配置对话框中添加设置界面

[dialogs/config_dialog.py](../dialogs/config_dialog.py)

```python
def create_your_tool_tab(self):
    """创建你的工具配置页"""
    widget = QWidget()
    layout = QVBoxLayout(widget)

    # 启用勾选框
    enabled_checkbox = QCheckBox("启用工具")
    layout.addWidget(enabled_checkbox)

    # 路径选择
    path_layout = QHBoxLayout()
    path_edit = QLineEdit()
    browse_button = QPushButton("浏览...")
    path_layout.addWidget(path_edit)
    path_layout.addWidget(browse_button)
    layout.addLayout(path_layout)

    # 自定义参数
    # ...

    return widget
```

### 添加新的自定义控件

#### 示例: 创建一个可拖拽排序的列表

```python
class DraggableListWidget(QListWidget):
    """支持拖拽排序的列表控件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def dropEvent(self, event):
        """处理拖拽事件"""
        super().dropEvent(event)
        self.item_order_changed.emit()  # 发出顺序改变信号

    item_order_changed = pyqtSignal()
```

---

## 代码规范

### 命名规范

- **类名**: 大驼峰 (PascalCase) - `ConfigManager`, `SerialThread`
- **函数/方法名**: 小写下划线 (snake_case) - `load_config()`, `send_data()`
- **变量名**: 小写下划线 (snake_case) - `tool_config`, `serial_port`
- **常量**: 全大写下划线 (UPPER_CASE) - `REGISTERED_TOOLS`, `MAX_RETRY`
- **私有成员**: 单下划线前缀 - `_internal_method()`, `_private_var`

### 文档字符串

```python
def execute_command(self, command, context, callback=None):
    """执行特殊指令

    Args:
        command (str): 指令字符串，如 "delay:500"
        context (dict): 执行上下文，包含必要信息
        callback (callable, optional): 完成后的回调函数

    Returns:
        bool: 执行成功返回 True，失败返回 False

    Raises:
        ValueError: 指令格式错误
    """
    pass
```

### 注释规范

```python
# 单行注释：简短说明
x = 5  # 行尾注释

# 多行注释：详细说明
# 第一行
# 第二行
```

### 代码格式

- **缩进**: 4 个空格 (不使用 Tab)
- **行宽**: 建议不超过 100 字符
- **空行**: 类定义之间 2 个空行，方法之间 1 个空行
- **导入顺序**: 标准库 → 第三方库 → 本地模块

```python
# 标准库
import os
import sys

# 第三方库
from PyQt5.QtWidgets import QWidget
import serial

# 本地模块
from utils.ui_utils import UIUtils
from managers.config_manager import ConfigManager
```

---

## 调试技巧

### 1. 启用控制台输出 (开发模式)

修改 [main.spec](../main.spec):

```python
exe = EXE(
    ...
    console=True,  # 改为 True，显示控制台
    ...
)
```

### 2. 添加调试日志

```python
import logging

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# 使用日志
logger.debug("调试信息")
logger.info("普通信息")
logger.warning("警告信息")
logger.error("错误信息")
```

### 3. 串口调试

```python
# 打印发送的数据
def write_data(self, data):
    print(f"[发送] {data.hex()}")  # 十六进制格式
    self.serial_port.write(data)

# 打印接收的数据
def on_data_received(self, data):
    print(f"[接收] {data.hex()}")
    # ...
```

### 4. UI 调试

```python
# 打印控件树
def print_widget_tree(widget, level=0):
    print("  " * level + widget.__class__.__name__)
    for child in widget.children():
        if isinstance(child, QWidget):
            print_widget_tree(child, level + 1)

# 使用
print_widget_tree(self)
```

### 5. 性能分析

```python
import time

def measure_time(func):
    """测量函数执行时间的装饰器"""
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"{func.__name__} 耗时: {(end - start) * 1000:.2f} ms")
        return result
    return wrapper

@measure_time
def slow_function():
    # ...
```

---

## 性能优化

### 1. 避免频繁更新 UI

**问题**: 高速接收数据时频繁更新 QTextBrowser 导致卡顿

**解决**:

```python
class OutputManager:
    def __init__(self):
        self.buffer = []
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.flush_buffer)
        self.update_timer.start(100)  # 每 100ms 刷新一次

    def append_output(self, text, source):
        """添加到缓冲区"""
        self.buffer.append((text, source))

    def flush_buffer(self):
        """批量刷新"""
        if self.buffer:
            for text, source in self.buffer:
                self.text_browser.append(text)
            self.buffer.clear()
```

### 2. 优化大量命令加载

**问题**: 加载数千条命令时界面卡顿

**解决**:

```python
def load_commands(self, commands):
    """批量加载命令"""
    self.command_table.setUpdatesEnabled(False)  # 禁用更新

    for command in commands:
        self.command_table.add_command_row(...)

    self.command_table.setUpdatesEnabled(True)  # 重新启用更新
```

### 3. 减少配置文件写入频率

**问题**: 频繁保存配置导致磁盘 I/O 压力

**解决**:

```python
class ConfigManager:
    def __init__(self):
        self.save_timer = QTimer()
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save_config)
        self.dirty = False

    def mark_dirty(self):
        """标记配置已修改"""
        self.dirty = True
        self.save_timer.start(1000)  # 1 秒后保存

    def _save_config(self):
        """实际保存"""
        if self.dirty:
            self.save_config(self.config)
            self.dirty = False
```

---

## 已知问题

### 1. 高速连续发送时偶尔丢数据

**原因**: 串口缓冲区溢出

**临时解决**: 增加发送间隔

**计划修复**: 添加发送队列和流控

### 2. 某些串口设备断开后无法重连

**原因**: 串口资源未正确释放

**临时解决**: 重启应用

**计划修复**: 改进串口关闭逻辑

### 3. Windows 下打包后文件路径问题

**原因**: PyInstaller 临时目录问题

**解决**: 使用 `resource_path()` 函数

```python
def resource_path(relative_path):
    """获取资源文件的绝对路径"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 临时目录
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)
```

### 4. Linux 下串口权限问题

**原因**: 用户不在 dialout 组

**解决**:

```bash
sudo usermod -a -G dialout $USER
# 注销后重新登录
```

---

## 测试

### 单元测试 (计划中)

```python
import unittest

class TestSpecialCommandManager(unittest.TestCase):
    def setUp(self):
        self.manager = SpecialCommandManager(...)

    def test_delay_command(self):
        """测试 delay 指令"""
        result = self.manager.parse_command("delay:500")
        self.assertEqual(result, (SpecialCommandType.DELAY, 500))

    def test_invalid_command(self):
        """测试无效指令"""
        with self.assertRaises(ValueError):
            self.manager.parse_command("invalid:xxx")
```

### 集成测试 (计划中)

```python
class TestSerialCommunication(unittest.TestCase):
    def test_send_receive(self):
        """测试发送接收"""
        # 使用虚拟串口进行测试
        pass
```

---

## 参考资料

### 官方文档

- [PyQt5 官方文档](https://www.riverbankcomputing.com/static/Docs/PyQt5/)
- [PySerial 官方文档](https://pyserial.readthedocs.io/)
- [Python 官方文档](https://docs.python.org/3/)

### 设计模式

- 单例模式 - ConfigManager
- 观察者模式 - Qt 信号槽机制
- 策略模式 - 特殊指令系统
- 工厂模式 - 工具注册表

### 推荐阅读

- *Design Patterns: Elements of Reusable Object-Oriented Software*
- *Effective Python: 90 Specific Ways to Write Better Python*
- *Qt5 Python GUI Programming Cookbook*

---

## 贡献指南

### 提交代码前检查清单

- [ ] 代码符合命名规范
- [ ] 添加了必要的注释和文档字符串
- [ ] 测试通过 (如有)
- [ ] 更新了 VERSION_HISTORY.md (如有版本变更)
- [ ] 更新了 README.md (如有功能变更)

### Pull Request 流程

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

## 联系方式

如有技术问题或建议，欢迎通过以下方式联系:

- **Issue**: 提交到项目仓库
- **Email**: [待添加]
- **讨论区**: [待添加]

---

<p align="center">
  <i>感谢所有贡献者！</i>
</p>

