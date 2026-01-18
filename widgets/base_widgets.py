import os
import sys
import subprocess
from PyQt5.QtWidgets import QAction, QApplication, QMessageBox
from PyQt5.QtCore import Qt
from utils.ui_utils import UIUtils, OutputSource

class BaseWidgetMixin:
    """基础组件混入类, 提供通用功能"""

    def apply_always_on_top_setting(self, dialog, tool_name, config_manager):
        """
        应用始终置顶设置到对话框

        Args:
            dialog: 要设置的对话框对象
            tool_name: 工具名称
            config_manager: 配置管理器
        """
        tool_config = config_manager.get_tool_config(tool_name)
        is_always_on_top = tool_config.get("always_on_top", False) if tool_config else False

        # 获取当前窗口标志
        current_flags = dialog.windowFlags()

        if is_always_on_top:
            # 添加置顶标志
            new_flags = current_flags | Qt.WindowStaysOnTopHint
        else:
            # 明确移除置顶标志
            new_flags = current_flags & ~Qt.WindowStaysOnTopHint

        # 如果标志改变，需要设置并重新显示
        if new_flags != current_flags:
            dialog.setWindowFlags(new_flags)

        # 无论标志是否改变，都需要显示对话框
        dialog.show()

    def get_main_window(self):
        """递归查找主窗口"""
        if self.__class__.__name__ == 'SerialTool':
            return self

        # 某些情况下 self 可能没有 parent() 方法 (虽然混入的是 QWidget 子类)
        if not hasattr(self, 'parent'):
            return None

        parent = self.parent()
        while parent is not None:
            # 假设主窗口类名为 SerialTool
            if parent.__class__.__name__ == 'SerialTool':
                return parent
            parent = parent.parent()
        return None

    def prepare_tool_launch(self, tool_name, config_manager):
        """
        准备启动工具 - 通用化处理
        如果工具需要独占串口,则关闭当前串口连接

        Returns:
            bool: 是否可以继续启动工具
        """
        main_window = self.get_main_window()

        # 检查工具是否需要独占串口
        if config_manager.tool_requires_serial_port(tool_name):
            if main_window and hasattr(main_window, 'serial_thread') and main_window.serial_thread:
                # 询问用户是否关闭串口
                reply = QMessageBox.question(
                    main_window if main_window else self,
                    "关闭串口",
                    f"{config_manager.get_tool_display_name(tool_name)}需要独占串口。\n是否关闭当前串口连接?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )

                if reply == QMessageBox.Yes:
                    # 关闭串口 - 使用 toggle_serial_connection() 来确保完整的关闭流程
                    if hasattr(main_window, 'toggle_serial_connection'):
                        main_window.toggle_serial_connection()
                    elif hasattr(main_window, 'close_serial'):
                        main_window.close_serial()

                    # 等待串口线程完全停止并释放资源(最多等待5秒)
                    import time
                    wait_count = 0
                    while hasattr(main_window, 'serial_thread') and main_window.serial_thread and wait_count < 50:
                        time.sleep(0.1)  # 等待100ms
                        wait_count += 1
                        # 处理事件队列,确保线程有机会停止
                        QApplication.processEvents()

                    # 额外等待一段时间确保系统完全释放串口句柄(500ms)
                    time.sleep(0.5)

                    if hasattr(main_window, 'output_manager'):
                        main_window.output_manager.append_text(
                            f"串口已关闭以启动{config_manager.get_tool_display_name(tool_name)}",
                            OutputSource.SYSTEM
                        )
                    return True
                else:
                    # 用户取消操作
                    return False

        return True

    def add_number_converter_actions(self, menu, config_manager, selected_text=None):
        """向菜单中添加进制转换器相关的操作"""
        main_window = self.get_main_window()
        tool_name = "number_conversion_dialog"
        
        if config_manager.is_tool_enabled(tool_name) and main_window:
            menu.addSeparator()
            if selected_text:
                # 有选中文本时, 显示HEX和DEC计算
                hex_action = QAction("HEX 计算\tCtrl+H", self)
                hex_action.triggered.connect(lambda: self.open_bit_calculator(selected_text, "HEX"))
                menu.addAction(hex_action)

                dec_action = QAction("DEC 计算", self)
                dec_action.triggered.connect(lambda: self.open_bit_calculator(selected_text, "DEC"))
                menu.addAction(dec_action)
            else:
                # 没有选中文本时, 只显示一个通用的计算选项
                calc_action = QAction("进制转换器", self)
                calc_action.triggered.connect(lambda: self.open_bit_calculator())
                menu.addAction(calc_action)

    def open_bit_calculator(self, text="", conversion_type="HEX"):
        """打开位计算器 (通用逻辑)"""
        # 尝试从自身获取 config_manager, 如果没有则尝试从主窗口获取
        config_manager = getattr(self, 'config_manager', None)
        main_window = self.get_main_window()
        
        if not config_manager and main_window:
            config_manager = getattr(main_window, 'config_manager', None)
            
        if not config_manager:
            return

        tool_name = "number_conversion_dialog"

        # 处理信号传递过来的布尔值
        if isinstance(text, bool):
            text = ""
        
        # 1. 优先检查用户配置的自定义路径
        custom_path = config_manager.get_tool_path(tool_name)
        if custom_path and os.path.exists(custom_path):
            try:
                command = []
                if custom_path.endswith('.py'):
                    command.append(sys.executable)
                command.append(custom_path)

                if text:
                    command.append(text)
                    command.append(conversion_type)

                # 注意: 外部工具通过subprocess启动,无法控制窗口置顶属性(always_on_top配置仅对内置工具有效)
                subprocess.Popen(command, cwd=os.path.dirname(custom_path) if os.path.dirname(custom_path) else None)
                return
            except Exception as e:
                if main_window and hasattr(main_window, 'output_manager'):
                    main_window.output_manager.append_text(f"错误: 通过自定义路径打开位计算器失败: {str(e)}", OutputSource.ERROR)
                # 失败后继续尝试内置逻辑
        
        # 2. 如果没有自定义路径或启动失败，尝试内置逻辑 (作为模块导入)
        try:
            # 获取基础路径 (处理打包后的情况)
            if getattr(sys, 'frozen', False):
                # 打包后的路径
                base_path = sys._MEIPASS
                import_root = base_path
            else:
                # 开发环境路径
                # 假设 base_widgets.py 在 widgets/ 目录下
                current_dir = os.path.dirname(os.path.abspath(__file__))
                
                # 向上查找，直到找到包含 number_converter_project 的目录
                # 这样比固定层级更鲁棒
                search_path = current_dir
                import_root = None
                for _ in range(4):  # 最多向上查找4层 (widgets -> project -> parent)
                    potential_root = os.path.join(search_path, 'number_converter_project')
                    if os.path.isdir(potential_root):
                        import_root = search_path
                        break
                    search_path = os.path.dirname(search_path)
                
                if not import_root:
                    # 如果没找到，回退到默认的父目录逻辑
                    base_path = os.path.dirname(current_dir)
                    import_root = os.path.dirname(base_path)
            
            # 将导入根路径加入 sys.path
            if import_root and import_root not in sys.path:
                sys.path.insert(0, import_root)
            
            # 动态导入
            # 始终优先作为包导入，这样内部的相对导入才能正常工作，且不会与当前工程的 widgets 目录冲突
            try:
                from number_converter_project.number_conversion_dialog import NumberConversionDialog
            except ImportError as e:
                # 收集调试信息
                debug_info = [
                    f"Error: {str(e)}",
                    f"Import Root: {import_root}",
                    f"Frozen: {getattr(sys, 'frozen', False)}",
                    f"CWD: {os.getcwd()}",
                    f"File: {os.path.abspath(__file__)}"
                ]
                
                # 如果作为包导入失败，尝试直接导入模块 (兼容旧版本或特殊结构)
                if import_root:
                    calc_project_path = os.path.join(import_root, 'number_converter_project')
                    if calc_project_path not in sys.path:
                        sys.path.insert(0, calc_project_path)
                    
                    debug_info.append(f"Calc Project Path: {calc_project_path}")
                    debug_info.append(f"Path exists: {os.path.exists(calc_project_path)}")
                
                # 打印调试信息到输出管理器 (如果有)
                if main_window and hasattr(main_window, 'output_manager'):
                    main_window.output_manager.append_text("调试信息: " + " | ".join(debug_info), OutputSource.SYSTEM)
                
                from number_conversion_dialog import NumberConversionDialog
            
            # 创建并显示对话框
            # 注意: 这里需要一个父窗口, 优先使用 main_window
            # 获取配置的默认参数
            default_params = config_manager.get_number_conversion_params()
            self.calc_dialog = NumberConversionDialog(
                selected_text=text,
                conversion_type=conversion_type,
                data_width=default_params.get("data_width", "DWORD"),
                parent=main_window
            )
            # 应用置顶设置（内部会处理窗口显示）
            self.apply_always_on_top_setting(self.calc_dialog, tool_name, config_manager)
            
        except ImportError as e:
            QMessageBox.information(main_window if main_window else self, "提示", f"位计算器模块未找到或未打包。\n错误信息: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 未能加载位计算器模块: {str(e)}", OutputSource.ERROR)
        except Exception as e:
            QMessageBox.critical(main_window if main_window else self, "错误", f"打开位计算器失败: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 打开位计算器失败: {str(e)}", OutputSource.ERROR)

    def open_bin_hex_converter(self):
        """打开 Bin to Hex 转换器"""
        # 获取 config_manager
        config_manager = getattr(self, 'config_manager', None)
        main_window = self.get_main_window()

        if not config_manager and main_window:
            config_manager = getattr(main_window, 'config_manager', None)

        if not config_manager:
            return

        tool_name = "bin_hex_converter"

        # 1. 优先检查用户配置的自定义路径
        custom_path = config_manager.get_tool_path(tool_name)
        if custom_path and os.path.exists(custom_path):
            try:
                command = []
                if custom_path.endswith('.py'):
                    command.append(sys.executable)
                command.append(custom_path)

                # 注意: 外部工具通过subprocess启动,无法控制窗口置顶属性(always_on_top配置仅对内置工具有效)
                subprocess.Popen(command, cwd=os.path.dirname(custom_path) if os.path.dirname(custom_path) else None)
                return
            except Exception as e:
                if main_window and hasattr(main_window, 'output_manager'):
                    main_window.output_manager.append_text(f"错误: 通过自定义路径打开 Bin to Hex 转换器失败: {str(e)}", OutputSource.ERROR)

        # 2. 尝试内置逻辑 (作为模块导入)
        try:
            # 获取基础路径
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
                import_root = base_path
            else:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                search_path = current_dir
                import_root = None

                # 向上查找包含 bin_hex_converter_project 的目录
                for _ in range(4):
                    potential_root = os.path.join(search_path, 'bin_hex_converter_project')
                    if os.path.isdir(potential_root):
                        import_root = search_path
                        break
                    search_path = os.path.dirname(search_path)

                if not import_root:
                    base_path = os.path.dirname(current_dir)
                    import_root = os.path.dirname(base_path)

            # 将导入根路径加入 sys.path
            if import_root and import_root not in sys.path:
                sys.path.insert(0, import_root)

            # 动态导入
            try:
                from bin_hex_converter_project.bin_hex_converter_dialog import BinHexConverterDialog
            except ImportError:
                if import_root:
                    converter_project_path = os.path.join(import_root, 'bin_hex_converter_project')
                    if converter_project_path not in sys.path:
                        sys.path.insert(0, converter_project_path)
                from bin_hex_converter_dialog import BinHexConverterDialog

            # 获取配置参数
            params = config_manager.get_bin_hex_converter_params()

            # 创建并显示对话框
            self.bin_hex_dialog = BinHexConverterDialog(
                parent=main_window,
                data_width=params["data_width"],
                bytes_per_row=params["bytes_per_row"],
                byteorder=params["byteorder"],
                uppercase=params["uppercase"]
            )
            # 应用置顶设置（内部会处理窗口显示）
            self.apply_always_on_top_setting(self.bin_hex_dialog, tool_name, config_manager)

        except ImportError as e:
            QMessageBox.information(main_window if main_window else self, "提示", f"Bin to Hex 转换器模块未找到或未打包。\n错误信息: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 未能加载 Bin to Hex 转换器模块: {str(e)}", OutputSource.ERROR)
        except Exception as e:
            QMessageBox.critical(main_window if main_window else self, "错误", f"打开 Bin to Hex 转换器失败: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 打开 Bin to Hex 转换器失败: {str(e)}", OutputSource.ERROR)

    def open_firmware_downloader(self):
        """打开固件下载工具"""
        # 获取 config_manager
        config_manager = getattr(self, 'config_manager', None)
        main_window = self.get_main_window()

        if not config_manager and main_window:
            config_manager = getattr(main_window, 'config_manager', None)

        if not config_manager:
            return

        tool_name = "firmware_downloader"

        # **关键修复：在关闭串口之前先获取串口配置**
        # 获取当前串口配置（从主窗口传递）
        port_config = {}
        if main_window:
            # 从主窗口的串口线程获取配置
            if hasattr(main_window, 'serial_thread') and main_window.serial_thread:
                serial_thread = main_window.serial_thread
                port_config = {
                    'port': serial_thread.port,
                    'baudrate': serial_thread.baudrate,
                    'bytesize': serial_thread.bytesize,
                    'parity': serial_thread.parity,
                    'stopbits': serial_thread.stopbits
                }
            # 如果串口未打开，从UI控件获取配置
            elif hasattr(main_window, 'port_combo') and hasattr(main_window, 'baud_combo'):
                import serial
                port_text = main_window.port_combo.currentText()
                port = port_text.split(' ')[0] if port_text else ''

                parity_map = {"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN,
                             "Odd": serial.PARITY_ODD, "Mark": serial.PARITY_MARK}
                parity = parity_map.get(main_window.parity_combo.currentText(), serial.PARITY_NONE)

                stopbits_map = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
                               "2": serial.STOPBITS_TWO}
                stopbits = stopbits_map.get(main_window.stop_bits_combo.currentText(), serial.STOPBITS_ONE)

                port_config = {
                    'port': port,
                    'baudrate': int(main_window.baud_combo.currentText()) if main_window.baud_combo.currentText() else 115200,
                    'bytesize': int(main_window.data_bits_combo.currentText()) if main_window.data_bits_combo.currentText() else 8,
                    'parity': parity,
                    'stopbits': stopbits
                }

        # 通用化处理: 检查是否需要关闭串口（在获取配置之后执行）
        if not self.prepare_tool_launch(tool_name, config_manager):
            return  # 用户取消了操作

        # 1. 优先检查用户配置的自定义路径
        custom_path = config_manager.get_tool_path(tool_name)
        if custom_path and os.path.exists(custom_path):
            try:
                command = []
                if custom_path.endswith('.py'):
                    command.append(sys.executable)
                command.append(custom_path)
                        
                # 传递串口参数和所有配置参数（注意：外部工具通过命令行参数接收配置）
                if port_config.get('port'):
                    import serial
                    # 获取所有配置参数
                    params = config_manager.get_firmware_downloader_params()

                    # 将 parity 和 stopbits 转换为字符串表示
                    parity_reverse_map = {
                        serial.PARITY_NONE: 'N',
                        serial.PARITY_EVEN: 'E',
                        serial.PARITY_ODD: 'O',
                        serial.PARITY_MARK: 'M',
                        serial.PARITY_SPACE: 'S'
                    }
                    stopbits_reverse_map = {
                        serial.STOPBITS_ONE: '1',
                        serial.STOPBITS_ONE_POINT_FIVE: '1.5',
                        serial.STOPBITS_TWO: '2'
                    }

                    parity_str = parity_reverse_map.get(port_config.get('parity', serial.PARITY_NONE), 'N')
                    stopbits_str = stopbits_reverse_map.get(port_config.get('stopbits', serial.STOPBITS_ONE), '1')

                    # 基础串口配置
                    command.extend([
                        '--port', port_config['port'],
                        '--baudrate', str(port_config['baudrate']),
                        '--bytesize', str(port_config.get('bytesize', 8)),
                        '--parity', parity_str,
                        '--stopbits', stopbits_str
                    ])

                    # 初始文件
                    initial_file = params.get("initial_file", "")
                    if initial_file and os.path.exists(initial_file):
                        command.extend(['--file', initial_file])

                    # 下载基本配置
                    command.extend([
                        '--packet-size', str(params['packet_size']),
                        '--start-command', params['start_command']
                    ])

                    if params.get('add_packet_crc'):
                        command.append('--add-packet-crc')
                        command.extend(['--packet-crc-type', params['packet_crc_type']])

                    # 开始命令 ACK 配置
                    if params.get('wait_start_ack'):
                        command.append('--wait-start-ack')
                        command.extend([
                            '--start-ack-timeout', str(params['start_ack_timeout']),
                            '--start-ack-check-mode', params['start_ack_check_mode']
                        ])
                        if params.get('start_ack_check_length'):
                            command.append('--start-ack-check-length')
                            command.extend(['--start-ack-expected-length', str(params['start_ack_expected_length'])])
                        if params.get('start_ack_check_data'):
                            command.append('--start-ack-check-data')
                            command.extend([
                                '--start-ack-expected-data', params['start_ack_expected_data'],
                                '--start-ack-data-format', params['start_ack_data_format']
                            ])

                    # 数据包 ACK 配置
                    if params.get('wait_packet_ack'):
                        command.append('--wait-packet-ack')
                        command.extend([
                            '--packet-ack-timeout', str(params['packet_ack_timeout']),
                            '--packet-ack-check-mode', params['packet_ack_check_mode']
                        ])
                        if params.get('packet_ack_check_length'):
                            command.append('--packet-ack-check-length')
                            command.extend(['--packet-ack-expected-length', str(params['packet_ack_expected_length'])])
                        if params.get('packet_ack_check_data'):
                            command.append('--packet-ack-check-data')
                            command.extend([
                                '--packet-ack-expected-data', params['packet_ack_expected_data'],
                                '--packet-ack-data-format', params['packet_ack_data_format']
                            ])
                        if params.get('packet_ack_check_crc'):
                            command.append('--packet-ack-check-crc')
                            command.extend(['--packet-ack-crc-type', params['packet_ack_crc_type']])

                    # 末尾数据包 ACK 配置
                    if params.get('wait_last_packet_ack'):
                        command.append('--wait-last-packet-ack')
                        command.extend([
                            '--last-packet-ack-timeout', str(params['last_packet_ack_timeout']),
                            '--last-packet-ack-check-mode', params['last_packet_ack_check_mode']
                        ])
                        if params.get('last_packet_ack_check_length'):
                            command.append('--last-packet-ack-check-length')
                            command.extend(['--last-packet-ack-expected-length', str(params['last_packet_ack_expected_length'])])
                        if params.get('last_packet_ack_check_data'):
                            command.append('--last-packet-ack-check-data')
                            command.extend([
                                '--last-packet-ack-expected-data', params['last_packet_ack_expected_data'],
                                '--last-packet-ack-data-format', params['last_packet_ack_data_format']
                            ])
                        if params.get('last_packet_ack_check_crc'):
                            command.append('--last-packet-ack-check-crc')
                            command.extend(['--last-packet-ack-crc-type', params['last_packet_ack_crc_type']])

                    # 结尾字符串配置
                    if params.get('send_end_string'):
                        command.append('--send-end-string')
                        command.extend(['--end-string', params['end_string']])

                # 注意: 外部工具通过subprocess启动,无法控制窗口置顶属性(always_on_top配置仅对内置工具有效)
                subprocess.Popen(command, cwd=os.path.dirname(custom_path) if os.path.dirname(custom_path) else None)
                return
            except Exception as e:
                if main_window and hasattr(main_window, 'output_manager'):
                    main_window.output_manager.append_text(f"错误: 通过自定义路径打开固件下载工具失败: {str(e)}", OutputSource.ERROR)

        # 2. 尝试内置逻辑 (作为模块导入)
        try:
            # 获取基础路径
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
                import_root = base_path
            else:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                search_path = current_dir
                import_root = None

                # 向上查找包含 firmware_downloader_project 的目录
                for _ in range(4):
                    potential_root = os.path.join(search_path, 'firmware_downloader_project')
                    if os.path.isdir(potential_root):
                        import_root = search_path
                        break
                    search_path = os.path.dirname(search_path)

                if not import_root:
                    base_path = os.path.dirname(current_dir)
                    import_root = os.path.dirname(base_path)

            # 将导入根路径加入 sys.path
            if import_root and import_root not in sys.path:
                sys.path.insert(0, import_root)

            # 动态导入
            try:
                from firmware_downloader_project.firmware_downloader_dialog import FirmwareDownloaderDialog
            except ImportError:
                if import_root:
                    downloader_project_path = os.path.join(import_root, 'firmware_downloader_project')
                    if downloader_project_path not in sys.path:
                        sys.path.insert(0, downloader_project_path)
                from firmware_downloader_dialog import FirmwareDownloaderDialog

            # 获取配置参数
            params = config_manager.get_firmware_downloader_params()

            # 转换 parity 和 stopbits 为字符串/数字格式（用于内部工具）
            import serial
            parity_value = port_config.get('parity', serial.PARITY_NONE)
            stopbits_value = port_config.get('stopbits', serial.STOPBITS_ONE)

            # parity 转换映射
            parity_reverse_map = {
                serial.PARITY_NONE: 'N',
                serial.PARITY_EVEN: 'E',
                serial.PARITY_ODD: 'O',
                serial.PARITY_MARK: 'M',
                serial.PARITY_SPACE: 'S'
            }
            # stopbits 转换映射（转为浮点数）
            stopbits_reverse_map = {
                serial.STOPBITS_ONE: 1.0,
                serial.STOPBITS_ONE_POINT_FIVE: 1.5,
                serial.STOPBITS_TWO: 2.0
            }

            parity_str = parity_reverse_map.get(parity_value, 'N')
            stopbits_float = stopbits_reverse_map.get(stopbits_value, 1.0)

            # 创建并显示对话框，传递所有参数
            self.firmware_downloader_dialog = FirmwareDownloaderDialog(
                parent=main_window,
                # 初始文件
                initial_file=params.get("initial_file", ""),
                # 串口配置
                port=port_config.get('port', ''),
                baudrate=port_config.get('baudrate', 115200),
                bytesize=port_config.get('bytesize', 8),
                parity=parity_str,
                stopbits=stopbits_float,
                # 下载基本配置
                packet_size=params["packet_size"],
                start_command=params["start_command"],
                add_packet_crc=params["add_packet_crc"],
                packet_crc_type=params["packet_crc_type"],
                # 开始命令 ACK 配置
                wait_start_ack=params["wait_start_ack"],
                start_ack_timeout=params["start_ack_timeout"],
                start_ack_check_length=params["start_ack_check_length"],
                start_ack_expected_length=params["start_ack_expected_length"],
                start_ack_check_data=params["start_ack_check_data"],
                start_ack_expected_data=params["start_ack_expected_data"],
                start_ack_data_format=params["start_ack_data_format"],
                start_ack_check_mode=params["start_ack_check_mode"],
                # 数据包 ACK 配置
                wait_packet_ack=params["wait_packet_ack"],
                packet_ack_timeout=params["packet_ack_timeout"],
                packet_ack_check_length=params["packet_ack_check_length"],
                packet_ack_expected_length=params["packet_ack_expected_length"],
                packet_ack_check_data=params["packet_ack_check_data"],
                packet_ack_expected_data=params["packet_ack_expected_data"],
                packet_ack_data_format=params["packet_ack_data_format"],
                packet_ack_check_crc=params["packet_ack_check_crc"],
                packet_ack_crc_type=params["packet_ack_crc_type"],
                packet_ack_check_mode=params["packet_ack_check_mode"],
                # 末尾数据包 ACK 配置
                wait_last_packet_ack=params["wait_last_packet_ack"],
                last_packet_ack_timeout=params["last_packet_ack_timeout"],
                last_packet_ack_check_length=params["last_packet_ack_check_length"],
                last_packet_ack_expected_length=params["last_packet_ack_expected_length"],
                last_packet_ack_check_data=params["last_packet_ack_check_data"],
                last_packet_ack_expected_data=params["last_packet_ack_expected_data"],
                last_packet_ack_data_format=params["last_packet_ack_data_format"],
                last_packet_ack_check_crc=params["last_packet_ack_check_crc"],
                last_packet_ack_crc_type=params["last_packet_ack_crc_type"],
                last_packet_ack_check_mode=params["last_packet_ack_check_mode"],
                # 结尾字符串配置
                send_end_string=params["send_end_string"],
                end_string=params["end_string"]
            )
            # 应用置顶设置（内部会处理窗口显示）
            self.apply_always_on_top_setting(self.firmware_downloader_dialog, tool_name, config_manager)

        except ImportError as e:
            QMessageBox.information(main_window if main_window else self, "提示", f"固件下载工具模块未找到或未打包。\n错误信息: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 未能加载固件下载工具模块: {str(e)}", OutputSource.ERROR)
        except Exception as e:
            QMessageBox.critical(main_window if main_window else self, "错误", f"打开固件下载工具失败: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 打开固件下载工具失败: {str(e)}", OutputSource.ERROR)

    def handle_common_shortcuts(self, event):
        """处理通用的快捷键逻辑"""
        # Ctrl+H: 唤醒 HEX 计算
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_H:
            selected_text = ""
            if hasattr(self, 'selectedText'): # QLineEdit
                selected_text = self.selectedText().strip()
            elif hasattr(self, 'textCursor'): # QTextEdit / QTextBrowser
                selected_text = self.textCursor().selectedText().strip()
            
            if selected_text:
                self.open_bit_calculator(selected_text, "HEX")
                return True # 表示事件已处理
        return False
