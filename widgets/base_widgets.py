#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
基础控件混入模块, 负责外部工具启动, 数值转换器调用和共享控件辅助逻辑.

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import os
import sys
import subprocess
from PyQt5.QtWidgets import QAction, QApplication, QMessageBox
from PyQt5.QtCore import Qt
from core.command_types import OutputSource
from utils.ui_utils import UIUtils

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

        # 如果标志改变,需要设置并重新显示
        if new_flags != current_flags:
            dialog.setWindowFlags(new_flags)

        # 无论标志是否改变,都需要显示对话框
        dialog.show()

    def show_tool_dialog(self, dialog, tool_name, config_manager, main_window=None):
        """显示工具窗口, 默认不绑定主窗口层级."""
        dialog.setParent(None)
        dialog.setWindowModality(Qt.NonModal)
        if hasattr(dialog, "setModal"):
            dialog.setModal(False)

        flags = dialog.windowFlags()
        flags = (flags & ~Qt.WindowType_Mask) | Qt.Window
        dialog.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)

        if main_window and not main_window.windowIcon().isNull():
            dialog.setWindowIcon(main_window.windowIcon())
        self._remember_tool_dialog(dialog)
        self.apply_always_on_top_setting(dialog, tool_name, config_manager)

    def _remember_tool_dialog(self, dialog):
        """保留独立工具窗口引用, 支持同类工具多开."""
        if not hasattr(self, "_open_tool_dialogs"):
            self._open_tool_dialogs = []
        self._open_tool_dialogs.append(dialog)
        dialog.destroyed.connect(lambda _=None, closed_dialog=dialog: self._forget_tool_dialog(closed_dialog))

    def _forget_tool_dialog(self, dialog):
        """从窗口引用列表移除已销毁工具窗口,避免继续持有失效的 Qt 对象."""
        if not hasattr(self, "_open_tool_dialogs"):
            return
        self._open_tool_dialogs = [
            open_dialog for open_dialog in self._open_tool_dialogs
            if open_dialog is not dialog
        ]

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
        """处理普通外部工具的串口独占提示,不用于固件下载任务的统一门禁.

        Args:
            tool_name (str): 工具注册表名称,用于查询显示名和独占要求.
            config_manager (ConfigManager): 工具配置来源.

        Returns:
            bool: 用户未拒绝且准备流程结束时为 True.当前旧工具路径在最多五秒
                轮询及额外 500 ms 等待后返回,不保证线程确已退出;不能作为可靠
                串口租约使用.固件入口使用主窗口的显式关闭结果和线程退出检查.
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

                    # 最多等待串口线程 5 秒;等待结束不代表已确认线程退出.
                    import time
                    wait_count = 0
                    while hasattr(main_window, 'serial_thread') and main_window.serial_thread and wait_count < 50:
                        time.sleep(0.1)  # 等待100ms
                        wait_count += 1
                        # 处理事件队列,确保线程有机会停止
                        QApplication.processEvents()

                    # 再留出 500ms 供系统清理;此等待本身不能保证句柄已经释放.
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
        """根据选中文本打开进制转换工具,优先使用有效的外部工具路径.

        Args:
            text (str): 待转换文本,默认空;按钮传入的 bool 会被当作空文本.
            conversion_type (str): 初始进制,默认 HEX,可使用 DEC.

        外部工具通过子进程启动,不受内置窗口的置顶配置控制.内置工具按开发/
        打包目录定位,优先包导入以避免与当前 widgets 模块冲突;独立窗口引用
        由调用方保留.已有导入回退仅按当前实现执行,本方法不修改工具代码.
        """
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
        # 2. 如果没有自定义路径或启动失败,尝试内置逻辑 (作为模块导入)
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
                
                # 向上查找,直到找到包含 number_converter_project 的目录
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
                    # 如果没找到,回退到默认的父目录逻辑
                    base_path = os.path.dirname(current_dir)
                    import_root = os.path.dirname(base_path)
            
            # 将导入根路径加入 sys.path
            if import_root and import_root not in sys.path:
                sys.path.insert(0, import_root)
            
            # 动态导入
            # 始终优先作为包导入,这样内部的相对导入才能正常工作,且不会与当前工程的 widgets 目录冲突
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
                
                # 如果作为包导入失败,尝试直接导入模块 (兼容旧版本或特殊结构)
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
            
            # 创建并显示独立窗口; 不传主窗口作为 parent, 避免工具窗口始终压在主窗口上方.
            # 获取配置的默认参数
            default_params = config_manager.get_number_conversion_params()
            self.calc_dialog = NumberConversionDialog(
                selected_text=text,
                conversion_type=conversion_type,
                data_width=default_params.get("data_width", "DWORD"),
                parent=None
            )
            self.calc_dialog.setWindowTitle("HEX 计算_GHowe" if conversion_type == "HEX" else "DEC 计算_GHowe")
            self.show_tool_dialog(self.calc_dialog, tool_name, config_manager, main_window)
            
        except ImportError as e:
            QMessageBox.information(main_window if main_window else self, "提示", f"位计算器模块未找到或未打包。\n错误信息: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 未能加载位计算器模块: {str(e)}", OutputSource.ERROR)
        except Exception as e:
            QMessageBox.critical(main_window if main_window else self, "错误", f"打开位计算器失败: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 打开位计算器失败: {str(e)}", OutputSource.ERROR)

    def open_bin_hex_converter(self):
        """打开 BIN/HEX 转换工具,优先使用配置的外部程序,否则定位内置模块.

        内置工具使用独立窗口并应用工具配置;外部子进程不受内置窗口置顶设置
        控制.文件转换操作由所打开工具负责,当前入口不转换文件.
        """
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

            # 创建并显示独立窗口; 不传主窗口作为 parent, 避免工具窗口始终压在主窗口上方.
            self.bin_hex_dialog = BinHexConverterDialog(
                parent=None,
                data_width=params["data_width"],
                bytes_per_row=params["bytes_per_row"],
                byteorder=params["byteorder"],
                uppercase=params["uppercase"]
            )
            self.show_tool_dialog(self.bin_hex_dialog, tool_name, config_manager, main_window)

        except ImportError as e:
            QMessageBox.information(main_window if main_window else self, "提示", f"Bin to Hex 转换器模块未找到或未打包。\n错误信息: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 未能加载 Bin to Hex 转换器模块: {str(e)}", OutputSource.ERROR)
        except Exception as e:
            QMessageBox.critical(main_window if main_window else self, "错误", f"打开 Bin to Hex 转换器失败: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 打开 Bin to Hex 转换器失败: {str(e)}", OutputSource.ERROR)

    def open_firmware_downloader(self):
        """先打开完整下载窗口,由窗口的开始按钮提交共用下载任务."""
        from core.firmware_download import get_firmware_downloader_class
        main_window = self.get_main_window()
        if not main_window:
            return
        if main_window.serial_operation_busy():
            main_window._firmware_download_log("ERROR", "已有固件任务占用串口")
            return
        try:
            get_firmware_downloader_class()
            from dialogs.firmware_download_dialog import ManagedFirmwareDownloadDialog
        except ImportError as exc:
            QMessageBox.warning(main_window, "固件下载工具不可用", str(exc))
            return
        dialog = ManagedFirmwareDownloadDialog(main_window)
        if main_window.config_manager.get_tool_config("firmware_downloader").get("always_on_top", False):
            dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        try:
            dialog.exec_()
        finally:
            dialog.deleteLater()

    def handle_common_shortcuts(self, event):
        """识别 Ctrl+H 并将输入控件选中文本交给 HEX 计算器,返回是否处理该事件."""
        # Ctrl+H: 唤醒 HEX 计算
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_H:
            selected_text = ""
            if hasattr(self, 'selectedText'):  # QLineEdit
                selected_text = self.selectedText().strip()
            elif hasattr(self, 'textCursor'):  # QTextEdit / QTextBrowser
                selected_text = self.textCursor().selectedText().strip()
            
            if selected_text:
                self.open_bit_calculator(selected_text, "HEX")
                return True  # 表示事件已处理
        return False
