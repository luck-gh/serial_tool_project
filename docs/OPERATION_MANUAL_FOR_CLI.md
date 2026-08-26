# GHowe 串口调试助手 CLI 操作手册

适用版本：V1.2.5 Windows EXE

本文档面向直接使用可执行文件的用户，说明命令行模式的启动方法、参数、配置文件、特殊命令支持范围和退出状态。在图形界面中点击左下角 `?`，再选择“CLI 操作手册”即可查看本文档。

## 1. 选择可执行文件

| 可执行文件 | 用途 |
| --- | --- |
| `GHowe_串口调试助手_CLI.exe` | CLI 专用版，适合命令行和自动化任务 |
| `GHowe_串口调试助手.exe` | 通用版，增加 `--cli` 后进入 CLI 模式 |
| `GHowe_串口调试助手_GUI.exe` | GUI 专用版，不用于命令行操作 |

正式发布的文件名可能包含版本后缀，例如 `GHowe_串口调试助手_CLI_v1.2.5.exe`。本文示例为便于阅读省略版本后缀，使用时请替换成实际文件名。

建议先在 EXE 所在文件夹打开 Windows Terminal 或 PowerShell，再执行下面的命令。如果路径或文件名包含空格，请使用双引号。

## 2. 基本语法

CLI 专用版：

```powershell
.\GHowe_串口调试助手_CLI.exe [参数]
```

通用版：

```powershell
.\GHowe_串口调试助手.exe --cli [参数]
```

查看 CLI 专用版帮助：

```powershell
.\GHowe_串口调试助手_CLI.exe --help
```

查看通用版的 CLI 帮助：

```powershell
.\GHowe_串口调试助手.exe --cli --help
```

## 3. 参数说明

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `--cli` | 无 | 让通用版进入 CLI 模式；CLI 专用版不需要此参数 |
| `--config` | 文件路径 | 指定要读取和保存的配置文件 |
| `--port` | 串口名 | 设置并保存串口，例如 `COM3` |
| `--baudrate` | 数字 | 设置并保存波特率，例如 `115200` |
| `--send` | 命令 | 发送一条普通字符串或受支持的特殊命令 |
| `--read-timeout` | 毫秒 | 发送后继续读取响应的时间，默认 `300` 毫秒 |

注意：

- `--read-timeout` 的单位是毫秒，不是秒。例如等待 2 秒应填写 `2000`。
- 只设置 `--port` 或 `--baudrate` 而不使用 `--send` 时，程序会保存设置后退出。
- 一次调用只执行一个 `--send` 入口命令。多步操作应保存为模块，再通过 `SendMode` 执行。

## 4. 配置文件

CLI 与 GUI 使用相同格式的配置文件。建议先在 GUI 中设置串口参数、结尾标识符、输出显示和命令模块，再让 CLI 使用这份配置。

```powershell
.\GHowe_串口调试助手_CLI.exe --config ".\serial_config.json" --send "AT"
```

使用配置文件时请注意：

- `--port` 和 `--baudrate` 会覆盖配置中的对应值，并保存到该配置文件。
- 未指定 `--config` 时，程序会根据当前 EXE 文件名使用默认配置文件。
- 通用版、GUI 专用版、CLI 专用版或带版本后缀的 EXE，默认配置文件名可能不同。
- 如需 GUI 与 CLI 稳定共用同一份设置，请始终显式指定同一个 `--config` 文件。
- 修改或替换配置文件前，建议先复制一份作为备份。

## 5. 常用示例

### 5.1 发送普通字符串

```powershell
.\GHowe_串口调试助手_CLI.exe --port COM3 --baudrate 115200 --send "AT"
```

普通字符串会按照配置追加结尾标识符。

### 5.2 发送十六进制数据

```powershell
.\GHowe_串口调试助手_CLI.exe --port COM3 --send "SendHex:AA 55 01 0D 0A"
```

### 5.3 修改并保存波特率

```powershell
.\GHowe_串口调试助手_CLI.exe --config ".\serial_config.json" --send "BaudRate:9600"
```

### 5.4 执行模块

```powershell
.\GHowe_串口调试助手_CLI.exe --config ".\serial_config.json" --send "SendMode:初始化"
```

也可以使用模块入口写法：

```powershell
.\GHowe_串口调试助手_CLI.exe --config ".\serial_config.json" --send "mode:初始化"
```

程序会在配置中查找该模块，并依次执行其中已启用的命令。

### 5.5 发送后读取响应

```powershell
.\GHowe_串口调试助手_CLI.exe --port COM3 --send "AT" --read-timeout 2000
```

该命令发送后继续读取约 2000 毫秒，并把收到的数据输出到终端。

### 5.6 只更新串口设置

