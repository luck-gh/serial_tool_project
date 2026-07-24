#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
特殊指令管理模块, 负责执行 delay, SendHex, BaudRate, SendMode 等命令序列指令。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from utils.ui_utils import SpecialCommandType
from managers.config_manager import ConfigManager
from core.command_executor import TableCommandProvider, collect_module_commands
from core import output_rules

class SpecialCommandManager:
    """特殊指令管理器"""
    def __init__(self, config_manager: ConfigManager):
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
        """执行指令"""
        if command_type in self.commands:
            if command_type == SpecialCommandType.FIRMWAREDOWNLOAD:
                return self.commands[command_type](param, context, completion_callback)
            return self.commands[command_type](param, context)
        return False

    def _handle_mode(self, param, context):
        """处理mode指令"""
        # mode指令在解析模板时处理, 这里只是占位
        return True

    def _handle_mode_end(self, param, context):
        """处理modeend指令 - 结束当前模块定义
        
        runtime行为:
        - 结束当前模块的执行 (跳出当前模块发送循环)
        """
        from utils.ui_utils import OutputSource

        # 如果 param 为 "0" 或空，则结束当前模块执行
        if not param or param.strip() == "0":
            if hasattr(context, '_skip_to_loop'):
                # 下划线属性直接赋值，main_window.py 中 send_next_command 会检查此属性
                context._skip_to_loop = True
            
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text("ModeEnd: 结束当前模块执行", OutputSource.SYSTEM)
        
        return True

    def _handle_delay(self, param, context):
        """处理delay指令"""
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
            # 移除空格
            hex_str = param.strip().replace(" ", "")
            if not hex_str:
                return False
            
            # 如果长度为奇数，在前面补0以符合 bytes.fromhex 的要求
            if len(hex_str) % 2 != 0:
                hex_str = '0' + hex_str
                
            data = bytes.fromhex(hex_str)
            
            # 获取结尾标识符并追加 (根据用户需求：SendHex 后需要发送系统设置的结尾标识符)
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
                context.update_baudrate(baudrate)
            return True
        except ValueError:
            return False

    def _handle_comport(self, param, context):
        """处理 ComPort 指令"""
        port = param.strip()
        if hasattr(context, 'update_com_port'):
            context.update_com_port(port)
            return True
        return False

    def _handle_setendlog(self, param, context):
        """处理SetEndlog指令"""
        if hasattr(context, 'set_ending'):
            context.set_ending(param.strip())
            return True
        return False

    def _handle_sendmode(self, param, context):
        """处理SendMode指令

        当在连续发送过程中调用时，会内联发送指定模块的内容
        当单独点击发送按钮时，只发送指定模块的内容（不启动连续发送）
        """
        # 使用 execute_sendmode_inline 来发送指定模块
        # 这样无论是在连续发送中还是单独点击，行为都是一致的
        return self.execute_sendmode_inline(param.strip(), context, completion_callback=None)

    def _handle_stop_continuous(self, param, context):
        """处理StopContinuous指令 - 停止连续发送，可选择是否停止循环发送

        参数:
        - 0 或 空: 提前结束本轮发送，保持循环发送继续（默认）
        - 1: 完全停止连续发送和循环发送
        """
        from utils.ui_utils import OutputSource

        # 解析参数：0=提前结束本轮但继续循环，1=完全停止
        mode = 0
        if param and param.strip():
            try:
                mode = int(param.strip())
            except ValueError:
                pass

        if mode == 0:
            # 模式 0: 提前结束本轮，但继续循环发送
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
        """处理 FirmwareDownload 指令，实际流程由主窗口协调串口占用。"""
        if not hasattr(context, "start_firmware_download_command"):
            return False
        return context.start_firmware_download_command(param, completion_callback)

    def execute_sendmode_inline(self, module_name, context, completion_callback=None):
        """内联执行 SendMode 指令 - 发送指定模块后返回继续当前模块

        Args:
            module_name: 模块名称
            context: 上下文对象
            completion_callback: 完成后的回调函数（可选）

        Returns:
            bool: 是否成功启动执行（不代表执行完成）
        """
        from PyQt5.QtCore import QTimer
        from utils.ui_utils import UIUtils, OutputSource

        module_name = module_name.strip()

        # 判断当前是否为手动单次发送模式 (非连续发送状态)
        is_manual_mode = not getattr(context, 'is_continuous_sending', False)

        # 检查串口后端是否可发送；远程端模式下不能直接读取本机 is_connected
        if hasattr(context, 'can_send_serial_data'):
            can_send = context.can_send_serial_data()
        else:
            can_send = hasattr(context, 'is_connected') and context.is_connected

        if not can_send:
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            if completion_callback:
                completion_callback()
            return False

        # 刷新模块列表以确保最新
        if hasattr(context, 'refresh_modules'):
            context.refresh_modules(silent=True)

        if not hasattr(context, 'modules') or not hasattr(context, 'command_table'):
            if completion_callback:
                completion_callback()
            return False

        # 检查模块是否存在
        if module_name not in context.modules and module_name != "全部":
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
            return True  # 返回 True 表示执行完成（虽然没发送内容）

        if hasattr(context, 'output_manager'):
            context.output_manager.append_text(output_rules.sendmode_start(module_name), OutputSource.SYSTEM)

        # 发送命令（同步方式，使用延迟链）
        def send_module_command(index=0):
            # 检查是否已停止连续发送 (仅在连续发送模式下才检查此标志)
            if not is_manual_mode and hasattr(context, 'is_continuous_sending') and not context.is_continuous_sending:
                # 已停止，调用完成回调并退出
                if completion_callback:
                    completion_callback()
                return

            if index >= len(commands_to_send):
                # 模块发送完成，调用完成回调
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
                        QTimer.singleShot(int(delay_ms), lambda: send_module_command(index + 1))
                        return
                    except ValueError:
                        if hasattr(context, 'output_manager'):
                            context.output_manager.append_text(output_rules.invalid_delay(param), OutputSource.ERROR)
                elif command_type == SpecialCommandType.SENDHEX:
                    if self.execute(command_type, param, context):
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        QTimer.singleShot(interval, lambda: send_module_command(index + 1))
                    else:
                        if hasattr(context, 'output_manager'):
                            context.output_manager.append_text(
                                output_rules.special_command_failed("SendHex", param),
                                OutputSource.ERROR,
                            )
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        QTimer.singleShot(interval, lambda: send_module_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SENDMODE:
                    # 嵌套的 SendMode - 递归调用，传入完成回调
                    def on_nested_sendmode_complete():
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        QTimer.singleShot(interval, lambda: send_module_command(index + 1))

                    self.execute_sendmode_inline(param.strip(), context, on_nested_sendmode_complete)
                    return
                elif command_type == SpecialCommandType.FIRMWAREDOWNLOAD:
                    def on_firmware_download_complete(success):
                        if not success:
                            if hasattr(context, "stop_continuous_sending"):
                                context.stop_continuous_sending()
                            if completion_callback:
                                completion_callback()
                            return
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        QTimer.singleShot(interval, lambda: send_module_command(index + 1))

                    if not self.execute(
                        command_type,
                        param,
                        context,
                        on_firmware_download_complete,
                    ):
                        if hasattr(context, "stop_continuous_sending"):
                            context.stop_continuous_sending()
                        if completion_callback:
                            completion_callback()
                    return
                elif command_type == SpecialCommandType.STOPCONTINUOUS:
                    # 处理 StopContinuous 指令
                    self.execute(command_type, param, context)
                    # 如果是模式0（_skip_to_loop），则跳出 SendMode 但继续外层循环
                    # 如果是模式1，会调用 stop_continuous_sending，完全停止
                    if completion_callback:
                        completion_callback()
                    return
                elif command_type == SpecialCommandType.BAUDRATE:
                    # 处理 BaudRate 指令
                    self.execute(command_type, param, context)
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    QTimer.singleShot(interval, lambda: send_module_command(index + 1))
                    return
                elif command_type == SpecialCommandType.COMPORT:
                    # 处理 ComPort 指令
                    self.execute(command_type, param, context)
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    QTimer.singleShot(interval, lambda: send_module_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SETENDLOG:
                    # 处理 SetEndlog 指令
                    self.execute(command_type, param, context)
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    QTimer.singleShot(interval, lambda: send_module_command(index + 1))
                    return
                # 其他特殊指令跳过
                interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                QTimer.singleShot(interval, lambda: send_module_command(index + 1))
            else:
                # 发送普通命令
                if hasattr(context, 'send_command') and context.send_command(command, row):
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    QTimer.singleShot(interval, lambda: send_module_command(index + 1))
                else:
                    # 发送失败，跳过并继续
                    interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                    QTimer.singleShot(interval, lambda: send_module_command(index + 1))

        # 开始发送模块命令
        send_module_command()
        return True

    def get_command_names(self):
        """获取所有指令名称"""
        return [cmd.value for cmd in self.commands.keys()]
