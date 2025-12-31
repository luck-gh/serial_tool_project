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
            SpecialCommandType.SENDMODE: self._handle_sendmode
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

    def get_command_names(self):
        """获取所有指令名称"""
        return [cmd.value for cmd in self.commands.keys()]
