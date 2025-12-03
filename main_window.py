import sys
import os
import csv
import re
from collections import OrderedDict

import serial
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QGroupBox, QLabel, QComboBox, QPushButton, QCheckBox,
                             QTextBrowser, QTableWidget, QTableWidgetItem, QHeaderView,
                             QLineEdit, QSpinBox, QDoubleSpinBox, QScrollArea, QFrame,
                             QMessageBox, QFileDialog, QDialog, QDialogButtonBox, QTextEdit,
                             QSplitter, QMenu, QAction, QSizePolicy, QStyleFactory, QInputDialog)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer, QEvent
from PyQt5.QtGui import QFont, QColor, QTextCursor, QIcon, QPalette

from utils.ui_utils import UIUtils, Colors, resource_path
from widgets.custom_widgets import CustomTextBrowser, OutputSource
from widgets.command_widgets import CommandTableWidget
from managers.output_manager import OutputManager
from managers.special_command_manager import SpecialCommandManager
from core.serial_thread import SerialThread
from managers.config_manager import ConfigManager
from dialogs.config_dialog import ConfigDialog

class SerialTool(QMainWindow):
    """串口调试工具主窗口"""
    def __init__(self, tool_version="0.0.0", tool_version_date="N/A", exe_name="main"):
        super().__init__()
        self.tool_version = tool_version
        self.tool_version_date = tool_version_date
        self.exe_name = exe_name
        self.serial_thread = None
        self.is_connected = False
        self.send_count = 0
        self.receive_count = 0
        self.continuous_timer = QTimer()
        self.continuous_timer.timeout.connect(self.send_continuous_commands)
        self.is_continuous_sending = False
        self.current_module = "全部"
        self.modules = OrderedDict()  # 存储模块信息
        
        # 使用可执行文件名来构建配置文件名
        config_file = f"{self.exe_name}_config.json"
        self.config_manager = ConfigManager(
            tool_version=self.tool_version,
            tool_version_date=self.tool_version_date,
            config_file=config_file
        )
        self.special_command_manager = SpecialCommandManager(self.config_manager)

        self.init_ui()
        self.refresh_ports()

        # 添加10条初始命令
        self.add_initial_commands(10)

    def add_initial_commands(self, addlen):
        """添加10条空的初始命令"""
        for i in range(addlen):
            row = self.command_table.rowCount()
            # 直接添加行, add_command_row内部已经处理了按钮连接, 不需要再次连接
            self.command_table.add_command_row(False, "", f"", row)

    def eventFilter(self, obj, event):
        """滚轮事件过滤器, 完全防止滚轮改变下拉框值"""
        if event.type() == QEvent.Wheel and isinstance(obj, QComboBox):
            # 完全忽略所有QComboBox的滚轮事件
            return True
        return False

    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("串口调试助手_GHowe")
        self.setGeometry(100, 100, 1400, 800)
        self.setWindowIcon(QIcon(resource_path("resources/HOWE_LOGO.ico")))

        # 设置等宽字体
        font = QFont("Consolas", 10)
        QApplication.setFont(font)

        # 设置现代风格
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: #f5f5f5;
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                margin-top: 1ex;
                padding-top: 12px;
                background-color: white;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px 0 8px;
                color: #333333;
            }}
            QPushButton {{
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:pressed {{
                opacity: 0.8;
            }}
            QComboBox {{
                padding: 6px;
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
                color: black;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left-width: 1px;
                border-left-color: #cccccc;
                border-left-style: solid;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #666666;
                width: 0px;
                height: 0px;
            }}
            QComboBox QAbstractItemView {{
                border: 1px solid #cccccc;
                background-color: white;
                color: black;
                selection-background-color: #2196F3;
                selection-color: white;
                outline: 0;
            }}
            QComboBox QAbstractItemView::item {{
                height: 25px;
                padding: 5px;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: #2196F3;
                color: white;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: #E3F2FD;
                color: black;
            }}
            QLineEdit, QSpinBox {{
                padding: 6px;
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
                color: black;
            }}
            QTextBrowser {{
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
                font-family: 'Consolas', 'Monaco', monospace;
            }}
            QTableWidget {{
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
                gridline-color: #e0e0e0;
            }}
            QHeaderView::section {{
                background-color: #e8e8e8;
                padding: 6px;
                border: none;
                border-right: 1px solid #d0d0d0;
                font-weight: bold;
            }}
            QCheckBox {{
                spacing: 5px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
            }}
        """)

        # 中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 创建水平分割器
        self.h_splitter = QSplitter(Qt.Horizontal)

        # 左侧设置面板
        self.left_panel = self.create_left_panel()
        self.left_panel.setMaximumWidth(400)  # 设置最大宽度为500像素
        self.h_splitter.addWidget(self.left_panel)

        # 右侧数据交互面板
        self.right_panel = self.create_right_panel()
        self.h_splitter.addWidget(self.right_panel)

        # 设置分割器比例
        self.h_splitter.setSizes([300, 700])
        self.h_splitter.setHandleWidth(2)

        main_layout.addWidget(self.h_splitter)

        # 初始化输出管理器
        self.output_manager = OutputManager(
            self.receive_browser,
            lambda: self.timestamp_check.isChecked(),
            lambda: self.show_send_check.isChecked(),
            self.get_send_color
        )

        # 连接信号
        self.connect_signals()

    def create_left_panel(self):
        """创建左侧设置面板 (可滚动) """
        # 滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # 设置面板容器
        panel = QWidget()
        scroll_area.setWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # 基本设置
        basic_group = QGroupBox("基本设置")
        basic_layout = QVBoxLayout(basic_group)
        basic_layout.setSpacing(8)

        # 添加刷新按钮
        self.refresh_ports_btn = QPushButton("刷新")
        self.refresh_ports_btn.setFixedWidth(60)
        self.refresh_ports_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)

        # 下拉菜单的最小宽度
        minimumWidth = 180
        # 端口选择
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("端口:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.activated.connect(self.refresh_ports)
        self.port_combo.installEventFilter(self)            # 禁用滚轮
        self.port_combo.setMinimumWidth(minimumWidth)       # 设置最小宽度
        port_layout.addWidget(self.refresh_ports_btn)
        port_layout.addWidget(self.port_combo)
        basic_layout.addLayout(port_layout)

        # 波特率
        baud_layout = QHBoxLayout()
        baud_layout.addWidget(QLabel("波特率:"))
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["9600", "115200", "57600", "38400", "19200", "4800"])
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.installEventFilter(self)            # 禁用滚轮
        self.baud_combo.setMinimumWidth(minimumWidth)       # 设置最小宽度
        baud_layout.addWidget(self.baud_combo)
        basic_layout.addLayout(baud_layout)

        # 数据位
        data_bits_layout = QHBoxLayout()
        data_bits_layout.addWidget(QLabel("数据位:"))
        self.data_bits_combo = QComboBox()
        self.data_bits_combo.addItems(["5", "6", "7", "8"])
        self.data_bits_combo.setCurrentText("8")
        self.data_bits_combo.installEventFilter(self)       # 禁用滚轮
        self.data_bits_combo.setMinimumWidth(minimumWidth)  # 设置最小宽度
        data_bits_layout.addWidget(self.data_bits_combo)
        basic_layout.addLayout(data_bits_layout)

        # 校验位
        parity_layout = QHBoxLayout()
        parity_layout.addWidget(QLabel("校验位:"))
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(["None", "Even", "Odd", "Mark"])
        self.parity_combo.setCurrentText("None")
        self.parity_combo.installEventFilter(self)          # 禁用滚轮
        self.parity_combo.setMinimumWidth(minimumWidth)     # 设置最小宽度
        parity_layout.addWidget(self.parity_combo)
        basic_layout.addLayout(parity_layout)

        # 停止位
        stop_bits_layout = QHBoxLayout()
        stop_bits_layout.addWidget(QLabel("停止位:"))
        self.stop_bits_combo = QComboBox()
        self.stop_bits_combo.addItems(["1", "1.5", "2"])
        self.stop_bits_combo.setCurrentText("1")
        self.stop_bits_combo.installEventFilter(self)       # 禁用滚轮
        self.stop_bits_combo.setMinimumWidth(minimumWidth)  # 设置最小宽度
        stop_bits_layout.addWidget(self.stop_bits_combo)
        basic_layout.addLayout(stop_bits_layout)

        # 打开串口按钮
        self.connect_btn = QPushButton("打开串口")
        self.connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """)
        basic_layout.addWidget(self.connect_btn)

        layout.addWidget(basic_group)

        # 接收设置
        receive_group = QGroupBox("接收设置")
        receive_layout = QVBoxLayout(receive_group)
        receive_layout.setSpacing(8)

        self.save_btn = QPushButton("保存数据")
        self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)

        self.clear_receive_btn = QPushButton("清空数据")
        self.clear_receive_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)

        receive_layout.addWidget(self.save_btn)
        receive_layout.addWidget(self.clear_receive_btn)

        layout.addWidget(receive_group)

        # 发送设置
        send_group = QGroupBox("发送设置")
        send_layout = QVBoxLayout(send_group)
        send_layout.setSpacing(8)

        # 结尾标识符
        self.ending_combo = QComboBox()
        self.ending_combo.addItems(["None", r"\r\n", r"\r", r"\n"])
        self.ending_combo.setCurrentText(r"\r\n")
        self.ending_combo.installEventFilter(self)          # 禁用滚轮
        send_layout.addWidget(QLabel("结尾标识符:"))
        send_layout.addWidget(self.ending_combo)

        # 显示发送字符串
        show_send_layout = QHBoxLayout()
        self.show_send_check = QCheckBox("显示发送字符串")
        self.show_send_check.setChecked(False)
        show_send_layout.addWidget(self.show_send_check)

        self.send_color_combo = QComboBox()
        self.send_color_combo.addItems(["红色", "蓝色", "绿色", "紫色", "黑色"])
        self.send_color_combo.setCurrentText("红色")
        self.send_color_combo.installEventFilter(self)      # 禁用滚轮
        show_send_layout.addWidget(self.send_color_combo)
        send_layout.addLayout(show_send_layout)

        # 连续发送间隔
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("连续发送间隔(ms):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 10000)
        self.interval_spin.setValue(100)
        interval_layout.addWidget(self.interval_spin)
        send_layout.addLayout(interval_layout)

        layout.addWidget(send_group)

        # 其他设置
        other_group = QGroupBox("其他设置")
        other_layout = QVBoxLayout(other_group)
        other_layout.setSpacing(8)

        self.timestamp_check = QCheckBox("显示时间戳")
        self.timestamp_check.setChecked(False)
        other_layout.addWidget(self.timestamp_check)

        layout.addWidget(other_group)

        # 模板相关
        template_group = QGroupBox("模板相关")
        template_layout = QVBoxLayout(template_group)
        template_layout.setSpacing(8)

        template_buttons_layout = QHBoxLayout()
        self.import_btn = QPushButton("导入模板")
        self.import_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)

        self.export_btn = QPushButton("导出模板")
        self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)

        template_buttons_layout.addWidget(self.import_btn)
        template_buttons_layout.addWidget(self.export_btn)
        template_layout.addLayout(template_buttons_layout)

        layout.addWidget(template_group)

        # 添加弹性空间
        layout.addStretch()

        # 配置按钮
        self.config_btn = QPushButton("配置")
        self.config_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """)
        layout.addWidget(self.config_btn)

        return scroll_area

    def create_right_panel(self):
        """创建右侧数据交互面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # 使用垂直分割器
        v_splitter = QSplitter(Qt.Vertical)

        # 接收显示区
        receive_group = QGroupBox("接收显示区")
        receive_layout = QVBoxLayout(receive_group)

        # 接收显示区-右键菜单
        self.receive_browser = CustomTextBrowser(self.config_manager)
        self.receive_browser.setFont(QFont("Consolas", 10))
        receive_layout.addWidget(self.receive_browser)

        # 发送编辑区
        send_group = QGroupBox("发送编辑区")
        send_layout = QVBoxLayout(send_group)

        # 表头
        header_layout = QHBoxLayout()
        send_layout.addLayout(header_layout)

        # 命令表格
        self.command_table = CommandTableWidget(self.config_manager)
        self.command_table.cellDoubleClicked.connect(self.on_cell_double_clicked)
        send_layout.addWidget(self.command_table)

        # 追加命令按钮
        self.add_command_btn = QPushButton("追加命令")
        self.add_command_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)
        send_layout.addWidget(self.add_command_btn)

        # 将两个组添加到分割器
        v_splitter.addWidget(receive_group)
        v_splitter.addWidget(send_group)
        v_splitter.setSizes([400, 400])
        v_splitter.setHandleWidth(2)

        layout.addWidget(v_splitter, 4)

        # 连续发送选择
        continuous_group = QGroupBox("连续发送选择")
        continuous_layout = QHBoxLayout(continuous_group)

        self.refresh_modules_btn = QPushButton("刷新")
        self.refresh_modules_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)
        continuous_layout.addWidget(self.refresh_modules_btn)

        # 创建模块标签并设置右对齐
        module_label = QLabel("模块:")
        module_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # 右对齐
        continuous_layout.addWidget(module_label)

        self.module_combo = QComboBox()
        self.module_combo.addItem("全部")
        continuous_layout.addWidget(self.module_combo)

        self.continuous_btn = QPushButton("连续发送")
        self.continuous_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)
        continuous_layout.addWidget(self.continuous_btn)

        layout.addWidget(continuous_group)

        # 统计栏
        stats_layout = QHBoxLayout()

        self.send_count_label = QLabel("发送: 0 字节")
        self.receive_count_label = QLabel("接收: 0 字节")
        self.reset_stats_btn = QPushButton("复位")
        self.reset_stats_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """)

        stats_layout.addWidget(self.send_count_label)
        stats_layout.addWidget(self.receive_count_label)
        stats_layout.addStretch()
        stats_layout.addWidget(self.reset_stats_btn)

        layout.addLayout(stats_layout)

        return panel

    def connect_signals(self):
        """连接信号槽"""
        self.connect_btn.clicked.connect(self.toggle_serial_connection)
        self.refresh_ports_btn.clicked.connect(self.refresh_ports)
        self.save_btn.clicked.connect(self.save_receive_data)
        self.clear_receive_btn.clicked.connect(self.clear_receive_data)
        self.add_command_btn.clicked.connect(self.add_command)
        self.continuous_btn.clicked.connect(self.toggle_continuous_send)
        self.reset_stats_btn.clicked.connect(self.reset_statistics)
        self.import_btn.clicked.connect(self.import_template)
        self.export_btn.clicked.connect(self.export_template)
        self.refresh_modules_btn.clicked.connect(self.refresh_modules)
        self.config_btn.clicked.connect(self.open_config_dialog)

    def refresh_ports(self):
        """刷新可用串口列表"""
        current_text = self.port_combo.currentText()
        self.port_combo.clear()

        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.port_combo.addItem(port.device)

        # 恢复之前的选择
        if current_text and self.port_combo.findText(current_text) >= 0:
            self.port_combo.setCurrentText(current_text)

    def toggle_serial_connection(self):
        """打开/关闭串口连接"""
        if not self.is_connected:
            self.open_serial()
        else:
            self.close_serial()

    def open_serial(self):
        """打开串口"""
        port = self.port_combo.currentText()
        if not port:
            self.output_manager.append_text("错误: 请选择串口", OutputSource.ERROR)
            return

        try:
            baudrate = int(self.baud_combo.currentText())
            bytesize = int(self.data_bits_combo.currentText())

            parity_map = {"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN,
                         "Odd": serial.PARITY_ODD, "Mark": serial.PARITY_MARK}
            parity = parity_map[self.parity_combo.currentText()]

            stopbits_map = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
                           "2": serial.STOPBITS_TWO}
            stopbits = stopbits_map[self.stop_bits_combo.currentText()]

            self.serial_thread = SerialThread(port, baudrate, bytesize, parity, stopbits)
            self.serial_thread.data_received.connect(self.on_data_received)
            self.serial_thread.error_occurred.connect(self.on_serial_error)
            self.serial_thread.start()

            self.is_connected = True
            self.connect_btn.setText("关闭串口")
            self.connect_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.RED_BUTTON};
                    color: white;
                }}
            """)
            self.output_manager.append_text("串口已打开", OutputSource.SYSTEM)

        except Exception as e:
            self.output_manager.append_text(f"错误: 打开串口失败: {str(e)}", OutputSource.ERROR)

    def close_serial(self):
        """关闭串口"""
        if self.serial_thread:
            self.serial_thread.stop()
            self.serial_thread = None

        self.is_connected = False
        self.connect_btn.setText("打开串口")
        self.connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """)
        self.output_manager.append_text("串口已关闭", OutputSource.SYSTEM)

    def on_data_received(self, data):
        """接收数据回调"""
        try:
            text = data.decode('utf-8')
            self.receive_count += len(data)
            self.update_statistics()
            self.output_manager.append_text(text, OutputSource.RECEIVE)
        except UnicodeDecodeError:
            self.output_manager.append_text(f"[非UTF-8数据: {data.hex()}]", OutputSource.ERROR)

    def on_serial_error(self, error_msg):
        """串口错误回调"""
        self.output_manager.append_text(f"错误: {error_msg}", OutputSource.ERROR)
        if self.is_connected:
            self.close_serial()

    def open_config_dialog(self):
        """打开配置对话框"""
        dialog = ConfigDialog(self.config_manager, self)
        if dialog.exec_():
            # 配置已保存，可以执行一些刷新操作
            self.output_manager.append_text("配置已更新", OutputSource.SYSTEM)


    def get_send_color(self):
        """获取发送文本颜色"""
        color_map = {
            "红色": "red",
            "蓝色": "blue",
            "绿色": "green",
            "紫色": "purple",
            "黑色": "black"
        }
        return color_map.get(self.send_color_combo.currentText(), "red")

    def get_ending_chars(self):
        """获取结尾标识符"""
        ending = self.ending_combo.currentText()
        if ending == "None":
            return b""
        else:
            return ending.encode('utf-8').decode('unicode_escape').encode('utf-8')

    def send_command(self, command, row_index=None):
        """发送命令"""
        if not self.is_connected:
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            return

        try:
            # 添加结尾标识符
            ending = self.get_ending_chars()
            full_command = command.encode('utf-8') + ending

            # 发送数据
            sent_bytes = self.serial_thread.write_data(full_command)
            self.send_count += sent_bytes
            self.update_statistics()

            # 重置接收时间戳标志
            self.output_manager.reset_receive_timestamp()

            # 显示发送的字符串
            if self.show_send_check.isChecked():
                self.output_manager.append_text(command, OutputSource.SEND)

        except Exception as e:
            self.output_manager.append_text(f"错误: 发送失败: {str(e)}", OutputSource.ERROR)

    def add_command(self):
        """添加新命令"""
        row = self.command_table.rowCount()
        # 直接添加行, add_command_row内部已经处理了按钮连接, 不需要再次连接
        self.command_table.add_command_row(False, "", "", row)

    def on_send_clicked(self, row):
        """发送按钮点击事件"""
        enable, command, comment = self.command_table.get_row_data(row)
        if command.strip():
            # 检查是否为特殊指令
            if not enable and re.match(r'^(\w+):(.*)$', command, re.IGNORECASE):
                match = re.match(r'^(\w+):(.*)$', command, re.IGNORECASE)
                command_type = match.group(1).lower()
                param = match.group(2)

                if command_type == "delay":
                    try:
                        delay_ms = float(param.strip())
                        # 单次发送时执行delay
                        self.output_manager.append_text(f"执行延迟: {delay_ms}ms", OutputSource.SYSTEM)
                        # 使用QTimer进行非阻塞延迟
                        QTimer.singleShot(int(delay_ms), lambda: None)
                    except ValueError:
                        self.output_manager.append_text(f"错误: 无效的延迟参数: {param}", OutputSource.ERROR)
                else:
                    self.output_manager.append_text(f"特殊指令 '{command_type}' 在单次发送中忽略", OutputSource.SYSTEM)
            else:
                # 普通命令, 直接发送
                self.send_command(command, row)

    def on_cell_double_clicked(self, row, column):
        """单元格双击事件 - 编辑注释"""
        if column == 0:  # 选择框列
            self.command_table.edit_comment(row)

    def toggle_continuous_send(self):
        """切换连续发送状态"""
        if not self.is_continuous_sending:
            self.start_continuous_send()
        else:
            self.stop_continuous_send()

    def start_continuous_send(self):
        """开始连续发送"""
        if not self.is_connected:
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            return

        self.is_continuous_sending = True
        self.continuous_btn.setText("停止")
        self.continuous_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 使用单次定时器启动连续发送
        QTimer.singleShot(0, self.send_continuous_commands)

    def stop_continuous_sending(self):
        """停止连续发送"""
        self.is_continuous_sending = False
        self.continuous_btn.setText("连续发送")
        self.continuous_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)
        self.continuous_timer.stop()

    def send_continuous_commands(self):
        """发送连续命令"""
        if not self.is_continuous_sending:
            return

        selected_module = self.module_combo.currentText()

        # 收集需要发送的命令
        commands_to_send = []
        for row in range(self.command_table.rowCount()):
            enable, command, comment = self.command_table.get_row_data(row)

            # 检查是否属于选择的模块
            if selected_module != "全部" and row not in self.modules.get(selected_module, []):
                continue

            if enable and command.strip():
                commands_to_send.append((row, command, False))
            elif not enable and re.match(r'^(\w+):(.*)$', command, re.IGNORECASE):
                # 特殊指令
                match = re.match(r'^(\w+):(.*)$', command, re.IGNORECASE)
                command_type = match.group(1).lower()
                param = match.group(2)
                commands_to_send.append((row, command, True, command_type, param))

        # 发送命令
        def send_next_command(index=0):
            if not self.is_continuous_sending or index >= len(commands_to_send):
                self.stop_continuous_sending()
                return

            row, command, is_special, *special_args = commands_to_send[index]

            if is_special:
                # 处理特殊指令
                command_type, param = special_args
                if command_type == "delay":
                    try:
                        delay_ms = float(param.strip())
                        self.output_manager.append_text(f"连续发送延迟: {delay_ms}ms", OutputSource.SYSTEM)
                        QTimer.singleShot(int(delay_ms), lambda: send_next_command(index + 1))
                        return
                    except ValueError:
                        self.output_manager.append_text(f"错误: 无效的延迟参数: {param}", OutputSource.ERROR)
                # 其他特殊指令在发送时忽略
                QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
            else:
                # 发送普通命令
                self.send_command(command, row)
                QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))

        # 开始发送
        if commands_to_send:
            send_next_command()
        else:
            self.output_manager.append_text("警告: 没有找到可发送的命令", OutputSource.SYSTEM)
            self.stop_continuous_sending()

    def stop_continuous_send(self):
        """停止连续发送 (公共接口) """
        self.stop_continuous_sending()

    def refresh_modules(self):
        """刷新模块列表"""
        self.modules.clear()
        self.module_combo.clear()
        self.module_combo.addItem("全部")

        current_module = "默认"
        self.modules[current_module] = []

        for row in range(self.command_table.rowCount()):
            enable, command, comment = self.command_table.get_row_data(row)

            # 检查是否为mode指令
            if not enable and re.match(r'^mode:(.*)$', command, re.IGNORECASE):
                match = re.match(r'^mode:(.*)$', command, re.IGNORECASE)
                current_module = match.group(1).strip()
                self.modules[current_module] = []
                self.module_combo.addItem(current_module)
            else:
                # 添加到当前模块
                self.modules[current_module].append(row)

        self.output_manager.append_text("模块列表已刷新", OutputSource.SYSTEM)

    def save_receive_data(self):
        """保存接收数据"""
        filename, _ = QFileDialog.getSaveFileName(
            self, "保存接收数据", "", "Text Files (*.txt);;All Files (*)")

        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.receive_browser.toPlainText())
                self.output_manager.append_text(f"数据已保存到: {filename}", OutputSource.SYSTEM)
            except Exception as e:
                self.output_manager.append_text(f"错误: 保存文件失败: {str(e)}", OutputSource.ERROR)

    def clear_receive_data(self):
        """清空接收数据"""
        self.output_manager.clear()

    def update_statistics(self):
        """更新统计信息"""
        self.send_count_label.setText(f"发送: {self.send_count} 字节")
        self.receive_count_label.setText(f"接收: {self.receive_count} 字节")

    def reset_statistics(self):
        """复位统计"""
        self.send_count = 0
        self.receive_count = 0
        self.update_statistics()

    def parse_template_line(self, line):
        """解析模板行"""
        line = line.strip()

        # 跳过空行
        if not line:
            return None

        # 处理注释行 (//或#开头)
        if line.startswith('//') or line.startswith('#'):
            comment_text = line.lstrip('/#').strip()
            return False, "", comment_text

        # 解析CSV格式
        parts = [part.strip() for part in line.split(',', 2)]

        if len(parts) < 2:
            return None

        # 解析选择框
        try:
            enable = parts[0].lower() == 'true'
        except:
            enable = False

        command = parts[1]
        comment = parts[2] if len(parts) > 2 else ""

        return enable, command, comment

    def import_template(self):
        """导入模板"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入模板", "", "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")

        if not filename:
            return

        try:
            with open(filename, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # 清空现有命令
            self.command_table.clear_all()
            self.modules.clear()
            self.module_combo.clear()
            self.module_combo.addItem("全部")

            current_module = "默认"
            self.modules[current_module] = []

            row = 0
            for line in lines:
                result = self.parse_template_line(line)
                if result is None:
                    continue

                enable, command, comment = result

                # 检查特殊指令
                if not enable and re.match(r'^(\w+):(.*)$', command, re.IGNORECASE):
                    match = re.match(r'^(\w+):(.*)$', command, re.IGNORECASE)
                    keyword = match.group(1).lower()
                    param = match.group(2)

                    if keyword == 'mode':
                        current_module = param.strip()
                        self.modules[current_module] = []
                        self.module_combo.addItem(current_module)
                    # delay指令会在发送时处理

                # 添加命令到表格 (保持行号对应)
                send_btn = self.command_table.add_command_row(enable, command, comment, row)

                # 添加到当前模块 (非注释行和特殊指令行)
                if not (enable == False and command == "" and comment):  # 不是纯注释行
                    self.modules[current_module].append(row)

                row += 1

            # 导入完成后, 确保所有按钮连接正确
            self.command_table.update_send_buttons_after_row(0)

            self.output_manager.append_text(f"模板已导入: {filename}", OutputSource.SYSTEM)

        except Exception as e:
            self.output_manager.append_text(f"错误: 导入模板失败: {str(e)}", OutputSource.ERROR)

    def export_template(self):
        """导出模板"""
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出模板", "", "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")

        if not filename:
            return

        try:
            with open(filename, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                # 写入注释头
                writer.writerow(["// 选择框,串口需要发送的数据,注释"])

                commands = self.command_table.get_all_commands()
                for enable, command, comment in commands:
                    writer.writerow([str(enable), command, comment])

            self.output_manager.append_text(f"模板已导出: {filename}", OutputSource.SYSTEM)

        except Exception as e:
            self.output_manager.append_text(f"错误: 导出模板失败: {str(e)}", OutputSource.ERROR)

    def closeEvent(self, event):
        """对话框关闭事件"""
        event.accept()
