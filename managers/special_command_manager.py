from utils.ui_utils import SpecialCommandType
from managers.config_manager import ConfigManager

class SpecialCommandManager:
    """特殊指令管理器"""
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.commands = {
            SpecialCommandType.MODE: self._handle_mode,
            SpecialCommandType.DELAY: self._handle_delay
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

    def get_command_names(self):
        """获取所有指令名称"""
        return [cmd.value for cmd in self.commands.keys()]
