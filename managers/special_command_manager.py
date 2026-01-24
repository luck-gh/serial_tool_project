from utils.ui_utils import SpecialCommandType
from managers.config_manager import ConfigManager

class SpecialCommandManager:
    """特殊指令管理器"""
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.commands = {
            SpecialCommandType.MODE: self._handle_mode,
            SpecialCommandType.DELAY: self._handle_delay,
            SpecialCommandType.SENDHEX: self._handle_sendhex,
            SpecialCommandType.BAUDRATE: self._handle_baudrate,
            SpecialCommandType.SETENDLOG: self._handle_setendlog,
            SpecialCommandType.SENDMODE: self._handle_sendmode,
            SpecialCommandType.STOPCONTINUOUS: self._handle_stop_continuous
        }

    def add_command(self, command_type, handler):
        """添加新指令"""
        self.commands[command_type] = handler

    def execute(self, command_type, param, context):
        """执行指令"""
        if command_type in self.commands:
            return self.commands[command_type](param, context)
        return False

    def _handle_mode(self, param, context):
        """处理mode指令"""
        # mode指令在解析模板时处理, 这里只是占位
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
                context.output_manager.append_text(f"SendHex 错误: {str(e)}", OutputSource.ERROR)
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

    def _handle_setendlog(self, param, context):
        """处理SetEndlog指令"""
        if hasattr(context, 'set_ending'):
            context.set_ending(param.strip())
            return True
        return False

    def _handle_sendmode(self, param, context):
        """处理SendMode指令"""
        if hasattr(context, 'trigger_send_mode'):
            return context.trigger_send_mode(param.strip())
        return False

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
                context.output_manager.append_text(f"错误: 找不到模块 '{module_name}'", OutputSource.ERROR)
            if completion_callback:
                completion_callback()
            return False

        # 收集目标模块的命令
        commands_to_send = []
        for row in range(context.command_table.rowCount()):
            enable, command, comment = context.command_table.get_row_data(row)

            # 检查是否属于目标模块
            if module_name != "全部" and row not in context.modules.get(module_name, []):
                continue

            if enable:  # 只发送勾选的命令
                # 检查是否为特殊指令
                cmd_type_str, param = UIUtils.parse_special_command(command)
                if cmd_type_str:
                    # 处理特殊指令
                    from utils.ui_utils import SpecialCommandType
                    command_type = None
                    for ct in SpecialCommandType:
                        if ct.value == cmd_type_str:
                            command_type = ct
                            break

                    if command_type:
                        commands_to_send.append((row, command, True, command_type, param))
                    else:
                        unescaped_command = UIUtils.unescape_text(command)
                        commands_to_send.append((row, unescaped_command, False))
                else:
                    # 普通命令
                    unescaped_command = UIUtils.unescape_text(command)
                    commands_to_send.append((row, unescaped_command, False))

        if not commands_to_send:
            if hasattr(context, 'output_manager'):
                context.output_manager.append_text(f"警告: 模块 '{module_name}' 中没有启用的命令", OutputSource.SYSTEM)
            if completion_callback:
                completion_callback()
            return True  # 返回 True 表示执行完成（虽然没发送内容）

        if hasattr(context, 'output_manager'):
            context.output_manager.append_text(f"SendMode: 发送模块 '{module_name}' 的内容", OutputSource.SYSTEM)

        # 发送命令（同步方式，使用延迟链）
        def send_module_command(index=0):
            # 检查是否已停止连续发送
            if hasattr(context, 'is_continuous_sending') and not context.is_continuous_sending:
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
                            context.output_manager.append_text(f"SendMode 延迟: {delay_ms}ms", OutputSource.SYSTEM)
                        QTimer.singleShot(int(delay_ms), lambda: send_module_command(index + 1))
                        return
                    except ValueError:
                        if hasattr(context, 'output_manager'):
                            context.output_manager.append_text(f"错误: 无效的延迟参数: {param}", OutputSource.ERROR)
                elif command_type == SpecialCommandType.SENDHEX:
                    if self.execute(command_type, param, context):
                        interval = context.interval_spin.value() if hasattr(context, 'interval_spin') else 100
                        QTimer.singleShot(interval, lambda: send_module_command(index + 1))
                    else:
                        if hasattr(context, 'output_manager'):
                            context.output_manager.append_text(f"错误: SendHex 执行失败: {param}", OutputSource.ERROR)
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
                elif command_type == SpecialCommandType.STOPCONTINUOUS:
                    # 处理 StopContinuous 指令
                    self.execute(command_type, param, context)
                    # 如果是模式0（_skip_to_loop），则跳出 SendMode 但继续外层循环
                    # 如果是模式1，会调用 stop_continuous_sending，完全停止
                    if completion_callback:
                        completion_callback()
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
