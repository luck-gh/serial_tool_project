# GHowe 串口调试助手 CLI 操作手册

本文档说明命令行模式的启动方式、参数、配置文件、特殊命令支持范围和退出状态。

## 1. 启动入口

| 入口 | 用途 |
| --- | --- |
| `python main.py` | 默认启动 GUI；增加 `--cli` 后运行 CLI |
| `python gui_main.py` | 只启动 GUI |
| `python cli_main.py` | 只运行 CLI |

打包后对应：

| 可执行文件 | 用途 |
| --- | --- |
| `GHowe_串口调试助手.exe` | 通用版，默认 GUI，支持 `--cli` |
| `GHowe_串口调试助手_GUI.exe` | 纯 GUI 版 |
| `GHowe_串口调试助手_CLI.exe` | 纯 CLI 版 |

## 2. 基本语法

源码通用入口：

```powershell
python main.py --cli [参数]
```

源码 CLI 入口：

```powershell
python cli_main.py [参数]
```

通用 EXE：

```powershell
.\GHowe_串口调试助手.exe --cli [参数]
```

CLI EXE：

```powershell
.\GHowe_串口调试助手_CLI.exe [参数]
```

## 3. 参数

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `--cli` | 无 | 通用入口切换到 CLI 模式 |
| `--config` | 路径 | 指定 JSON 配置文件 |
| `--port` | 串口名 | 覆盖配置中的串口，例如 `COM3` |
| `--baudrate` | 数字 | 覆盖配置中的波特率，例如 `115200` |
| `--send` | 字符串 | 发送一条普通命令或受支持的特殊命令 |
| `--read-timeout` | 秒 | 发送后继续读取串口数据的时长 |

查看帮助：

```powershell
python cli_main.py --help
```

## 4. 配置文件

CLI 与 GUI 使用相同格式的 JSON 配置。建议先在 GUI 中完成串口参数、命令模板和模块配置，再让 CLI 使用该配置文件。

```powershell
python cli_main.py --config .\config\serial_config.json --send "AT"
```

命令行中的 `--port` 和 `--baudrate` 优先于配置文件中的对应值。

## 5. 常用示例

### 5.1 发送普通字符串

```powershell
python cli_main.py --port COM3 --baudrate 115200 --send "AT"
```

普通字符串会按照配置追加结尾标识符。

### 5.2 发送十六进制数据

```powershell
python cli_main.py --port COM3 --send "SendHex:AA 55 01 0D 0A"
```

### 5.3 修改波特率

```powershell
python cli_main.py --config .\config.json --send "BaudRate:9600"
```

一次 CLI 调用只执行 `--send` 指定的一个入口命令。需要执行多步流程时，建议把命令定义为模块，再发送 `SendMode:模块名`。

### 5.4 执行模块

```powershell
python cli_main.py --config .\config.json --send "SendMode:初始化"
```

也可以使用：

```powershell
python cli_main.py --config .\config.json --send "mode:初始化"
```

CLI 会在配置中查找模块并依次执行其中的有效命令。

### 5.5 发送后读取响应

```powershell
python cli_main.py --port COM3 --send "AT" --read-timeout 2
```

该命令发送后继续读取约 2 秒，并把收到的数据输出到终端。

## 6. 特殊命令支持

### 6.1 直接作为 `--send` 使用

| 命令 | 支持情况 | 说明 |
| --- | --- | --- |
| 普通字符串 | 支持 | 按配置追加结尾标识符 |
| `SendHex` | 支持 | 发送十六进制字节 |
| `BaudRate` | 支持 | 修改波特率 |
| `ComPort` | 支持 | 切换串口 |
| `SetEndlog` | 支持 | 修改结尾标识符 |
| `SendMode` | 支持 | 执行配置中的模块 |
| `mode` | 支持 | 在 CLI 中作为执行同名模块的入口 |
| `delay` | 不作为独立入口 | 仅在模块执行过程中生效 |
| `modeend` | 不作为独立入口 | 模块结构标记 |
| `StopContinuous` | 不作为独立入口 | GUI 连续发送控制命令 |

### 6.2 模块执行过程中

- 普通字符串按配置发送。
- `SendHex`、`BaudRate`、`ComPort` 和 `SetEndlog` 按对应语义执行。
- `delay` 会暂停指定毫秒数。
- `SendMode` 可以调用另一个已定义模块。
- `mode`、`modeend` 和 `StopContinuous` 作为结构或 GUI 控制命令跳过。

## 7. 特殊命令格式

| 命令 | 示例 |
| --- | --- |
| 十六进制发送 | `SendHex:AA 55 01` |
| 修改波特率 | `BaudRate:115200` |
| 切换串口 | `ComPort:COM5` |
| 设置结尾 | `SetEndlog:\r\n` |
| 执行模块 | `SendMode:初始化` |
| 模块内延时 | `delay:500` |

命令名称不建议混用额外空格。十六进制参数可使用空格分隔字节。

## 8. 输出与退出码

CLI 会把运行信息、串口响应和错误输出到终端。脚本或自动化任务应同时检查进程退出码。

- `0`：执行成功。
- 非 `0`：参数错误、配置错误、串口打开失败、发送失败或命令执行失败。

## 9. 自动化示例

```powershell
python cli_main.py --port COM3 --send "AT" --read-timeout 1
if ($LASTEXITCODE -ne 0) {
    Write-Error "串口命令执行失败"
}
```

```powershell
.\GHowe_串口调试助手.exe --cli --config .\config.json --send "SendMode:初始化"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
```

## 10. 常见问题

### CLI 启动后没有 GUI

`cli_main.py` 和 CLI EXE 本来就不显示 GUI。需要 GUI 时运行 `main.py`、`gui_main.py` 或对应 GUI EXE，并且不要增加 `--cli`。

### 找不到串口

- 使用 Windows 设备管理器确认串口名称。
- 显式传入 `--port COMx`。
- 确认串口没有被其他程序占用。

### 模块不存在

- 确认 `--config` 指向正确配置文件。
- 确认模块由 `mode:模块名` 和 `modeend:0` 正确定义。
- 确认 `SendMode` 参数与模块名完全一致。

### 中文输出乱码

PowerShell 通常可直接显示 UTF-8。如果旧版终端出现乱码，可先执行：

```powershell
chcp 65001
```

## 11. 与 GUI 的差异

- CLI 适合脚本、自动化测试和单次命令执行。
- GUI 提供查找、替换发送、连续发送、循环发送、模板编辑和远程控制。
- 替换发送规则属于 GUI 交互能力，CLI 当前不会应用 GUI 的临时替换发送模式。
- CLI 和 GUI 可以共享串口基础配置、命令列表和模块定义。
