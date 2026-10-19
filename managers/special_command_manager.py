#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
特殊指令管理模块, 负责执行 delay, SendHex, BaudRate, SendMode 等命令序列指令.

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from core.command_types import SpecialCommandType, OutputSource
from managers.config_manager import ConfigManager
from core.command_executor import TableCommandProvider, collect_module_commands
from core import output_rules

class SpecialCommandManager:
    """GUI 特殊命令分发器,复用窗口的设备入口并组织异步模块执行.

    分发返回值取决于命令:布尔值可能仅表示受理,delay 返回毫秒数.
    FirmwareDownload 使用完成回调;远程设备步骤由窗口等待关联回执,
    不会把命令入队或模块标记视为设备成功.
    """
    def __init__(self, config_manager: ConfigManager):
        """绑定配置并注册既有特殊命令处理函数.

        Args:
            config_manager (ConfigManager): 当前窗口的工具与应用配置管理器.
        """
        self.config_manager = config_manager
        self.commands = {
            SpecialCommandType.MODE: self._handle_mode,
            SpecialCommandType.MODEEND: self._handle_mode_end,
            SpecialCommandType.DELAY: self._handle_delay,
            SpecialCommandType.SENDHEX: self._handle_sendhex,
            SpecialCommandType.BAUDRATE: self._handle_baudrate,
            SpecialCommandType.COMPORT: self._handle_comport,
            SpecialCommandType.SETENDLOG: self._handle_setendlog,
            SpecialCommandType.SENDMODE: self._handle_sendmode,
            SpecialCommandType.STOPCONTINUOUS: self._handle_stop_continuous,
            SpecialCommandType.FIRMWAREDOWNLOAD: self._handle_firmware_download,
        }

    def add_command(self, command_type, handler):
        """添加新指令"""
        self.commands[command_type] = handler

    def execute(self, command_type, param, context, completion_callback=None):
        """分发特殊命令,失败时标记当前命令链.

        Args:
            command_type (SpecialCommandType): 要执行的特殊命令类型.
            param (str): 命令冒号后的参数文本.
            context (SerialTool): 提供实际设备,配置和日志操作的窗口.
            completion_callback (Callable | None): 仅供 FirmwareDownload 的布尔结果
                回调,默认 None;普通异步设备操作仍由窗口等待协议回执.

        Returns:
            bool | float: 原处理函数结果;True 可仅表示受理,delay 返回毫秒值,
                未找到处理函数时返回 False,不能统一解释为设备完成.
        """
        if command_type in self.commands:
            if command_type == SpecialCommandType.FIRMWAREDOWNLOAD:
                return self.commands[command_type](param, context, completion_callback)
            result = self.commands[command_type](param, context)
            if result is False:
                context._remote_sequence_failed = True
            return result
        return False

    def _handle_mode(self, param, context):
        """接收已由模块解析器处理的 mode 标记,不执行设备操作."""
        # mode指令在解析模板时处理, 这里只是占位
        return True

    def _handle_mode_end(self, param, context):
        """处理modeend指令 - 结束当前模块定义
        
        runtime行为:
        - 结束当前模块的执行 (跳出当前模块发送循环)
        """
        from core.command_types import OutputSource

        # 如果 param 为 "0" 或空,则结束当前模块执行
        if not param or param.strip() == "0":
            if hasattr(context, '_skip_to_loop'):
                # 下划线属性直接赋值,main_window.py 中 send_next_command 会检查此属性
                context._skip_to_loop = True
            
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text("ModeEnd: 结束当前模块执行", OutputSource.SYSTEM)
        
        return True

    def _handle_delay(self, param, context):
        """解析毫秒数并在存在 delay_timer 时启动定时器;返回数值不表示等待已结束."""
        try:
            delay_ms = float(param.strip())
            # 在GUI线程中使用QTimer进行延迟, 避免阻塞界面
            if hasattr(context, 'delay_timer'):
                context.delay_timer.start(int(delay_ms))
                # 这里需要等待定时器完成, 实际在连续发送循环中处理
            return delay_ms
        except ValueError:
            return False

    def _handle_sendhex(self, param, context):
        """处理 SendHex 指令"""
        try:
            from core.command_parser import parse_hex_bytes
            data = parse_hex_bytes(param)
            if not data:
                return False
            
            # 获取结尾标识符并追加 (根据用户需求:SendHex 后需要发送系统设置的结尾标识符)
            if hasattr(context, 'get_ending_chars'):
                ending = context.get_ending_chars()
                data += ending
                
            if hasattr(context, 'send_raw_data'):
                return context.send_raw_data(data)
            return True
        except Exception as e:
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text(output_rules.sendhex_error(str(e)), OutputSource.ERROR)
            return False

    def _handle_baudrate(self, param, context):
        """处理BaudRate指令"""
        try:
            baudrate = int(param.strip())
            if hasattr(context, 'update_baudrate'):
                return context.update_baudrate(baudrate)
            return False
        except ValueError:
            return False

    def _handle_comport(self, param, context):
        """处理 ComPort 指令"""
        port = param.strip()
        if hasattr(context, 'update_com_port'):
            return context.update_com_port(port)
        return False

    def _handle_setendlog(self, param, context):
        """处理SetEndlog指令"""
        if hasattr(context, 'set_ending'):
            return context.set_ending(param.strip())
        return False

    def _handle_sendmode(self, param, context):
        """处理SendMode指令

        当在连续发送过程中调用时,会内联发送指定模块的内容
        当单独点击发送按钮时,只发送指定模块的内容(不启动连续发送)
        """
        # 使用 execute_sendmode_inline 来发送指定模块
        # 这样无论是在连续发送中还是单独点击,行为都是一致的
        return self.execute_sendmode_inline(param.strip(), context, completion_callback=None)

    def _handle_stop_continuous(self, param, context):
        """处理StopContinuous指令 - 停止连续发送,可选择是否停止循环发送

        参数:
        - 0 或 空: 提前结束本轮发送,保持循环发送继续(默认)
        - 1: 完全停止连续发送和循环发送
        """
        from core.command_types import OutputSource

        # 解析参数:0=提前结束本轮但继续循环,1=完全停止
        mode = 0
        if param and param.strip():
            try:
                mode = int(param.strip())
            except ValueError:
                pass

        if mode == 0:
            # 模式 0: 提前结束本轮,但继续循环发送
            # 设置标志让 send_next_command 立即跳转到循环检查
            context._skip_to_loop = True
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text("StopContinuous: 提前结束本轮发送", OutputSource.SYSTEM)
            return True
        elif mode == 1:
            # 模式 1: 完全停止连续发送和循环发送
            if hasattr(context, 'stop_continuous_send'):
                context.stop_continuous_send()
                if hasattr(context, 'loop_send_check'):
                    context.loop_send_check.setChecked(False)
                if hasattr(context, 'output_manager'):
                    context.output_manager.append_text("StopContinuous: 已停止连续发送和循环发送", OutputSource.SYSTEM)
                return True
        return False

    def _handle_firmware_download(self, param, context, completion_callback=None):
        """处理 FirmwareDownload 指令,实际流程由主窗口协调串口占用."""
        if not hasattr(context, "start_firmware_download_command"):
            return False
        return context.start_firmware_download_command(param, completion_callback)

    def execute_sendmode_inline(self, module_name, context, completion_callback=None, stack=None):
        """内联执行 SendMode 指令 - 发送指定模块后返回继续当前模块

        Args:
            module_name: 模块名称
            context: 上下文对象
            completion_callback: 完成后的回调函数(可选)
            stack: 当前嵌套模块路径,用于拒绝递归循环;顶层调用省略.

        Returns:
            bool: 是否成功启动执行(不代表执行完成)
        """
        from PyQt5.QtCore import QTimer
        from core.command_types import OutputSource
        from utils.ui_utils import UIUtils

        module_name = module_name.strip()
        stack = stack or []
        if module_name in stack:
            context._remote_sequence_failed = True
            context.output_manager.append_text("SendMode 循环调用: " + " -> ".join(stack + [module_name]), OutputSource.ERROR)
            if completion_callback:
                completion_callback()
            return False

        def schedule_next(delay, next_command):
            """复用窗口的回执等待和命令代号检查,失败时沿模块完成回调退出."""
            context.schedule_command_next(delay, next_command, completion_callback)

        # 判断当前是否为手动单次发送模式 (非连续发送状态)
        is_manual_mode = not getattr(context, 'is_continuous_sending', False)
        if is_manual_mode and not stack and not getattr(context, '_remote_device_pending', {}):
            context._remote_sequence_failed = False

        # 检查串口后端是否可发送;控制端模式下不能直接读取本机 is_connected
        if hasattr(context, 'can_send_serial_data'):
            can_send = context.can_send_serial_data()
        else:
            can_send = hasattr(context, 'is_connected') and context.is_connected

        if not can_send:
            context._remote_sequence_failed = True
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            if completion_callback:
                completion_callback()
            return False

        # 刷新模块列表以确保最新
        if hasattr(context, 'refresh_modules'):
            context.refresh_modules(silent=True)

        if not hasattr(context, 'modules') or not hasattr(context, 'command_table'):
            context._remote_sequence_failed = True
            if completion_callback:
                completion_callback()
            return False

        # 检查模块是否存在
        if module_name not in context.modules and module_name != "全部":
            context._remote_sequence_failed = True
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text(output_rules.module_not_found(module_name), OutputSource.ERROR)
            if completion_callback:
                completion_callback()
            return False

        # 从 UI 表格读取命令, 模块解析逻辑与 CLI 共用
        commands_to_send = collect_module_commands(TableCommandProvider(context.command_table), module_name)

        if not commands_to_send:
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text(output_rules.module_no_enabled_commands(module_name), OutputSource.SYSTEM)
            if completion_callback:
                completion_callback()
            return True  # 返回 True 表示执行完成(虽然没发送内容)

        if hasattr(context, 'output_manager'):
            context.output_manager.append_text(output_rules.sendmode_start(module_name), OutputSource.SYSTEM)

        if is_manual_mode and not stack:
            context._manual_module_generation = context._command_generation
            original_callback = completion_callback
            def finish_manual_module():
                """完成时释放本代手动模块占用;停止仍由既有命令代号使旧回调失效."""
                context._manual_module_generation = None
                if original_callback:
                    original_callback()
            completion_callback = finish_manual_module

        # 通过定时器和完成回调串联发送,避免阻塞 GUI 线程.
        def send_module_command(index=0):
            """按索引执行模块一步,通过定时器或任务回调进入下一步,不阻塞 GUI."""
            # 检查是否已停止连续发送 (仅在连续发送模式下才检查此标志)
            if not is_manual_mode and hasattr(context, 'is_continuous_sending') and not context.is_continuous_sending:
                # 已停止,调用完成回调并退出
                if completion_callback:
                    completion_callback()
                return

            if index >= len(commands_to_send):
                # 模块发送完成,调用完成回调
                if completion_callback:
                    completion_callback()
                return

            row, command, is_special, *special_args = commands_to_send[index]

            if is_special:
                # 处理特殊指令
                command_type, param = special_args
                if command_type == SpecialCommandType.DELAY:
                    try:
                        delay_ms = float(param.strip())
                        if hasattr(context, 'output_manager'):
                            context.output_manager.append_text(output_rules.sendmode_delay(delay_ms), OutputSource.SYSTEM)
                        schedule_next(int(delay_ms), lambda: send_module_command(index + 1))
                        return
                    except ValueError:
                        if hasattr(context, 'output_manager'):
                            context.output_manager.append_text(output_rules.invalid_delay(param), OutputSource.ERROR)
                elif command_type == SpecialCommandType.SENDHEX:
                    if self.execute(command_type, param, context):
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        schedule_next(interval, lambda: send_module_command(index + 1))
                    else:
                        if hasattr(context, 'output_manager'):
                            context.output_manager.append_text(
                                output_rules.special_command_failed("SendHex", param),
                                OutputSource.ERROR,
                            )
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        schedule_next(interval, lambda: send_module_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SENDMODE:
                    # 嵌套的 SendMode - 递归调用,传入完成回调
                    def on_nested_sendmode_complete():
                        """嵌套模块结束后等待远程回执,再安排外层模块下一步."""
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        schedule_next(interval, lambda: send_module_command(index + 1))

                    self.execute_sendmode_inline(param.strip(), context, on_nested_sendmode_complete, stack + [module_name])
                    return
                elif command_type == SpecialCommandType.FIRMWAREDOWNLOAD:
                    completed = False
                    def on_firmware_download_complete(success):
                        """只处理一次下载终态;失败停止流程,成功后按模块间隔进入下一步."""
                        nonlocal completed
                        if completed:
                            return
                        completed = True
                        if not success:
                            context._remote_sequence_failed = True
                            if hasattr(context, "stop_continuous_sending"):
                                context.stop_continuous_sending()
                            if completion_callback:
                                completion_callback()
                            return
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        schedule_next(interval, lambda: send_module_command(index + 1))

                    if not self.execute(
                        command_type,
                        param,
                        context,
                        on_firmware_download_complete,
                    ):
                        on_firmware_download_complete(False)
                    return
                elif command_type == SpecialCommandType.STOPCONTINUOUS:
                    # 处理 StopContinuous 指令
                    self.execute(command_type, param, context)
                    # 如果是模式0(_skip_to_loop),则跳出 SendMode 但继续外层循环
                    # 如果是模式1,会调用 stop_continuous_sending,完全停止
                    if completion_callback:
                        completion_callback()
                    return
                elif command_type == SpecialCommandType.BAUDRATE:
                    # 处理 BaudRate 指令
                    self.execute(command_type, param, context)
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    schedule_next(interval, lambda: send_module_command(index + 1))
                    return
                elif command_type == SpecialCommandType.COMPORT:
                    # 处理 ComPort 指令
                    self.execute(command_type, param, context)
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    schedule_next(interval, lambda: send_module_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SETENDLOG:
                    # 处理 SetEndlog 指令
                    self.execute(command_type, param, context)
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    schedule_next(interval, lambda: send_module_command(index + 1))
                    return
                # 其他特殊指令跳过
                interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                schedule_next(interval, lambda: send_module_command(index + 1))
            else:
                # 发送普通命令
                if hasattr(context, 'send_command') and context.send_command(command, row):
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    schedule_next(interval, lambda: send_module_command(index + 1))
                else:
                    context._remote_sequence_failed = True
                    # 失败沿完成回调返回调用方,不能把模块执行报告为成功.
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    schedule_next(interval, lambda: send_module_command(index + 1))

        # 开始发送模块命令
        send_module_command()
        return True

    def get_command_names(self):
        """获取所有指令名称"""
        from core.command_parser import COMMANDS
        return [COMMANDS[cmd.value.casefold()] for cmd in self.commands.keys()]
