# 开发者笔记 (Developer Notes)

本文档面向开发者，提供项目架构、设计原则、扩展指南和技术细节。

远程命令支持表、方案取舍、上传协议、独占恢复和验证入口见 [REMOTE_PROTOCOL.md](REMOTE_PROTOCOL.md)。`core/remote_firmware.py` 负责任务生命周期，`core/firmware_download.py` 提供无 Qt 的下载辅助逻辑，`core/firmware_worker.py` 提供 GUI 下载线程。Qt 工作线程须在 `finished` 后销毁/恢复资源，不能用业务结果信号代替线程退出。

内置下载复用同级 `../firmware_downloader_project` 的 `core/downloader.py` 与 `core/protocol.py`，配套核心须提供 `result_status` 和当前 ACK/未知结果契约；CLI 仅收集纯核心，不导入下载窗口。两项目是独立 Git 仓库，不能用父目录的一次提交代替两仓提交；串口发布工作流检出下载仓库默认分支，发布前须先确保配套下载改动已可检出。下载自身的测试与手动构建入口由其 `docs/DEVELOPER_NOTES.md` 维护。

固件工具按钮打开 `dialogs/firmware_download_dialog.py` 的 `ManagedFirmwareDownloadDialog`,复用下载项目的完整 UI,以主窗口所属模态窗口编辑本次参数,不运行独立下载线程. 点击开始后调用 `start_firmware_download_command`,本机参数通过仅本次有效的关键字参数传递,远程不传本机串口和协议参数. 主窗口的 `firmware_log`/`firmware_progress` 信号更新窗口,完成回调在资源恢复后解锁;关闭或 Esc 在任务期间仅请求取消,不阻塞等待或销毁回调接收者. 自动命令入口仍使用持久化默认参数及原进度框. UI 回归入口为 `checks/firmware_dialog_check.py`,无需修改配套下载仓库.

常驻控制台入口为 `core/cli_console.py`，本机与 GUI 后端共用 `core/console_execution.py` 的 `ConsoleExecutor`。`LocalConsoleTransport` 管理本机读线程，GUI transport 在 Qt 线程调用窗口入口；不复制串口拥有者或固件算法。

