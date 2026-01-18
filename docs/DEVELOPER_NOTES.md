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
├── main.py                          # 应用程序主入口 (38 行)
├── main_window.py                   # 主窗口 UI 和核心逻辑 (~1500 行)
├── main.spec                        # PyInstaller 打包配置
├── main_config.json                 # 应用程序配置文件 (自动生成)
├── resources/                       # 资源文件
│   └── HOWE_LOGO.ico               # 应用程序图标
├── core/                            # 核心功能模块
│   ├── __init__.py
│   ├── README.md
│   └── serial_thread.py            # 串口通信线程
├── managers/                        # 功能管理模块
│   ├── __init__.py
│   ├── README.md
│   ├── config_manager.py           # 配置管理器 (工具注册表)
│   ├── output_manager.py           # 输出管理器 (日志分类过滤)
│   └── special_command_manager.py  # 特殊指令管理器
├── widgets/                         # 自定义 UI 控件
│   ├── __init__.py
│   ├── README.md
│   ├── base_widgets.py             # 基础控件混入类
│   ├── custom_widgets.py           # 自定义控件库
│   └── command_widgets.py          # 命令表格控件
├── utils/                           # 辅助工具和 UI 样式
│   ├── __init__.py
│   ├── README.md
│   └── ui_utils.py                 # UI 工具类、枚举、颜色常量
├── dialogs/                         # 交互对话框
│   ├── __init__.py
│   ├── README.md
│   ├── config_dialog.py            # 配置对话框 (工具设置)
│   └── comment_edit_dialog.py      # 注释编辑对话框
└── docs/                            # 文档目录
    ├── VERSION_HISTORY.md          # 版本更新记录
    ├── COMMAND_LINE_USAGE.md       # 命令行使用指南
    └── DEVELOPER_NOTES.md          # 开发者笔记 (本文档)
```

### 代码统计

- **总代码行数**: ~3,300 行 Python 代码
- **核心模块**: 12 个 Python 文件
- **自定义控件**: 5+ 个
- **管理器模块**: 3 个

---

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
  "last_state": {
    "basic_settings": {...},
    "receive_settings": {...},
    "send_settings": {...},
    "ui_settings": {...},
    "commands": [...]
  }
}
```

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

### 1. core/serial_thread.py

**职责**: 异步串口通信

#### 关键类

```python
class SerialThread(QThread):
    """串口通信线程"""

    # 信号定义
    data_received = pyqtSignal(bytes)  # 接收到数据
    error_occurred = pyqtSignal(str)   # 发生错误

    def __init__(self, port, baudrate, ...):
        self.serial_port = serial.Serial(...)
        self.running = True

    def run(self):
        """线程主循环"""
        while self.running:
            if self.serial_port.in_waiting:
                data = self.serial_port.read(...)
                self.data_received.emit(data)

    def write(self, data):
        """发送数据"""
        self.serial_port.write(data)

    def stop(self):
        """停止线程"""
        self.running = False
        self.serial_port.close()
```

#### 设计要点

- **多线程安全**: 使用 QThread，不阻塞 UI
- **信号机制**: 通过 pyqtSignal 与主线程通信
- **异常处理**: 捕获串口异常，通过信号通知主窗口

### 2. managers/config_manager.py

**职责**: 配置管理、工具注册表、版本兼容

#### 关键方法

```python
class ConfigManager:
    # 工具注册表 (类变量)
    REGISTERED_TOOLS = {...}

    def __init__(self, tool_version, tool_version_date, config_file):
        self.config_file = config_file
        self.load_config()

    def load_config(self):
        """加载配置，自动迁移"""
        # 读取配置文件
        # 检测版本
        # 自动迁移
        # 补充默认值

    def save_config(self, config_data):
        """保存配置"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

    def get_tool_config(self, tool_key):
        """获取工具配置"""
        return self.config.get("tools", {}).get(tool_key, {})

    def update_tool_config(self, tool_key, config):
        """更新工具配置"""
        self.config["tools"][tool_key] = config
        self.save_config(self.config)
```

#### 设计要点

- **工具注册表** - 集中管理所有工具的默认配置
- **版本检测** - 拒绝加载版本过高的配置
- **自动迁移** - 自动补充缺失字段
- **增量保存** - 按需写入文件

### 3. managers/special_command_manager.py

**职责**: 解析和执行特殊指令

#### 关键方法

```python
class SpecialCommandManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager

    def is_special_command(self, command):
        """判断是否为特殊指令"""
        return (command.startswith("delay:") or
                command.startswith("SendHex:") or
                ...)

    def parse_command(self, command):
        """解析特殊指令"""
        if command.startswith("delay:"):
            return ("delay", int(command[6:]))
        elif command.startswith("SendHex:"):
            return ("SendHex", command[8:])
        # ...

    def execute_delay(self, ms, callback):
        """执行延迟指令"""
        QTimer.singleShot(ms, callback)

    def execute_sendmode(self, module_name, context, completion_callback):
        """执行模块跳转"""
        # 递归发送模块内的命令
        # 完成后调用 completion_callback
```

#### 设计要点

- **非阻塞执行** - 使用 QTimer 实现异步
- **回调机制** - 通过回调通知执行完成
- **上下文传递** - 通过 context 参数传递必要信息

### 4. widgets/custom_widgets.py

**职责**: 自定义 UI 控件

#### 关键控件

**CollapsibleGroupBox** - 可折叠分组框

```python
class CollapsibleGroupBox(QWidget):
    """可折叠的 QGroupBox"""

    def __init__(self, title, parent=None):
        self.toggle_button = QPushButton(title)
        self.content_area = QWidget()
        self.collapsed = False

        self.toggle_button.clicked.connect(self.toggle)

    def toggle(self):
        """切换折叠状态"""
        self.collapsed = not self.collapsed
        self.content_area.setVisible(not self.collapsed)
```

**CustomTextBrowser** - 自定义文本浏览器

```python
class CustomTextBrowser(QTextBrowser):
    """支持右键菜单的文本浏览器"""

    def contextMenuEvent(self, event):
        """右键菜单"""
        menu = QMenu(self)
        copy_action = menu.addAction("复制")
        clear_action = menu.addAction("清空")
        # ...
        menu.exec_(event.globalPos())
```

#### 设计要点

- **继承扩展** - 继承 Qt 控件并扩展功能
- **信号槽** - 使用 Qt 信号槽机制
- **样式定制** - 支持自定义样式

### 5. managers/output_manager.py

**职责**: 输出管理、日志分类、过滤

#### 关键方法

```python
class OutputManager:
    def __init__(self, text_browser):
        self.text_browser = text_browser
        self.filters = {
            OutputSource.SEND: True,
            OutputSource.RECEIVE: True,
            OutputSource.SYSTEM: True,
            OutputSource.ERROR: True
        }

    def append_output(self, text, source, show_timestamp=False):
        """添加输出"""
        if not self.filters.get(source, True):
            return  # 过滤掉不显示的输出

        color = self.get_color(source)
        timestamp = self.get_timestamp() if show_timestamp else ""

        self.text_browser.append(f"{timestamp}[{source.value}] {text}")

    def set_filter(self, source, enabled):
        """设置过滤器"""
        self.filters[source] = enabled

    def clear(self):
        """清空输出"""
        self.text_browser.clear()
```

#### 设计要点

- **日志分类** - 按来源分类 (发送、接收、系统、错误)
- **过滤控制** - 支持单独控制各类日志显示
- **颜色标记** - 不同来源使用不同颜色

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