```powershell
.\GHowe_串口调试助手_CLI.exe --config ".\serial_config.json" --port COM5 --baudrate 115200
```

未提供 `--send` 时不会发送数据，只更新配置并退出。

## 6. 特殊命令支持范围

### 6.1 直接作为 `--send` 使用

| 命令 | 支持情况 | 说明 |
| --- | --- | --- |
| 普通字符串 | 支持 | 按配置追加结尾标识符 |
| `SendHex` | 支持 | 发送十六进制字节 |
| `BaudRate` | 支持 | 修改并保存波特率 |
| `ComPort` | 支持 | 切换并保存串口 |
| `SetEndlog` | 支持 | 修改并保存结尾标识符 |
| `SendMode` | 支持 | 执行配置中的模块 |
| `mode` | 支持 | 作为执行同名模块的入口 |
| `delay` | 不作为独立入口 | 只在模块执行过程中生效 |
| `modeend` | 不作为独立入口 | 模块结构标记 |
| `StopContinuous` | 不支持 | 仅用于 GUI 连续发送 |
| `FirmwareDownload` | 不支持 | 仅用于 GUI 的固件下载流程 |

### 6.2 模块执行过程中

- 普通字符串按配置发送。
- `SendHex`、`BaudRate`、`ComPort` 和 `SetEndlog` 按对应含义执行。
- `delay` 会暂停指定毫秒数，同时继续接收串口数据。
- `SendMode` 可以调用另一个已定义模块；检测到模块循环调用时会报错并退出。
- `mode`、`modeend` 和 `StopContinuous` 作为结构或 GUI 控制命令跳过。
- 遇到 `FirmwareDownload` 会报错并以非零退出码结束。

### 6.3 特殊命令格式

| 命令 | 示例 |
| --- | --- |
| 十六进制发送 | `SendHex:AA 55 01` |
| 修改波特率 | `BaudRate:115200` |
| 切换串口 | `ComPort:COM5` |
| 设置结尾 | `SetEndlog:\r\n` |
| 执行模块 | `SendMode:初始化` |
| 模块内延时 | `delay:500` |

命令名称与冒号之间不要增加空格。十六进制参数可以使用空格分隔字节。

## 7. 输出与退出码

CLI 会把运行信息、串口响应和错误输出到终端。输出来源、时间戳和颜色会沿用所选配置文件中的相关设置。

- `0`：执行成功，或配置更新成功。
- 非 `0`：参数、配置、串口、发送或命令执行失败。

自动化任务应同时检查终端信息和进程退出码。退出码为 `0` 只表示命令执行过程未报错，不代表设备响应内容一定符合业务预期。

如果程序成功退出但终端没有预期信息，请检查配置中的发送、接收、系统和错误来源是否被关闭。

## 8. 自动化示例

```powershell
& ".\GHowe_串口调试助手_CLI.exe" --port COM3 --send "AT" --read-timeout 1000
if ($LASTEXITCODE -ne 0) {
    Write-Error "串口命令执行失败"
}
```

```powershell
& ".\GHowe_串口调试助手.exe" --cli --config ".\serial_config.json" --send "SendMode:初始化"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
```

## 9. 常见问题

### CLI 启动后没有图形窗口

这是正常现象。CLI 专用版只在终端中运行。需要图形界面时，请双击通用版或 GUI 专用版，并且不要增加 `--cli`。

### 找不到串口

- 使用 Windows 设备管理器确认串口名称。
- 使用 `--port COMx` 明确指定串口。
- 确认串口没有被其他软件占用。
- 重新插拔设备后再次确认 COM 号。

### 找不到模块

- 确认 `--config` 指向正确的配置文件。
- 在 GUI 中确认模块由 `mode:模块名` 和 `modeend:0` 正确定义。
- 确认 `SendMode` 参数与模块名完全一致。
- 确认模块中至少有一条已启用的命令。

### 读取响应时间不符合预期

`--read-timeout` 使用毫秒。`300` 表示约 0.3 秒，`2000` 表示约 2 秒。

### 中文输出乱码

Windows Terminal 和新版 PowerShell 通常可直接显示中文。如果旧版终端出现乱码，可先执行：

```powershell
chcp 65001
```

### 固件下载命令执行失败

CLI 当前不支持 `FirmwareDownload`。请在 GUI 的发送编辑区中使用该特殊命令。

## 10. 与 GUI 的主要差异

- CLI 适合自动化任务、批量测试和单次命令执行。
- GUI 提供查找、替换发送、响应校验、连续发送、循环发送、模板编辑、远程控制和固件下载流程。
- GUI 的替换发送和响应匹配不会在 CLI 中执行。
- CLI 与 GUI 可以通过显式指定同一个配置文件，共享串口基础设置、命令列表和模块定义。
