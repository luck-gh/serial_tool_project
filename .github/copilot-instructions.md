# GHowe 串口工具 - AI 编程指南

## 架构概述
这是一个基于 PyQt5 的串口调试 GUI 应用程序，具有模块化架构：
- **main.py**：应用程序入口，QApplication 设置（Fusion 样式，Consolas 字体）
- **main_window.py**：主 QMainWindow 类，协调所有组件
- **core/serial_thread.py**：QThread 用于非阻塞串口通信
- **managers/**：业务逻辑分离（ConfigManager、OutputManager、SpecialCommandManager）
- **widgets/**：可重用 UI 组件（CommandTableWidget、CustomTextBrowser 等）
- **dialogs/**：特定功能的模态对话框
- **utils/ui_utils.py**：共享工具、枚举和 resource_path() 函数，支持开发/打包环境兼容性

## 关键模式和约定

### 配置管理
- 基于 JSON 的配置，具有自动迁移（见 ConfigManager.load_config()）
- 配置文件名为 `{exe_name}_config.json`（支持多个工具实例）
- 退出时自动保存窗口状态、命令和设置
- 使用 `config_manager.get/set_config_value()` 访问设置

### 串口通信
- 始终使用 SerialThread 进行 I/O 操作（绝不阻塞主线程）
- 连接信号：`data_received(bytes)` 和 `error_occurred(str)`
- 打开后立即重置输入缓冲区，防止垃圾数据
- 优雅处理端口忙错误

### 特殊命令系统
命令文本中以 `#` 开头的特殊命令：
- `#delay <ms>`：暂停执行指定毫秒数
- `#sendhex <hex>`：发送原始十六进制字节（例如 `AA BB CC`）
- `#baudrate <rate>`：动态更改串口波特率
- `#setendlog <ending>`：设置命令结尾（\\r\\n 等）
- `#sendmode <module>`：发送特定命令模块
- `#mode <name>`：定义命令模块分组

### UI 组件
- 使用 `BaseWidgetMixin` 实现通用组件功能
- `CollapsibleGroupBox` 用于组织设置面板
- `CustomTextBrowser` 带有颜色编码的输出源（发送/接收/系统/错误）
- `CommandTableWidget` 管理带有复选框、十六进制输入验证的命令行
- 所有组件通过 Colors 枚举支持深色/浅色主题

### 资源处理
- 使用 ui_utils 中的 `resource_path()` 处理所有文件路径
- 支持开发环境和 PyInstaller 打包环境
- 图标和资源存储在 `resources/` 目录中

### 命令表管理
- 命令以 [enabled, text, comment] 元组形式存储在配置中
- 模块系统对命令进行分组（存储在 OrderedDict 中）
- 批量操作：全选、反选、删除选中项
- 通过 CSV 进行模板导入/导出，具有特殊命令解析

### 构建和打包
- PyInstaller spec 文件（`main.spec`）处理资源打包
- 环境变量 `BUNDLE_NUMBER_CONVERTER`, `BUNDLE_BIN_HEX_CONVERTER`, `BUNDLE_FIRMWARE_DOWNLOADER` 控制三个工具项目是否随主程序打包
- 构建命令：`pyinstaller main.spec`
- GUI 应用程序隐藏控制台（`console=False`）

### 线程和信号
- 串口 I/O 在后台线程中运行，防止 UI 冻结
- 使用 QTimer 进行延迟，而不是 time.sleep()
- 信号-槽模式实现线程安全通信
- 连续发送使用基于定时器的执行循环

### 错误处理
- 用户友好的中文错误消息
- 使用 ERROR 源类型将错误记录到输出
- 优雅降级（例如端口未找到、无效十六进制）

### 代码风格说明
- 全程使用中文注释和字符串
- 枚举类实现类型安全（OutputSource、SpecialCommandType）
- Mixin 模式实现共享组件行为
- 事件过滤器实现自定义输入处理（例如组合框滚轮事件）

## 开发工作流
1. 使用 `python -m serial_tool_project.main` 运行
2. 配置自动保存到 `{exe_name}_config.json`
3. 使用 `pyinstaller main.spec` 构建
4. 使用环回或真实设备测试串口连接

## 常见集成点
- 为新命令类型扩展 SpecialCommandManager
- 将组件添加到 main_window.py 的 init_ui() 方法
- 在主窗口的菜单/操作系统中注册新对话框
- 使用 OutputManager 实现跨组件的一致日志记录
