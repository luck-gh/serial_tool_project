import os
import sys
import subprocess
from PyQt5.QtWidgets import QAction, QApplication, QMessageBox
from PyQt5.QtCore import Qt
from utils.ui_utils import UIUtils, OutputSource

class BaseWidgetMixin:
    """基础组件混入类, 提供通用功能"""
    
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
            self.calc_dialog = NumberConversionDialog(selected_text=text, conversion_type=conversion_type, parent=main_window)
            self.calc_dialog.show()
            
        except ImportError as e:
            QMessageBox.information(main_window if main_window else self, "提示", f"位计算器模块未找到或未打包。\n错误信息: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 未能加载位计算器模块: {str(e)}", OutputSource.ERROR)
        except Exception as e:
            QMessageBox.critical(main_window if main_window else self, "错误", f"打开位计算器失败: {str(e)}")
            if main_window and hasattr(main_window, 'output_manager'):
                main_window.output_manager.append_text(f"错误: 打开位计算器失败: {str(e)}", OutputSource.ERROR)

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