`core/console_serial.py::write_serial_once`是一次性CLI与本机终端共用的实际写入确认边界,按[pySerial官方write契约](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.write)检查返回计数,硬件OSError或短写抛`OperationUnknown`,只写一次、不补发. 一次性入口另确认flush,模块尾读/close及日志补尾错误合并到原未知消息,最终结果只在日志收尾后输出一次. `MachineSession.write`在stdout具体OSError处停止会话并置退出2,不递归向坏stdout报告fatal;按[Python官方SIGPIPE收尾模式](https://docs.python.org/3/library/signal.html#note-on-sigpipe)在退出前将失败标准流FD接null,避免解释器finalize再次写坏管道. GUI已受理任务仍沿用detach生命周期,不新增取消/重发.
机器模式由 core/cli_machine.py 维护 stdin/stdout 帧、唯一终态和采集窗口,复用同一个 GuiConsoleBackend 与 ConsoleExecutor. 不另建串口传输/响应匹配器. 机器订阅的 RX、状态、执行终态和窗口确认按同一 QLocalSocket 流排序,任务连接保留原单请求生命周期,重复内部确认不输出. 目标漂移失败标记留在活动执行器,CLI 脱离后仍禁止其后续原语;健康 EOF 不取消受理任务.

机器监视使用 GUI 线程 QTimer,并在每次原语/窗口关闭时同步核对. 参考 [Qt 5 QTimer 线程归属](https://doc.qt.io/archives/qt-5.15/qtimer.html#details) 与 [JSON-RPC 请求 ID 关联惯例](https://www.jsonrpc.org/specification#request_object);沿用本项目事件 JSONL,并非新增 JSON-RPC 或额外库. 原始串口无 wire ID,采集窗口不宣称设备因果. 精确字段、下载独占和 EOF 边界只归 [CLI 手册](OPERATION_MANUAL_FOR_CLI.md#机器常驻接口).

新增接口验证入口: python -X faulthandler -m unittest discover -s checks -p machine_session_check.py -v. 测试通过真实 CLI 子进程和真实 GUI IPC,串口构造被禁止,端口枚举替换为空,只用独立临时配置与 mock 数据. 可通过 GHOWE_MACHINE_CANDIDATE 指向隔离候选 EXE,复用同一检查验候选机器帧;不指向实际用户工具或真实配置.


CLI 的 `core/cli_bridge_client.py` 与 GUI 服务端 `core/gui_cli_bridge.py` 共用 `core/cli_protocol.py` 的端点、身份诊断与消息限制；客户端仅依赖 QtCore/QtNetwork，不导入窗口或控件。公共枚举与串口校验分别位于 `core/command_types.py`、`core/serial_config.py`；运行 `python checks/cli_dependency_check.py` 验证 CLI 不依赖 GUI 模块。

`core/gui_config.py` 提供在线配置快照、字段规则、校验和事务应用；`snapshot_state()` 与自动保存共用状态序列化，查询不写盘。字段类型来自现有状态及工具注册表，枚举和范围复用控件与下载核心。使用现有环境可用的成熟 `jsonschema` 校验库，正式运行依赖已统一列入 `requirements.txt` 并接入发布工作流。参考 [JSON Schema 校验库](https://python-jsonschema.readthedocs.io/en/stable/validate/) 与 [RFC 7396](https://www.rfc-editor.org/rfc/rfc7396)；不自行实现通用 schema 校验器。

远程角色下拉框将界面名称与持久化值分离:显示“设备端（连接硬件）”/“控制端（远程操作）”,userData 保留现有 JSON 角色值,快照和 schema 使用 userData,加载/配置事务按 findData 定位. 模式应用后刷新地址候选,两种角色的标签统一为“地址”,与“模式”“端口”的控件保持等宽对齐;角色名称不决定 TCP 协议标识或已建立的连接。保存值契约见 [CLI 手册](OPERATION_MANUAL_FOR_CLI.md)。

配置应用通过 `QSignalBlocker` 阻断设置过程的控件信号，并用 `_loading_state` 抑制自动保存；成功原子保存一次，失败恢复旧内存/控件。动作仍由显式 CLI 操作发起。修改活动连接参数及跨电脑混合修改被拒绝；远程五项串口配置经白名单 `configure` 请求执行，按在途 ID 关联、占用检查和结果回执处理；普通设备请求不保留完成历史，跨帧不保证幂等，未知结果不重发，详见远程协议。

运行期工作 JSON 切换由 `core.gui_config.switch_file` 共用,保留同一个 ConfigManager 对象,事务替换 config/config_file. 候选直接只读解析并复用 schema/prepare/apply_widgets,不调用会创建/迁移保存的 ConfigManager.load_config. 缺省来自启动前控件及注册工具默认值,显式 null 不回退旧工作值. GUI 文件选择全过程抑制 autosave,失败/取消恢复原timer状态;成功丢弃旧dirty,后续统一保存到新路径. 桥接 config_file/endpoint 为启动身份,不随 working_config_file 迁移;basic/remote保留运行设置,无设备动作. 手动顶层SendMode用既有命令代号标记在途,避免Delay期间换表.

新增类使用 Google 风格 docstring：类头描述职责、线程及生命周期，`__init__` 的 `Args:` 逐项说明参数、类型、单位、默认值和约束；配置字典还需逐项说明键。`FirmwareDownloader.__init__` 是完整范例。注释必须与实际失败/未知语义一致，不能把“已入队”描述为“设备完成”。

docstring 与 `#` 行注释互补：对象职责和参数写入 docstring，局部实现意图、分支原因及注意事项保留在对应代码旁；不要为了统一 docstring 风格批量删除有用的行注释。

命令表的加载、追加、单行/多行及文本插入统一使用 `add_command_row`。新行继承邻行字体与行高，首行在 `ensurePolished()` 后按尺寸提示计算；禁止再次乘 devicePixelRatio，否则会重复缩放。150% 缩放下已离线核对上述入口的字体和行高一致。

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
- [验证与手动构建](#验证与手动构建)

---

## 项目架构

### 目录结构

```
serial_tool_project/
├── main.py                         # 兼容入口, 根据参数分流 GUI/CLI
├── gui_main.py                     # GUI 专用入口
├── cli_main.py                     # CLI 专用入口
├── version_info.py                 # GUI/CLI 共用版本信息
├── app_identity.py                 # 可执行文件名和配置文件名转换
├── main_window.py                  # 主窗口 UI 和核心交互逻辑
├── main.spec                       # PyInstaller 打包配置
├── main_config.json                # 应用程序配置文件 (自动生成)
├── resources/                      # 资源文件
│   └── HOWE_LOGO.ico               # 应用程序图标
├── core/                           # 核心功能模块
│   ├── __init__.py
│   ├── cli_runner.py               # CLI 模式入口和命令执行
│   ├── command_executor.py         # GUI/CLI 共用的命令模块解析
│   ├── output_rules.py             # GUI/CLI 共用的输出规则和文案
│   ├── remote_control.py           # 局域网远程串口控制
│   └── serial_thread.py            # 串口通信线程
├── managers/                       # 功能管理模块
│   ├── __init__.py
│   ├── config_manager.py           # 配置管理器 (工具注册表)
│   ├── output_manager.py           # Qt 接收区输出写入器
│   └── special_command_manager.py  # 特殊指令管理器
├── widgets/                        # 自定义 UI 控件
│   ├── __init__.py
│   ├── base_widgets.py             # 基础控件混入类
│   ├── custom_widgets.py           # 自定义控件库
│   └── command_widgets.py          # 命令表格控件
├── utils/                          # 辅助工具和 UI 样式
│   ├── __init__.py
│   └── ui_utils.py                 # GUI 菜单、颜色、资源路径与 CSV 转义
├── dialogs/                        # 交互对话框
│   ├── __init__.py
│   ├── config_dialog.py            # 配置对话框 (日志规则与工具设置)
│   └── comment_edit_dialog.py      # 注释编辑对话框
└── docs/                           # 文档目录
    ├── VERSION_HISTORY.md          # 版本更新记录
    ├── OPERATION_MANUAL_FOR_GUI.md # GUI 操作手册
    ├── OPERATION_MANUAL_FOR_CLI.md # CLI 操作手册
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

命令词法、规范名及转义集中在 `core/command_parser.py`，模块归属由 `core/command_executor.py` 收集。GUI 表格命令由 `SpecialCommandManager` 调度；常驻控制台统一由 `ConsoleExecutor` 执行，两个入口保留各自的交互与生命周期职责。

- GUI 延时使用 Qt 定时器，控制台在工作线程中可取消等待，均不阻塞 GUI 输入。
- `BaudRate` 更新活动句柄；`ComPort` 仅改下一次打开的选择；结束符、HEX 解码及串口参数转换复用现有纯函数。
- SendMode 设备步骤等待实际回执，失败停止后续步骤；入队不等于完成。GUI 完成回调与 Qt 工作线程退出也不能混为一谈。
- 命令能力及独立一次性/控制台差异以 [CLI 手册](OPERATION_MANUAL_FOR_CLI.md#命令与语义) 为准，不在这里复制命令表。

### 3. 配置持久化

**设计目标**: 增量保存，按需更新

采用增量保存策略，只在配置变更时写入文件。

#### 配置文件结构

以下为结构示意，版本/时间仅为示例，省略号不可直接导入：

```text
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

#### GUI 模块依赖关系

```
main.py
   ↓
main_window.py (主窗口)
   ↓
┌──────────────────────────────────────────
│
├─ managers/
│  ├─ config_manager.py (配置管理)
│  ├─ output_manager.py (输出管理)
│  └─ special_command_manager.py (指令管理)
│
├─ widgets/
│  ├─ custom_widgets.py (自定义控件)
│  └─ command_widgets.py (命令表格)
│                                          
├─ core/
│  └─ serial_thread.py (串口线程)
│
├─ dialogs/
│  ├─ config_dialog.py (配置对话框)
│  └─ comment_edit_dialog.py (注释对话框)
│
└─ utils/
   └─ ui_utils.py (UI 工具类)

```

---

## 模块详解

本节替代原来各目录下的 README, 用于集中说明目录职责和每个 Python 文件的职责边界。目录级 README 不再维护, 避免说明分散和过期。

### 顶层入口

| 文件 | 职责 |
|------|------|
| `main.py` | 兼容入口, 根据启动参数进入 GUI 模式或 CLI 模式。 |
| `gui_main.py` | GUI 专用入口, 只启动图形界面, 用于 GUI 专用打包产物。 |
| `cli_main.py` | CLI 专用入口, 只运行命令行模式, 用于 CLI 专用打包产物。 |
| `version_info.py` | 维护 GUI/CLI 入口共用的工具版本和更新时间。 |
| `app_identity.py` | 规范化可执行文件名, 生成默认配置文件名, 自动忽略 `_GUI`/`_CLI` 后缀。 |
| `main_window.py` | GUI 主窗口, 负责界面布局, 串口操作, 远程控制协调, 命令发送流程和状态保存。 |

### core

`core/` 放置可被 GUI 或 CLI 复用的核心能力, 尽量不直接依赖具体界面控件。

| 文件 | 职责 |
|------|------|
| `core/serial_thread.py` | 后台串口线程, 负责串口打开, 读取, 写入, 波特率切换和错误信号上报。 |
| `core/remote_control.py` | 局域网远程控制通信层, 实现设备端和控制端的 TCP JSON 消息交互。 |
| `core/cli_runner.py` | CLI 模式执行器, 负责命令行参数, 配置读取, 串口发送, 模块执行和 CLI 输出。 |
| `core/command_executor.py` | 命令序列解析器, 将 GUI 表格或配置文件中的命令统一解析为可执行模块命令。 |
| `core/command_parser.py` | 命令词法与转义、HEX 和结束符纯转换。 |
| `core/cli_console.py` / `core/console_execution.py` | 交互会话与本机/GUI 共用的控制台执行器。 |
| `core/console_serial.py` / `core/gui_cli_bridge.py` | 本机串口拥有者与 GUI 线程适配；设备资源按入口协调。 |
| `core/cli_protocol.py` / `core/receive_text.py` | 本机端点及身份诊断、增量接收解码、expect 摘要与原始 RX 流式追加。 |
| `core/command_templates.py` / `core/template_worker.py` | 同源模板解析、转义及原子 IO；独立 CLI 同步调用，GUI 按钮与桥接调用窗口异步模板入口。worker 只访问行/配置副本，Qt 线程分块快照、应用及回滚；公开配置查询在事务期间拒绝读取半态。 |
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
| `dialogs/config_dialog.py` | 日志规则与工具配置草稿, 确定时通过共用配置事务一次保存, 取消无副作用。 |
| `dialogs/firmware_download_dialog.py` | 复用固件工具 UI,将开始、取消和结果展示接到主窗口统一下载任务。 |
| `dialogs/comment_edit_dialog.py` | 命令注释编辑对话框, 提供多行注释编辑界面。 |

### utils

`utils/` 放置轻量公共工具, 避免依赖主窗口或管理器。

| 文件 | 职责 |
|------|------|
| `utils/ui_utils.py` | GUI 菜单样式、颜色常量、资源路径及 CSV 文本转义。命令解析位于 `core/command_parser.py`。 |

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

1. 在 [command_parser.py](../core/command_parser.py) 的 `COMMAND_NAMES` 注册规范名；若属于 GUI 特殊指令，再在 [command_types.py](../core/command_types.py) 补对应枚举。不要新增独立的前缀或冒号解析器。
2. GUI 行为在 `SpecialCommandManager.commands` 注册处理函数，调用现有窗口设备入口；模块收集继续复用 `collect_module_commands`。
3. 两种控制台在 `ConsoleExecutor` 增加公共语义，由 transport 适配本机串口或 GUI；独立一次性 CLI 仅在明确支持该能力时补 `cli_runner.py`，不要混淆模式契约。
4. 异步设备操作沿用结果回执或完成回调。明确取消、占用、失败与未知结果语义，不能把请求入队当设备成功。
5. 补相关行为回归及操作手册；参数、单位、默认值与返回值写入 docstring，局部实现意图留在代码旁。

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

### 接收与显示

`SerialThread` 阻塞读取首字节后合并当前缓冲数据；显示仍经 `OutputManager.append_text` 和共享 `OutputRules`。先定位实际瓶颈，再调整接收批次或 UI 刷新，不建立第二套输出策略。

`OutputManager` 在信息产生时执行保留规则,使用有界 deque 保存原事件顺序和独立展示顺序,累计序号不因清空复用. 日志上限使用十进制MB,按 `sys.getsizeof` 累加记录对象及正文、来源、颜色、级别、时间戳、序号字段的内存预算;共享字段逐条计入,不采样进程内存. Qt文档/队列及归档另有开销,不承诺RSS等于该上限;超过上限批量淘汰至75%. 独立收录来源、级别、时间戳及发送快捷联动的产品语义统一见 [GUI手册](OPERATION_MANUAL_FOR_GUI.md#43-显示与保存接收区数据),不得从当前显示反推过去应保留的数据.

接收控件使用 `QPlainTextEdit` 的纯文本布局,保留颜色和菜单,关闭撤销及自动折行. 采用 [Qt官方日志查看器模式](https://doc.qt.io/qt-6/qplaintextedit.html#using-qplaintextedit-as-a-display-widget) 的有界历史思路,字节预算同时约束无换行流. 20ms刷新按每批64K字符/8ms预算推进,长记录分片,显示文本块最多约4096字符,原始记录不插显示换行. 历史淘汰按记录的实际UTF-16区间局部删除,不反复全区重绘;显示筛选变化才从已保留记录分批重绘,不从文件回灌. 残缺行的系统/错误展示仍有独立20ms固定宽限,首个换行、发送和关闭提前排空,原始RX/响应匹配/IPC不等待排版. 回归入口为 `checks/output_display_check.py` 和 `checks/log_stream_check.py`.

`core/archive_buffer.py`维护GUI/CLI共用的纯Python阈值状态机、预算、批次确认及失败另存,`write_batch`检查短写并flush/fsync. `core/log_archive.py`是Qt薄适配,`core/cli_log.py::ThreadArchive`用threading写者并在join后推进同一状态机;普通CLI无Qt事件循环也能收尾. `core/log_settings.py`集中默认值、收录与日志对象schema,管理入口惰性导入现有jsonschema,不导入GUI控件schema. 待写元组/序号/文本的字节预算达到上限80%时交给 `ArchiveBatch(QThread)`,后台使用Python缓冲文件写入并按批次flush/fsync,成功后在finished回调推进 `committed_sequence`;关闭、清空及退出请求强制补写尾部,退出等待finished后才销毁窗口. 批次开始/完成发出changed信号更新主界面状态,不轮询磁盘;文件路径保留在目录快捷按钮的悬停提示中,常规启停不显示文字,尾部写入和失败另存入口始终位于主界面. 在写批次外最多再积累一份上限,跟不上或文件失败显式停存,不无限排队、不自动重试部分写入,未确认内容支持用户另存新文件. 参考 [Python缓冲IO](https://docs.python.org/3/library/io.html#io.BufferedWriter) 和 [fsync](https://docs.python.org/3/library/os.html#os.fsync). 不新增第三方依赖.

日志设置位于 `state.log_settings`,显示时间戳位于 `state.receive_settings.show_timestamp`,空的其他设置分组移除. `SerialTool.log_settings` 与 `_send_display_selected` 保存运行态基础选择. `ConfigDialog` 独立复制日志草稿,以日志/工具页签呈现,确认时用 `gui_config.apply` 一次提交两类补丁,失败回滚且不关闭弹窗,取消无写入. 版本信息为弹窗顶端常驻两列,其下依次为工作JSON及配置页签. 主窗口不保留隐藏日志控件;日志schema独立声明MB范围,主界面只绑定文件保存开关. 目录快捷按钮通过同一gui_config.apply事务立即修改log_directory,弹窗仍使用草稿确认;两个目录入口共用can_change_log_directory门禁,保存开启、批次未完成或失败待另存时禁用. 发送联动的有效勾选通过阻断信号呈现,不写回基础值;schema/config-get输出基础选择,全量及快捷入口为独立字段. 日志设置纳入 `gui_config` 标量绑定/schema和配置事务;仅事务保存成功后同步运行态及裁剪历史,失败回滚不丢历史或开启文件. CLI协议时刻由事件入口独立生成,GUI不保留某条时间不改变协议字段. 独立CLI自动日志及显式Log共用新日志规则,原始RX/机器JSONL保持独立. 终端只保留归档缓冲,不保留GUI显示历史;桥接自动归档由GUI拥有,显式Log仍可保存本机会话. 日志管理由cli_log_commands及控制台LogGet/Check/Apply/Status/Recover提供,GUI复用配置事务和SerialTool.recover_log_archive;恢复结果只在finished后返回. 独立管理严格解析已有配置,通过ConfigManager的config_data快照入口复用原子保存,查询不触发启动创建/迁移. 回归入口为checks/cli_log_check.py.

全局替换默认模板由 `SerialTool.DEFAULT_GLOBAL_REPLACEMENT_RULE` 维护. `ReplacementRuleDialog` 对没有已保存规则且提供默认模板的入口自动预填草稿,复用默认按钮的超时取值;自定义规则和不提供模板的行规则不变,确认才提交,取消不影响运行态. 默认响应同时引用原命令的通道、地址和值,使用完整读回行结束避免分片前缀误通过;远程校验仍从成功写入回执开始. 回归入口为 `checks/replacement_defaults_check.py` 和 `checks/remote_capability_check.py` 的 `CommandChecks`.

### 批量命令加载

导入替换复用同一行号的现有控件,`replace_command_row` 通过 `QSignalBlocker` 更新字段并重置搜索/校验/规则样式. 一次 `setRowCount` 调整行结构,空槽在后续分块中由 `add_command_row` 安装控件,不再逐行插入结构;字体/行高取已填邻行,普通追加/插入仍沿用原入口. 空槽期间配置查询拒绝半态,主界面及替换模式快捷入口共用控件禁用状态. 保留分块更新、旧表快照、取消/保存失败回滚和原子保存,首次应用时只解绑旧待校验项的行控件引用,其匹配、统计和诊断继续完成. 批量期间阻断表格信号并使用 `setUpdatesEnabled(False)`,结束时恢复;不重复应用字体、行高或设备像素比. 同规模替换可减少控件创建,预分配仅减少行结构变更,空表及大量新增行仍有实际控件创建成本,不保证总耗时固定改善.

### 配置写入

800ms 防抖、30 秒 dirty 兜底及退出保存由 `SerialTool` 协调，初始化恢复期间抑制自动保存；`ConfigManager` 负责原子写入。不要给纯配置管理器再添加 GUI 定时器。

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

## 验证与手动构建

常规开发只运行与改动相关的检查；纯文档调整核对参数、链接及示例语法即可。默认由用户手动构建，只有用户明确要求或必须验证打包行为时才构建 EXE；打包命令集中在 [README](../README.md#打包与入口)。

从 `serial_tool_project` 目录按需选择：

```powershell
python -m unittest discover -s checks -p 'cli_dependency_check.py'
python -m unittest discover -s checks -p 'console_check.py'
python -X faulthandler -m unittest discover -s checks -p '*check.py'
```

`checks/` 已覆盖解析、GUI 桥接、独立控制台、在线配置、远程认证及回环下载；使用临时配置、假串口和本机回环，不把结果当真实设备或跨 Windows 用户验收。Qt 测试先停止线程、排空回调再销毁对象，子进程管道须持续读取。

[`checks.remote_download_check.TransportTests.test_client_stop_during_run_finally_cannot_escape_qthread`](../checks/remote_download_check.py) 在隔离子进程用同步屏障覆盖 `run` 退出与 `stop` 同时关闭连接：先捕获并清空共享 socket，再关闭捕获的句柄。Python 异常若逃出 `QThread.run`，PyQt 默认会通过 Qt fatal 终止进程；不能仅凭 Windows `0xC0000409` 判断内存越界。可用 `python -m unittest checks.remote_download_check.TransportTests -v` 单独运行相关回归；检查保留默认异常处理和真实退出码，仅在测试子进程内禁止错误对话框，不改变产品或系统设置。

源码检查与产物验证分开：确需构建时使用安装了 `requirements.txt` 的同一 Python，并在隔离输出目录检查实际 EXE 的入口和依赖。`main.spec` 排除未用的 Pygments/requests 可选集成；CLI 保留 QtCore/QtNetwork，不引入 QtWidgets/QtGui。产物或包大小验证记录属于当次验收，不用固定数字描述长期契约。

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
- **Email**: 844396800@qq.com
---

<p align="center">
  <i>感谢所有贡献者！</i>
</p>
