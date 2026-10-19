# GHowe 串口调试助手 CLI 操作参考

适用版本：V1.2.6。面向 Windows EXE 与自动化 Agent；源码入口见末尾。请在 EXE 所在目录打开 PowerShell/Windows Terminal。路径含空格时加引号。文中 `D:\app\...` 均为示例路径，使用时替换为实际路径。

## 快速开始

```powershell
.\GHowe_串口调试助手_CLI.exe --port COM3 --baudrate 115200 --send "AT"
.\GHowe_串口调试助手_CLI.exe --port COM3 --send "AT" --expect "OK" --read-timeout 2000
.\GHowe_串口调试助手_CLI.exe --console
.\GHowe_串口调试助手_CLI.exe --via-gui --gui-action status --config "D:\app\gui_config.json"
```

通用版把 `GHowe_串口调试助手_CLI.exe` 换成 `GHowe_串口调试助手.exe --cli`。标准文件名会去掉结尾 `_GUI`/`_CLI`，在当前工作目录推导同名配置；源码入口、重命名 EXE 或不同工作目录可能得到不同路径。桥接始终显式指定 GUI 启动时用于定位桥接的配置绝对路径;运行期切换工作 JSON 后仍使用该原路径。

## 启动模式与参数

| 入口 | 行为 |
| --- | --- |
| 一次性 `--send` | 独立操作本机串口；加 `--via-gui` 后由匹配 GUI 执行。一次只接受一个命令。 |
| `--console` / `-i` | 常驻提示符，启动时按配置尝试 `Open`。按同一 Windows 用户和配置路径发现 GUI：找到则桥接，否则独立；发现/握手错误退出 2，不回退独立。端口打开失败仍留在未连接会话，可设置 `ComPort`/`BaudRate` 后 `Open` 重试。 |
| `--via-gui --console --machine` | UTF-8 JSONL 常驻接口,不自动 Open,仅连接显式绝对配置对应的已连接空闲 GUI;任务与 RX 窗口契约见[机器常驻接口](#机器常驻接口). |
| `--gui-action` | 管理已运行的 GUI；隐含桥接。 |

`--via-gui` 强制使用 GUI，失败不接管串口：连接前的端点错误退出 2；一次性请求已发出后断连/超时退出 3（结果未知）；桥接控制台断连退出 2。桥接不接受 `--port`/`--baudrate`，改用 `--send 'ComPort:...'` 或 `BaudRate` 修改 GUI 所持配置。

GUI 正常关闭或异常退出时,桥接控制台报告错误并自动退出,不再等待键盘输入;任务连接单独异常中断也会退出.
远程连接意外中断时,在线 GUI、控制台订阅者及有在途请求的一次性 CLI 接收同一错误文案和时间;
已经发出的操作若无法确认结果,最终为 unknown,不得自动重发.桥接仍在线时,远程失联不会强制关闭交互控制台,
但受影响的任务会终止后续步骤.主动本地停止远程连接不报告意外断连,在途结果仍按实际状态终结.
正常一次性请求结束和 Quit 不当作 GUI 连接错误;已经退出的一次性 CLI 无法接收未来事件,GUI 自身退出后也无法继续显示错误.
无可观察 TCP 错误的静默网络故障仍受既有传输和任务超时约束,没有新增心跳、自动重连或重发.

| 参数 | 值/默认 | 语义与约束 |
| --- | --- | --- |
| `--cli` | 开关 | 通用版进入 CLI；CLI 专用 EXE 不需要。 |
| `--config` | 路径 | 独立模式选择配置；省略时按 EXE 名称推导。桥接模式用于定位 GUI 启动时的端点，运行期切换工作 JSON 后仍使用原路径。 |
| `--port`、`--baudrate` | COM 名、整数 | 独立模式修改并保存；只提供这两项时保存后退出。桥接禁止使用。 |
| `--send` | 命令 | 一次性发送/执行；与 `--console` 互斥。 |
| `--console`、`-i` | 开关 | 启动交互控制台；可加 `--via-gui`、`--config`。`--port`/`--baudrate` 只限独立后端，发现 GUI 后拒绝使用。 |
| `--machine` | 开关 | 须与 `--console --via-gui --config` 同用,配置须绝对路径;通过 stdin/stdout 交换机器帧,不使用提示符或独立串口回退. |
| `--import-template`、`--export-template` | 文件路径 | 替换/导出命令表，不打开串口；与 `--send`、`--console` 和其它管理/设置参数分次执行，可加 `--via-gui`、`--config`。 |
| `--save-received` | 文件路径 | 与 `--send` 或 `--console` 组合，追加当前运行实际接收的原始 RX；不与管理动作组合。 |
| `--via-gui` | 开关 | 强制桥接；失败语义见上文及退出码表。 |
| `--expect` | 正则 | 仅与 `--send` 同用；在接收窗口累计解码 UTF-8 后匹配。匹配 0、超时未匹配 4、无效正则 2。 |
| `--read-timeout` | 0–60000 ms；默认 300 | 一次性命令发送后的接收观察窗口，独立和桥接均适用；`--expect` 未匹配时等满窗口。 |
| `--gui-action` | 下表动作 | 操作已运行 GUI；隐含 `--via-gui`。 |
| `--gui-timeout` | 秒；默认 600 | 等待桥接任务最终结果；超时/断连的未知结果不得自动重试。 |
| `--config-patch` | JSON 文件或 `-` | 与 `config-check`/`config-apply` 配合；`-` 从 stdin 读取。 |
| `--log-action` | get/check/apply/status/recover | 独立日志配置或当前GUI归档管理;加`--via-gui`操作已运行窗口,与其他操作分次执行. |
| `--log-patch` | JSON文件或`-` | check/apply的日志对象Merge Patch,不包裹state/log_settings. |
| `--log-recover` | 全新文件路径 | recover显式另存未确认批次,不覆盖/追加已有文件. |
| `--remote-host` | 设备端地址 | `remote-connect` 快捷参数；省略沿用 GUI 配置，`remote-listen` 不接受。 |
| `--remote-port` | 端口 1–65535 | `remote-connect`/`remote-listen` 参数；省略沿用 GUI 配置。 |
| `--remote-password` | 口令 | 远程快捷参数；省略沿用 GUI 配置，不在状态结果回显。 |

## 命令与语义

命令名大小写不敏感；帮助、错误、回显和日志用规范驼峰名。独立一次性 `--send` 支持普通文本、`SendHex`、`BaudRate`、`ComPort`、`SetEndlog`、`SendMode` 和 `Mode`。`delay` 仅在模块中生效；`modeend` 是模块结构标记。独立一次性 `--send` 不支持 `FirmwareDownload`/`StopContinuous`；加 `--via-gui` 时由 GUI 执行，`FirmwareDownload` 和 `SendMode` 可走 GUI 路径。

独立一次性发送按实际`write`字节数及`flush`结果确认工具传输完成,不代表DUT执行成功. 短写(包括0字节)、写入超时/异常或排空失败返回唯一`result`且`success=false,status=unknown`,退出3,不补发剩余字节、不继续模块后续命令、不生成成功TX回显. 同时发生尾部接收、关闭串口或日志补尾错误时,在同一结果消息中附带详情,保留原设备未知状态;即使先前RX已匹配也不能覆盖该未知结果.

寄存器读写等设备指令仍按设备协议发送，本工具不定义寄存器命令。独立一次性 `SendHex` 不追加结束符；GUI 和控制台的 `SendHex` 会追加配置结束符。

| 控制台命令 | 语义 |
| --- | --- |
| 普通文本 | UTF-8 发送并追加配置结束符；不走 GUI 编辑器专属替换/响应校验。 |
| `Mode:名称`、`SendMode:名称` | 执行配置模块，别名等价；循环由 `send_settings.loop_send` 控制。模块间隔用 `continuous_interval`，循环间隔用 `loop_interval`。 |
| `Delay:毫秒` | 等待 0–60000 ms。 |
| `SendHex:字节` | HEX 解码后追加配置结束符。 |
| `BaudRate:值` | 保存并立即应用到活动串口。 |
| `ComPort:端口` | 只保存选择；活动句柄不切换，执行 `Close` 后 `Open` 生效。 |
| `SetEndlog:值` | 设置并保存结束符；支持 `None`、`rn`、`r`、`n` 或对应文本。 |
| `StopContinuous:0` / `:1` | `:0` 结束当前模块轮次，若循环开启则下一轮继续；`:1` 停止后续循环。 |
| `FirmwareDownload:文件` | 独立与桥接控制台复用下载配置与核心；本地可沿用配置的外部工具，远程仅支持内置下载器。设备结果不可确认时为 `unknown`，不自动重试。 |
| `Status`、`Open`、`Close`、`Cancel` | 两后端相同。`Cancel` 请求取消当前任务，已发字节不可撤回；启动时的 Open 幂等。 |
| `ShowHex`、`Timestamp` | 会话级显示设置；无参数或 `:0` 关闭，`:on`/`:off` 明确设置，不写回配置。 |
| `Log:路径`、`Log:off` | 显式追加带来源的UTF-8会话日志;无参数、`:0`或`:off`补尾并停止. 收录遵守`state.log_settings`,与显示筛选/时间戳独立. |
| `LogGet`、`LogCheck:JSON`、`LogApply:JSON` | 查询、校验或原子保存日志对象的Merge Patch,如`LogApply:{"retain_all":false,"keep_timestamp":false}`. 桥接调用GUI配置事务,校验不应用. |
| `LogStatus`、`LogRecover:新路径` | 查询当前进程真实归档状态,显式把未确认批次另存到全新文件;等待实际写完才报告完成,已有文件拒绝覆盖. |
| `ImportTemplate:路径`、`ExportTemplate:路径` | 替换/导出当前后端的命令表，与一次性模板参数同义；文件操作不发往设备，也不能放入设备模块执行。 |
| `SaveReceived:路径`、`SaveReceived:off` | 开始/停止原始 RX 追加；无参数、`:0` 同样停止。打开新文件失败时保留旧记录器。 |
| `Clear`、`Help` | 清屏、列出命令。 |
| `Quit`、`Quit:cli`、`Quit:0` | 仅退出 CLI。桥接不关闭 GUI/串口，已受理任务继续；独立模式取消自身任务并释放自有串口。Ctrl+C/D 同样退出。 |

两后端提供同一控制台命令能力。显示来源、时间戳和颜色沿用显示配置,日志按独立日志配置收录;会话级HEX/显示时间戳和`Log`路径不写回配置,`LogApply`明确持久化日志设置。任务运行时新的设备任务会被拒绝；用 `Cancel` 或等待完成。

一次性日志管理使用`--log-action get|check|apply|status|recover`,可与`--config`及`--via-gui`组合,须与发送、控制台、机器及其他管理参数分开执行.`check/apply`须提供`--log-patch JSON文件`(或`-`读取stdin),内容是日志对象补丁,不包裹`state/log_settings`;`recover`须提供`--log-recover 新文件`. 查询与校验严格只读已有配置,不创建、修复或迁移文件;应用原子保存失败保持原内存/文件. 管理结果为单行UTF-8 JSON,失败非零退出.

```powershell
.\GHowe_串口调试助手_CLI.exe --config '.\device_config.json' --log-action get
.\GHowe_串口调试助手_CLI.exe --config '.\device_config.json' --log-action apply --log-patch '.\log-patch.json'
.\GHowe_串口调试助手_CLI.exe --via-gui --config 'D:\app\gui_config.json' --log-action status
.\GHowe_串口调试助手_CLI.exe --via-gui --config 'D:\app\gui_config.json' --log-action recover --log-recover '.\recovered.log'
```

归档状态位于`log_status`: `scope=current-process`、`enabled`及`archive`;后者含`path/busy/error/committed_sequence/pending_bytes/pending_entries/failed_entries/recoverable/limit_bytes`. `busy=false`与空`error`表示当前批次成功结束,序号只在完整写入及flush/fsync成功后推进. 独立一次性`status`没有上一进程的内存,`recover`明确失败;独立失败内容须在仍存活的控制台用`LogRecover`另存,GUI内容则用`--via-gui`恢复.

确认当前接纳的内容全部落盘还须核对`pending_entries=0`和`failed_entries=0`,低于阈值的尾部可能仍待补写. 桥接终端未启动显式`Log`时,`LogStatus/LogRecover`操作GUI完整归档;启动显式`Log`后这两个命令操作本机显式归档,GUI归档仍可用一次性`--via-gui --log-action status/recover`查询或恢复. GUI成功提交日志规则及发送快捷/颜色后会同步到已有终端,保留终端`Timestamp`等会话显示偏好;本机显式文件故障不会被GUI自动保存开关接管.

独立一次性发送及本机终端遵守`save_full_log`,空目录使用工作JSON旁的`logs`;桥接控制台由GUI拥有自动完整日志,不会再自动生成第二份,但可显式`Log:路径`保存终端会话文本. 缓冲达到MB预算80%后交给单后台写者,终端不持有GUI显示历史,MB限制用于待写缓冲. 关闭/退出先停止事件生产并补尾,失败影响退出结果;写入失败或生产速度超预算显式停存,已接纳未确认批次可另存,无法接纳的事件剩余及后续事件会明确报告未收录,不无限积压或自动重试可能部分写入的原文件. 本机`Log:off`停止会话自动/显式归档,不修改持久化开关;重新`Log:路径`可启动显式会话.

## 模板与接收文件

```powershell
.\GHowe_串口调试助手_CLI.exe --config '.\device_config.json' --import-template '.\commands.csv'
.\GHowe_串口调试助手_CLI.exe --via-gui --config 'D:\app\gui_config.json' --export-template '.\commands.txt'
.\GHowe_串口调试助手_CLI.exe --via-gui --config 'D:\app\gui_config.json' --send 'AT' --expect 'OK' --read-timeout 2000 --save-received '.\rx.bin'
.\GHowe_串口调试助手_CLI.exe --console --save-received '.\rx.bin'
```

CSV/TXT 共用 GUI 的三列模板格式（启用布尔值、命令、注释），支持 UTF-8/BOM、说明行、模块标记和既有转义。TXT 也须是该表格格式，不会自动把任意文本当命令列表。导入先完整解析，再替换整表、清空旧行替换规则并重置模块选择，保留串口及远程设置；格式、保存失败或提交前取消保持原状态。导出原子替换已有文件，导入/导出可使用相同文件名；不导出完整配置。

独立 CLI 直接调用共享模板函数；桥接把绝对路径交给 GUI 实际读写，与 GUI 按钮调用同一异步接口，窗口显示进度并可取消，任务繁忙时拒绝。大模板不作为 JSON 整表透传，128 KiB 的 IPC 消息限制不再限制模板文件大小。导入分块期间拒绝其它配置快照查询，避免读到半张表；进入最终原子保存阶段后等待真实提交结果。

全部文件路径相对启动 CLI 的工作目录解析。桥接模板由 GUI 账户访问该绝对路径，RX 文件仍由 CLI 写入；桥接仍要求同一 Windows 用户。控制台参数可带成对引号；若文件名恰为 `off`/`0`，用 `SaveReceived:.\off` 或带完整路径以免被解释为停止。

`--save-received`/`SaveReceived` 流式**追加原始字节**，不加时间戳、来源标签、HEX 转换或编码转换：UTF-8 设备文本可直接按 UTF-8 阅读，二进制原样保留。记录独立于显示和保存来源筛选，不含 TX、系统信息或运行前历史；无 RX 时创建/保留空文件。独立模式仅记录本进程读到的 RX；GUI 一次性模式记录该请求期间回传的 RX，GUI 控制台记录订阅期间收到的全部设备 RX（可能含 GUI 其它操作的响应），不包含下载器内部未发布的 ACK。`Log`是按独立日志配置筛选、带来源的UTF-8会话日志,时间戳由`keep_timestamp`控制,不受显示时间戳开关影响。

GUI“保存数据”导出接收区现有历史，可选发送、接收、系统、错误来源并保留其文本格式，范围与原始 RX 记录不同。超时、执行失败和正常退出仍保留此前写入；目标无法打开时不执行设备操作。运行期保存失败明确报错退出，不重发；桥接退出不会取消已受理的 GUI 任务或关闭其串口，设备操作可能已执行。

模板完成、取消、失败及 RX 保存启用/停止/失败使用同源系统文案；共享 GUI 时两边各显示一次，GUI 关闭系统来源显示不会阻止事件记录或 RX 写入。文件结果及桥接 JSON 的 `timestamp` 是事件原始时刻，保持纯 JSON；GUI 的“显示时间戳”只改变系统历史的显示，导出历史和控制台`Log`按`keep_timestamp`保留事件时刻，原始 RX 文件不加时间前缀。

### 解析与未知命令边界

`/:` 转义字面冒号；`//` 表示单个 `/`；其他斜杠原样保留。既有 `\:` 冒号转义继续有效。例：`Unknown/:0` 发送字面 `Unknown:0`。

未知前缀依入口处理：独立一次性 `--send` 的 `Unknown:0` 报错；GUI/远程模块保留表格中的未知设备文本。控制台直接输入未知前缀报错，但已配置模块中的未知设备文本仍按表格字面发送。已注册的会话命令不能在模块中作为设备命令执行；需要发送同名字面文本时转义冒号。

## GUI、配置与远程操作

| `--gui-action` | 用途 |
| --- | --- |
| `status` / `open` / `close` / `cancel` | 查询状态、开关 GUI 所持串口、请求取消任务。 |
| `remote-connect` / `remote-listen` / `remote-stop` | 控制端连接设备端、设备端开启监听、停止当前连接/监听。两端 GUI 必须已运行；`remote-listen` 绑定本机全部 IPv4 接口。`remote-connect` 等认证与能力协商完成才成功，不自动重连。 |
| `config-get` / `config-schema` | 读取窗口内存配置/字段规则。返回 `state`、`tools`；口令省略并由 `omitted_paths` 标出。`config-get` 成功结果额外返回 `max_config_result_bytes`，当前为 4194304 字节，包含完整 UTF-8 JSON 行及末尾换行。 |
| `config-check` / `config-apply` | 对 Merge Patch 校验/应用并保存；配置应用不隐式 Open、联网或下载。任务运行时拒绝应用。 |

`.\GHowe_串口调试助手_CLI.exe --via-gui --config "D:\app\启动.json" --gui-action config-switch --config-switch "D:\工作 目录\任务.json"` 调用与 GUI 相同的工作文件切换事务. `--config` 始终定位启动时的 GUI 桥接,不会改为新文件;不支持和 `--console`/`--machine` 组合. 切换加载工作设置并更换后续保存路径,保留当前串口/远程连接及其设置,不写旧文件或新文件. 有任务/响应校验/RX 窗口/模态编辑时拒绝. 成功返回 `switched`、`preserved_connection_settings=true`;同文件 `switched=false`. 成功或 GUI 拒绝/失败均返回实际 `working_config_file` 和固定 `bridge_config_file`;`status`/`config-get` 也返回这两个路径. 目标不存在、JSON 或配置非法、标记只读及应用失败均保持旧工作状态;取消选文件只适用于 GUI. 切换不等于修改远端串口参数,文件中连接字段未生效,亦不保证后续保存时仍有写入权限. 切换成功或同文件退出 0;GUI 拒绝、读取/校验/应用失败退出 1;参数组合或桥接定位错误退出 2;请求发出后断连/超时退出 3,按未知结果处理并通过原桥接路径查询,不盲目重试.

配置补丁遵循 [JSON Merge Patch（RFC 7396）](https://www.rfc-editor.org/rfc/rfc7396)，形状示例：`{"state":{"receive_settings":{"system_levels":{"debug":false}}}}`。对象合并、数组整体替换、`null` 删除；请求帧和常规回执上限 128 KiB，普通累计待发送数据上限 512 KiB；完整 `config-get` 成功终态帧单独上限 4 MiB，按 UTF-8 编码后的整行字节数（含换行）计算，且仅当前请求 ID、含配置对象及匹配容量声明的成功终态可使用对应待发送额度。超限明确失败且不截断配置。客户端和 GUI 均须支持该回执上限。查询/校验不写盘；直接改 GUI 正在使用的 JSON 不会更新窗口内存。

远程基本串口配置位于 `state.basic_settings`（port、baudrate、databits、parity、stopbits），修改前须关闭设备端串口，再显式 Open；远程配置修改要求设备端声明 `serial_config=1`。其它常见组：`state.remote_settings`、`receive_settings`、`send_settings`、`other_settings`、`commands`、`command_replacement_rules`、`tools`。改远程目标前先 `remote-stop`。状态结果中 `remote_mode`、`network_connected` 表示网络连接，`serial.connected` 单独表示串口状态。

`state.remote_settings.mode` 是持久化角色标识,仍使用 `"主控端"` 表示设备端、`"远程端"` 表示控制端;界面分别显示“设备端（连接硬件）”和“控制端（远程操作）”。配置查询、schema、补丁和工作 JSON 共用这两个保存值,不用界面说明文字作为配置值。运行状态 `remote_mode` 仍为 `server/client/off`。

桥接输出为 UTF-8 JSON 行：原始设备 RX 保留为 `receive` 事件，`accepted` 不是执行完成；等待 `result`。操作终态 `status` 为 `success`、`failed` 或 `unknown`；取消请求的 `accepted` 仅表示受理，控制台任务还可返回 `cancelled`。远程固件文件路径属于控制端，协议与串口属于设备端。

### Agent 远程操作流程

**当前网络远程依赖两端 GUI，不能只靠两份 CLI EXE 无窗口运行。** GUI 启动即开启本机 IPC 桥接，不需要另开“共享”开关；它只服务同一 Windows 用户、同一配置路径的 CLI，不是 TCP 远程监听。设备端（串口硬件所在机器）还必须显式启用网络监听，控制端 GUI 再连接它。

| 运行前提 | 实际能力 |
| --- | --- |
| 无本机 GUI，独立一次性命令或控制台 | 仅操作本机串口；配置文件里的远程设置不会使其成为网络客户端。 |
| 本机 GUI 已运行，目标 GUI 已监听 | 一次性桥接和桥接控制台可操作设备端串口；命令中的 COM 名属于设备端 Windows。 |
| 无本机 GUI，指定 `--via-gui` | 报端点错误，退出 2；停止流程，不尝试独立模式。 |
| 目标 GUI 未运行/未监听或认证失败 | `remote-connect` 失败；停止流程，不继续 Open/发送。 |

Agent 自动化优先用**一次性命令**：每步可检查退出码和最终 JSON。控制台适合已连通后的连续调试；先完成下面的远程连接、选口、Open，再进入 `--via-gui --console --config ...`，避免控制台启动时自动 Open 了本机串口。控制台不支持修改远程 IP/端口/口令，也不接受 `--gui-action`、`--remote-*`、`--config-patch`；这些管理操作须在进入前用一次性动作完成。控制台内没有 `--expect`，自动断言响应用一次性 `--send --expect`。

交接给其他 Agent 时必须提供：控制端 CLI 路径、已运行 GUI 启动时的桥接配置绝对路径；设备端 IP、TCP 端口、共享口令；**已确认的设备端 COM 名**、波特率及设备测试命令/预期响应。设备端 GUI 必须运行且监听，TCP 可达，两端版本须支持设备执行回执。控制端所有命令固定同一个 `--config`；设备端使用它自己 GUI 的配置路径，不要求两台机器路径相同。

**串口列表限制：当前 CLI 没有列出/刷新远端串口列表的参数、控制台命令或 GUI action。** `--gui-action status` 只返回当前所选串口，不返回可选列表；也没有 `remote-status` action。应在控制端 GUI 连接后点击串口“刷新”（会请求设备端扫描），或在设备端 GUI 确认列表，再把 COM 名交给 Agent。缺少 COM 名时停止并报告缺少输入，不能猜测或把本机枚举结果当成远端列表。

以下为同一工作流的两阶段 PowerShell 示例；示例 IP `192.0.2.10`、口令与 COM3 必须替换，COM3 来自设备端机器。先在**设备端机器**运行，前提是对应 GUI 已打开、远程模式当前未启用（已启用需先 `remote-stop`）：

```powershell
.\GHowe_串口调试助手_CLI.exe --config "D:\app\host_config.json" --gui-action remote-listen --remote-port 8765 --remote-password "REPLACE_WITH_SHARED_PASSWORD"
if ($LASTEXITCODE -ne 0) { throw "设备端监听失败，停止流程" }
```

再在**控制端机器**运行，前提是对应 GUI 已打开、无任务运行，且已取得设备端 COM 名。每步等待进程结束；非零退出立即停止，保留输出诊断：

```powershell
$cli = '.\GHowe_串口调试助手_CLI.exe'
$cfg = 'D:\app\gui_config.json'  # 示例路径：替换为控制端 GUI 实际使用的配置路径
function Invoke-SerialCli {
    & $cli --via-gui --config $cfg @args
    if ($LASTEXITCODE -ne 0) { throw "CLI 退出码 $LASTEXITCODE，停止；不要自动重试" }
}
Invoke-SerialCli --gui-action status
Invoke-SerialCli --gui-action remote-stop  # 切换 IP/端口/口令前先停旧连接；off 时也可执行
Invoke-SerialCli --gui-action close        # 连接设备端前释放控制端本机串口
Invoke-SerialCli --gui-action remote-connect --remote-host '192.0.2.10' --remote-port 8765 --remote-password 'REPLACE_WITH_SHARED_PASSWORD'
$status = @(Invoke-SerialCli --gui-action status | ConvertFrom-Json)[-1]
if ($status.remote_mode -ne 'client' -or -not $status.network_connected -or $status.remote.capabilities.device_results -ne 1) { throw '远程连接未就绪' }
Invoke-SerialCli --gui-action close        # 此时关闭的是设备端串口
Invoke-SerialCli --send 'ComPort:COM3'      # 仅改选择；原活动句柄不会切换
Invoke-SerialCli --send 'BaudRate:115200'
Invoke-SerialCli --gui-action open
$status = @(Invoke-SerialCli --gui-action status | ConvertFrom-Json)[-1]
if ($status.remote_mode -ne 'client' -or -not $status.network_connected -or -not $status.serial.connected) { throw '设备端串口未打开' }
Invoke-SerialCli --send 'AT' --expect 'OK' --read-timeout 2000
Invoke-SerialCli --gui-action close
Invoke-SerialCli --gui-action remote-stop
```

`remote-connect` 会等待认证、`device_results=1` 能力协商及设备端串口配置同步，通常 10 秒内完成或失败；只有最终 `event:"result", success:true, status:"success"`（消息“已连接设备端并同步状态”）才可继续。`remote-listen` 成功结果的 `remote.listening` 应为 `true`。状态查询自身成功不等于已连网；检查 `remote_mode:"client"`、`network_connected:true`，打开后再检查 `serial.connected:true`。`accepted`、串口仅被选中或命令已排队均不能当作完成。上例发送匹配成功退出 0，未匹配退出 4；退出 3/`unknown` 表示可能已经执行，停止自动重试并核对两端日志。

改 IP 使用 `remote-stop` → `remote-connect --remote-host ...`，TCP 端口和共享口令分别用 `--remote-port`、`--remote-password`；省略沿用窗口当前值。若要持久保存一组远程设置，先 `remote-stop`，把最小补丁 `{"state":{"remote_settings":{"host":"192.0.2.10","port":8765,"token":"REPLACE_WITH_SHARED_PASSWORD"}}}` 保存为 UTF-8 JSON 文件，依次执行 `--gui-action config-check --config-patch 文件`、检查成功后 `--gui-action config-apply --config-patch 文件`；两次都带同一 `--config`。应用配置不会建立连接，仍须显式 `remote-connect`；不要直接编辑已运行 GUI 的配置文件。当前配置动作没有 `--yes` 参数。

若改为控制台，在上例 Open 与状态确认后执行 `& $cli --via-gui --console --config $cfg`，输入设备文本、`Status` 或 `Close` → `ComPort:COMx` → `Open`，每条等成功回执后再继续；`Quit` 仅退出 CLI，GUI、网络连接和串口继续存在。需要结束远程会话时，退出控制台后执行上例最后的 `close`、`remote-stop`。一次性远程发送始终显式带 `--via-gui`；省略它会走独立本机串口路径。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 一次性成功或控制台正常退出；不代表设备响应符合业务预期。 |
| 1 | 配置、串口、发送或操作失败。控制台单条命令错误通常显示后继续。 |
| 2 | 参数/正则无效、GUI 端点或 IPC 握手错误、桥接控制台断连；不自动转独立连接。 |
| 3 | 独立一次性短写/写入或排空异常,或桥接请求发出后断连/超时;设备可能已收到部分字节或执行,停止自动重试. |
| 4 | `--expect` 窗口内未匹配。 |

## 源码与排障

GUI/独立CLI日志配置位于 `state.log_settings`: `limit_mb`(1–1024,默认4,1 MB = 1,000,000字节)、`retain_all`(默认true)、`keep_timestamp`(默认true)、`sources`(send/receive/system/error布尔值)、`system_levels`(normal/warning/info/debug布尔值)、`save_full_log`(默认false)、`log_directory`(空字符串表示工作配置目录下的logs). `state.receive_settings` 的 `display_sources`、`system_levels`、`show_timestamp` 只决定显示;旧字符上限和 `other_settings.show_timestamp` 不再是可写字段. 可通过config-get/schema/check/apply查询和修改,持久化成功后才裁剪历史或切换文件保存. 修改目录前先关闭保存并等尾部完成;写盘失败先另存未确认日志,GUI可用上述`--via-gui --log-action recover`或主界面入口。

`config-get`返回来源及级别的基础选择,不把临时强制勾选保存成用户偏好:GUI显示发送的有效状态为 `send_settings.show_send OR receive_settings.display_sources.send`,日志收录发送为 `send_settings.show_send OR log_settings.retain_all OR log_settings.sources.send`. 配置事务可修改基础选择,联动期间GUI仍显示强制选中,解除后呈现新基础选择. `keep_timestamp`始终独立. 主界面日志折叠块已移除,不再保存 `tool_settings.left_group_expanded.log`;日志规则在配置弹窗确认后通过同一事务提交,主界面 `save_full_log` 开关即时生效并自动保存. 日志收录选项不筛选机器JSONL或原始RX,独立CLI自动日志和显式Log共用这些收录选项;独立CLI的Timestamp会话选项使用新的receive_settings字段且不写配置. 完整切换语义见[GUI手册](OPERATION_MANUAL_FOR_GUI.md#43-显示与保存接收区数据).

源码从 `serial_tool_project` 运行：`python main.py --console`；`python cli_main.py --via-gui --gui-action status`。跨入口桥接显式指定相同 `--config`。

串口打不开：确认 COM 号未被占用；控制台可执行 `ComPort:COMx`、`BaudRate:115200` 后 `Open`。响应不符时用 `--expect` 和 `--read-timeout`；超时未匹配为 4。桥接失败时检查 GUI 是否运行、Windows 用户及绝对配置路径是否一致；IPC 错误不会回退。

一次性桥接连接失败的最终 JSON 在 `bridge` 中提供实际进程账户 `user`、Windows 令牌 SID `user_sid`、端点定位用户名 `endpoint_user`、配置绝对路径 `config_path`、`endpoint`、Qt 错误 `socket_error` 及 `socket_error_code`；控制台在错误文本中显示同类诊断。实际 Windows 身份由系统令牌读取，可能与环境变量用户名不同；无法读取时明确给出 `identity_error`。端点仍按原用户名与规范配置路径计算，当前不支持跨 Windows 用户桥接，不通过修改用户名环境变量授权。

一次性 `--send --expect` 的最终 `result` 独立成一行 UTF-8 JSON，含 `expect_matched`、`received_bytes`、`received_preview`、`received_preview_hex` 和 `received_preview_truncated`。独立模式此前的普通输出仍保留；桥接继续保留 `receive` 事件。计数仅含设备 RX，预览最多取前 128 字节：0 字节为无响应；有字节但未匹配为响应不符合正则，两者均退出 4。以下仅为正则不匹配示例，不是通用寄存器协议：`000 = 20240205` 有 14 字节响应，但不符合八位地址正则 `[0-9a-fA-F]{8} = `。执行失败附接收摘要,传输未知为退出3,正常观察窗口未匹配为退出4,不会重发;控制台不接受`--expect`。


## 机器常驻接口

启动: --cli --via-gui --console --machine --config <GUI启动时桥接配置绝对路径>. 协议版本 1,stdin/stdout 为 UTF-8 JSONL,每帧至多 128 KiB. stdout 仅机器事件,不经过显示过滤、颜色或提示符. 显式 GUI 和配置是必需参数,没有独立串口回退.

机器模式不执行人工控制台的自动 Open. GUI 必须已连接目标且空闲,否则失败退出. 首个 ready 具有 protocol:int=1,auto_open:bool=false,config_path/endpoint/endpoint_user:str,target:object,connection:object,idle:bool=true,capabilities:object. target.serial 的 port/baudrate/databits/parity/stopbits 均为当前 GUI 字符串;另含 remote_mode/remote_host/remote_session:str,remote_port:int. remote_session 是连接代次,不是口令. connection 含 serial_connected:bool,network_connected:bool|null,phase:str=connected|download_exclusive;非远程客户端时 network_connected=null. capabilities 沿用 device_results/firmware_upload/serial_config 能力门禁. 不输出整份 commands/tools;模板仍单独核对 config-get 当前内存.

输入命令帧: {"id":"32位小写十六进制调用方UUID","command":"既有控制台命令"}. ID 必须唯一,原 ID 贯穿真实 GUI 执行器、模块执行与终态;模块没有另造不可关联子任务. 同时只允许一条请求/采集窗口. 保留 SendMode/FirmwareDownload 等既有执行,拒绝机器会话的 Open/Close/ComPort/Com/BaudRate/Status/Cancel/StopContinuous 和人工会话命令;模块展开后的目标/连接原语也拒绝. 必要连接/配置在建立会话前通过原正式单次接口完成.

accepted/progress/receive/result 沿用既有事件含义. 每条已提交命令有且只有一个 result,含原 id:str,success:bool,status:str,message/timestamp:str,completion:str="transport",以及 target/connection:object|null;桥接故障无法读取当前值时明确为 null,不使用 ready 缓存冒充当前观测. success 仅证明工具自身原语完成:普通发送是完整写入/远程回执,下载是既有下载确认及恢复;不代表 DUT 功能成功. 调用方必须另验证协议响应和硬件结果,不能凭 accepted/TX/进程存活通过.

原始 RX receive 含 data:Base64字符串,timestamp:str,rx_sequence:每机器订阅从0起的非负单调递增int(首条RX为1);scope_id 与 id 为采集窗口 UUID 或 null,scope:str=request_window|ambient. task_id:str|null 独立保存当时 GUI 真正活动的桥接任务 ID. 外来 task_id 的数据不得匹配本请求. 没有线端请求 ID 的协议无法证明窗口内异步数据的因果;task_id=null 也只是窗口归属. 调用方必须保持单设备写者,匹配完整响应边界及输入关联判据. GUI 只观察,不要并发手工发送;同一 active 期间的人工写入可能无法从原始串口流辨别,工具不宣称物理因果或完全隔离人工操作.

Skill 同时取得成功 transport result 与所需完整响应后,输入 {"action":"close-window","id":"当前请求UUID"}. GUI 在同一订阅流按序输出 window_closed,含 id/scope_id:str,rx_sequence:int,timestamp:str,表示此前已经观测的 RX 均先发送;确认序号恰好等于该订阅最后一个 receive.rx_sequence,尚无RX时为0. 之后 RX 为 ambient. 收到确认才可发下一条. 控制帧不发送串口命令,内部管理 result 不冒充第二个设备终态. 当前窗口开放时拒绝其它桥接设备任务,只读管理仍可用. 关闭后清理该条 GUI 结果缓存,CLI 保留已用 ID 防重发,不会因机器长会话累积到既有 1024 条缓存上限.

connection/target_changed/fatal 主动报告状态;fatal:bool=true 或非成功 result 后整个会话停止. GUI 事件循环内监视,普通命令之间不启动额外 EXE 或独立 status. 每次设备原语与关闭窗口同步复核目标. 周期观察可能因系统调度延后,不作为设备完成判据. 不自动切换目标、重连、重发或重试.

实际下载原语进入后 phase=download_exclusive,只在本会话拥有的该原语期间允许预期调试串口暂时释放,保留真实 serial_connected. 远程网络丢失/目标变化始终失败. 既有下载回调返回后必须确认同一目标调试串口恢复,才允许 success 和关闭窗口,随后才能继续释放 MCU 等命令. 不用 busy 或固定延时猜恢复成功. 失败/未知不取消在途下载.

stdin EOF/异常退出不伪造 GUI 在途任务完成,不自动 close-window/cancel/replay. session_end 含 status:str,pending_id/scope_id:str|null. 无窗口正常 EOF 为 success/退出0;命令失败退出1;参数/协议/桥接故障退出2;EOF留有任务或窗口为 unknown/退出3. GUI 已受理任务可能在 CLI 退出后继续;其后续原语仍逐次核对原绑定目标与连接,EOF本身不取消,漂移或断连则停止后续写入. 操作者必须确认真实终态再启动其它动作. 响应匹配、等待上限和失败后禁止下一条由调用方负责,工具不新增设备匹配器或自动取消.

机器stdout管道关闭或写入失败时,会话停止并退出2,stdin保持打开也不继续等待. 坏stdout无法再发送fatal/result/session_end,stderr可用时只报告一次,stderr也关闭仍非零退出. 已受理GUI任务按同一脱离契约继续,不取消、不重发或伪造完成;调用方须停止后续流程并核对GUI真实结果.


progress 的 source 仅为 send/system/error. 实际执行器产生的任务进度具有 id=task_id=当前调用方UUID,scope_id=当前窗口;TX(source=send,data=Base64)同样保留真实任务ID,不得将外来TX重标当前请求. GUI publish_state、下载进度及其它无明确任务因果的通知可为 id=null,task_id=null,scope_id=当前窗口或null,source=system|error;它们只供诊断,不参与RX匹配或任务终态. source=error本身不伪造result或证明已取消;fatal:true及非成功result分别触发调用方停止. 尚无当前窗口时系统通知scope=ambient. 调用方不得强制所有progress.id等于当前请求,也不得把无归属通知改标成当前任务. receive 的source固定receive;实际payload仍只消费receive.data.
