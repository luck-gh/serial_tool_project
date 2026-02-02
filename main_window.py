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

from utils.ui_utils import UIUtils, Colors, resource_path, OutputSource, SpecialCommandType
from widgets.custom_widgets import CustomTextBrowser, CollapsibleGroupBox, ClickableComboBox
from widgets.command_widgets import CommandTableWidget
from managers.output_manager import OutputManager
from managers.special_command_manager import SpecialCommandManager
from core.serial_thread import SerialThread
from managers.config_manager import ConfigManager
from dialogs.config_dialog import ConfigDialog
from widgets.base_widgets import BaseWidgetMixin

class SerialTool(QMainWindow, BaseWidgetMixin):
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
        try:
            self.config_manager = ConfigManager(
                tool_version=self.tool_version,
                tool_version_date=self.tool_version_date,
                config_file=config_file
            )
        except ValueError as e:
            # 配置文件版本过高
            QMessageBox.critical(
                None,
                "配置文件版本错误",
                str(e)
            )
            # 退出程序
            sys.exit(1)

        self.special_command_manager = SpecialCommandManager(self.config_manager)

        self.init_ui()
        self.refresh_ports()
        self.load_state()

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
            self.get_send_color,
            self.get_source_filter
        )

        # 连接信号
        self.connect_signals()

        # 更新工具状态
        self.update_tools_state()

    def update_tools_state(self):
        """根据配置更新所有工具按钮状态（通用方法）"""
        for tool_name, button in self.tool_buttons.items():
            is_enabled = self.config_manager.is_tool_enabled(tool_name)
            button.setEnabled(is_enabled)

            if is_enabled:
                button.setStyleSheet(f"QPushButton {{ background-color: {Colors.BLUE_BUTTON}; color: white; }}")
            else:
                button.setStyleSheet("QPushButton {{ background-color: #cccccc; color: #888888; }}")

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
        minimumWidth = 200
        # 端口选择
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("端口:"))
        self.port_combo = ClickableComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.popupAboutToBeShown.connect(self.refresh_ports)
        self.port_combo.installEventFilter(self)            # 禁用滚轮
        self.port_combo.setFixedWidth(minimumWidth)         # 设置固定宽度
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
        self.baud_combo.setFixedWidth(minimumWidth)         # 设置固定宽度
        baud_layout.addWidget(self.baud_combo)
        basic_layout.addLayout(baud_layout)

        # 数据位
        data_bits_layout = QHBoxLayout()
        data_bits_layout.addWidget(QLabel("数据位:"))
        self.data_bits_combo = QComboBox()
        self.data_bits_combo.addItems(["5", "6", "7", "8"])
        self.data_bits_combo.setCurrentText("8")
        self.data_bits_combo.installEventFilter(self)       # 禁用滚轮
        self.data_bits_combo.setFixedWidth(minimumWidth)    # 设置固定宽度
        data_bits_layout.addWidget(self.data_bits_combo)
        basic_layout.addLayout(data_bits_layout)

        # 校验位
        parity_layout = QHBoxLayout()
        parity_layout.addWidget(QLabel("校验位:"))
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(["None", "Even", "Odd", "Mark"])
        self.parity_combo.setCurrentText("None")
        self.parity_combo.installEventFilter(self)          # 禁用滚轮
        self.parity_combo.setFixedWidth(minimumWidth)       # 设置固定宽度
        parity_layout.addWidget(self.parity_combo)
        basic_layout.addLayout(parity_layout)

        # 停止位
        stop_bits_layout = QHBoxLayout()
        stop_bits_layout.addWidget(QLabel("停止位:"))
        self.stop_bits_combo = QComboBox()
        self.stop_bits_combo.addItems(["1", "1.5", "2"])
        self.stop_bits_combo.setCurrentText("1")
        self.stop_bits_combo.installEventFilter(self)       # 禁用滚轮
        self.stop_bits_combo.setFixedWidth(minimumWidth)    # 设置固定宽度
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

        # 输出来源类型勾选
        source_type_layout = QHBoxLayout()
        source_type_layout.addWidget(QLabel("输出来源:"))
        receive_layout.addLayout(source_type_layout)

        source_checkboxes_layout = QHBoxLayout()
        self.show_send_source_check = QCheckBox("发送")
        self.show_recv_source_check = QCheckBox("接收")
        self.show_sys_source_check = QCheckBox("系统")
        self.show_err_source_check = QCheckBox("错误")
        
        # 默认全部勾选
        self.show_send_source_check.setChecked(True)
        self.show_recv_source_check.setChecked(True)
        self.show_sys_source_check.setChecked(True)
        self.show_err_source_check.setChecked(True)

        source_checkboxes_layout.addWidget(self.show_send_source_check)
        source_checkboxes_layout.addWidget(self.show_recv_source_check)
        source_checkboxes_layout.addWidget(self.show_sys_source_check)
        source_checkboxes_layout.addWidget(self.show_err_source_check)
        receive_layout.addLayout(source_checkboxes_layout)

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

        # 循环发送设置
        loop_layout = QHBoxLayout()
        self.loop_send_check = QCheckBox("循环发送")
        self.loop_send_check.setChecked(False)
        loop_layout.addWidget(self.loop_send_check)
        
        loop_layout.addWidget(QLabel("间隔(ms):"))
        self.loop_interval_spin = QSpinBox()
        self.loop_interval_spin.setRange(10, 60000)
        self.loop_interval_spin.setValue(1000)
        loop_layout.addWidget(self.loop_interval_spin)
        send_layout.addLayout(loop_layout)

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

        # 工具相关
        self.tools_group = CollapsibleGroupBox("工具相关")

        # 动态创建工具按钮（基于配置管理器的注册表）
        self.tool_buttons = {}  # 保存工具按钮引用 {tool_name: button}
        for tool_name in self.config_manager.get_all_tool_names():
            button_text = self.config_manager.get_tool_button_text(tool_name)
            btn = QPushButton(button_text)
            self.tools_group.addWidget(btn)
            self.tool_buttons[tool_name] = btn

        layout.addWidget(self.tools_group)

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

        # 连续发送模块
        continuous_group = QGroupBox("连续发送模块")
        continuous_layout = QHBoxLayout(continuous_group)

        self.refresh_modules_btn = QPushButton("刷新")
        self.refresh_modules_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """)
        continuous_layout.addWidget(self.refresh_modules_btn)

        # 跳转到此 - 模块选择
        jump_label = QLabel("跳转:")
        jump_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        continuous_layout.addWidget(jump_label)

        self.jump_module_combo = QComboBox()
        self.jump_module_combo.addItem("全部")
        continuous_layout.addWidget(self.jump_module_combo)

        # 新增的跳转到此按钮
        self.jump_to_module_btn = QPushButton("跳转到此")
        self.jump_to_module_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """)
        continuous_layout.addWidget(self.jump_to_module_btn)

        # Mode Sync Button
        self.sync_mode_btn = QPushButton("<-")
        self.sync_mode_btn.setFixedWidth(30)
        self.sync_mode_btn.setToolTip("将发送模式同步到跳转模式")
        self.sync_mode_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
                padding: 2px;
            }}
        """)
        continuous_layout.addWidget(self.sync_mode_btn)

        # 连续发送 - 模块选择
        send_label = QLabel("发送:")
        send_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        continuous_layout.addWidget(send_label)

        self.send_module_combo = QComboBox()
        self.send_module_combo.addItem("全部")
        continuous_layout.addWidget(self.send_module_combo)

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
        self.jump_to_module_btn.clicked.connect(self._jump_to_module_row)
        self.sync_mode_btn.clicked.connect(self._sync_mode_selection)

        # 连接工具按钮（动态连接，基于工具名映射到方法）
        tool_method_map = {
            "number_conversion_dialog": self.open_bit_calculator,
            "bin_hex_converter": self.open_bin_hex_converter,
            "firmware_downloader": self.open_firmware_downloader,
        }
        for tool_name, button in self.tool_buttons.items():
            if tool_name in tool_method_map:
                # 为每个按钮创建带反馈的包装函数
                method = tool_method_map[tool_name]
                button.clicked.connect(lambda _, btn=button, m=method: self._tool_button_clicked(btn, m))

    def _tool_button_clicked(self, button, method):
        """工具按钮点击处理 - 添加视觉反馈"""
        # 添加视觉反馈 - 按钮闪烁和颜色变化
        original_style = button.styleSheet()
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: button.setStyleSheet(original_style))

        # 调用实际的工具方法
        method()

    def refresh_ports(self):
        """刷新可用串口列表"""
        # 添加视觉反馈 - 按钮闪烁和颜色变化
        self.refresh_ports_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.refresh_ports_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """))

        current_text = self.port_combo.currentText()
        self.port_combo.clear()

        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.port_combo.addItem(f"{port.device} - {port.description}")

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
        port_text = self.port_combo.currentText()
        port = port_text.split(' ')[0]
        if not port:
            self.output_manager.append_text("错误: 请选择串口", OutputSource.ERROR)
            return

        # 检查串口是否存在
        available_ports = [p.device for p in serial.tools.list_ports.comports()]
        if port not in available_ports:
            self.output_manager.append_text(f"错误: 串口 {port} 不存在", OutputSource.ERROR)
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
            error_str = str(e)
            if "PermissionError(13, '拒绝访问。'" in error_str or "Access is denied" in error_str:
                self.output_manager.append_text(f"错误: 串口 {port} 正在使用中", OutputSource.ERROR)
            else:
                self.output_manager.append_text(f"错误: 打开串口失败: {error_str}", OutputSource.ERROR)
            
            # 打开失败时也刷新一下串口列表
            self.refresh_ports()

    def close_serial(self):
        """关闭串口"""
        # 如果正在连续发送，停止它
        if self.is_continuous_sending:
            self.stop_continuous_sending()

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
        
        # 串口错误时停止连续发送
        if self.is_continuous_sending:
            self.stop_continuous_sending()
            
        # 串口错误时停止循环发送定时器 (如果有)
        if hasattr(self, 'continuous_timer'):
            self.continuous_timer.stop()
            
        if self.is_connected:
            self.close_serial()
        
        # 串口错误时自动刷新串口列表
        self.refresh_ports()

    def open_config_dialog(self):
        """打开配置对话框"""
        # 添加视觉反馈 - 按钮闪烁效果
        self.config_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.config_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """))

        dialog = ConfigDialog(self.config_manager, self)
        if dialog.exec_():
            # 配置已保存，可以执行一些刷新操作
            self.output_manager.append_text("配置已更新", OutputSource.SYSTEM)
            self.update_tools_state()


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

    def get_source_filter(self, source_type):
        """获取输出来源过滤状态"""
        if source_type == OutputSource.SEND:
            return self.show_send_source_check.isChecked()
        elif source_type == OutputSource.RECEIVE:
            return self.show_recv_source_check.isChecked()
        elif source_type == OutputSource.SYSTEM:
            return self.show_sys_source_check.isChecked()
        elif source_type == OutputSource.ERROR:
            return self.show_err_source_check.isChecked()
        return True

    def get_ending_chars(self):
        """获取结尾标识符"""
        ending = self.ending_combo.currentText()
        if ending == "None":
            return b""
        else:
            return ending.encode('utf-8').decode('unicode_escape').encode('utf-8')

    def send_command(self, command, row_index=None):
        """发送命令"""
        if not self.is_connected or not self.serial_thread:
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            if self.is_continuous_sending:
                self.stop_continuous_sending()
            return False

        try:
            # 添加结尾标识符
            ending = self.get_ending_chars()
            full_command = command.encode('utf-8') + ending

            # 发送数据
            sent_bytes = self.serial_thread.write_data(full_command)
            if sent_bytes > 0:
                self.send_count += sent_bytes
                self.update_statistics()

                # 重置接收时间戳标志
                self.output_manager.reset_receive_timestamp()

                # 显示发送的字符串
                if self.show_send_check.isChecked():
                    self.output_manager.append_text(command, OutputSource.SEND)
                
                return True
            else:
                # 发送失败 (可能是串口已意外关闭)
                if self.is_continuous_sending:
                    self.stop_continuous_sending()
                return False

        except Exception as e:
            self.output_manager.append_text(f"错误: 发送失败: {str(e)}", OutputSource.ERROR)
            if self.is_continuous_sending:
                self.stop_continuous_sending()
            return False

    def add_command(self):
        """添加新命令"""
        # 添加视觉反馈 - 按钮闪烁效果
        self.add_command_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.add_command_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """))

        row = self.command_table.rowCount()
        # 直接添加行, add_command_row内部已经处理了按钮连接, 不需要再次连接
        self.command_table.add_command_row(False, "", "", row)
        self.refresh_modules(silent=True)

    def on_send_clicked(self, row):
        """发送按钮点击事件"""
        enable, command, comment = self.command_table.get_row_data(row)
        if command is not None:  # 允许空字符串，只要行数据存在
            # 检查是否为特殊指令
            cmd_type_str, param = UIUtils.parse_special_command(command)
            if cmd_type_str:
                # 尝试匹配枚举类型
                command_type = None
                for ct in SpecialCommandType:
                    if ct.value == cmd_type_str:
                        command_type = ct
                        break

                if command_type:
                    # 执行特殊指令
                    self.special_command_manager.execute(command_type, param, self)
                    return  # 特殊指令执行后返回

            # 普通命令或未知特殊指令, 处理转义后发送
            unescaped_command = UIUtils.unescape_text(command)
            self.send_command(unescaped_command, row)

    def send_raw_data(self, data):
        """发送原始字节数据 (供特殊指令使用) """
        if not self.is_connected or not self.serial_thread:
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            return False
            
        sent_bytes = self.serial_thread.write_data(data)
        if sent_bytes > 0:
            self.send_count += sent_bytes
            self.update_statistics()
            self.output_manager.reset_receive_timestamp()
            if self.show_send_check.isChecked():
                self.output_manager.append_text(f"[HEX]: {data.hex(' ').upper()}", OutputSource.SEND)
            return True
        return False

    def update_baudrate(self, baudrate):
        """更新波特率 (供特殊指令使用) """
        self.baud_combo.setCurrentText(str(baudrate))
        if self.is_connected and self.serial_thread:
            # 动态调整波特率，无需关闭串口
            self.output_manager.append_text(f"正在调整波特率至: {baudrate}", OutputSource.SYSTEM)
            if self.serial_thread.set_baudrate(baudrate):
                self.output_manager.append_text(f"波特率已更新为: {baudrate}", OutputSource.SYSTEM)
            else:
                self.output_manager.append_text(f"调整波特率失败", OutputSource.ERROR)

    def set_ending(self, ending_text):
        """设置结尾标识符 (供特殊指令使用) """
        # 映射常见的缩写或直接匹配
        mapping = {
            "none": "None",
            "rn": r"\r\n",
            "r": r"\r",
            "n": r"\n"
        }
        target = mapping.get(ending_text.lower(), ending_text)
        index = self.ending_combo.findText(target)
        if index >= 0:
            self.ending_combo.setCurrentIndex(index)
            self.output_manager.append_text(f"已设置结尾标识符为: {target}", OutputSource.SYSTEM)
        else:
            self.output_manager.append_text(f"错误: 不支持的结尾标识符: {ending_text}", OutputSource.ERROR)

    def trigger_send_mode(self, module_name):
        """触发指定模块的连续发送 (供特殊指令使用) """
        module_name = module_name.strip()
        # 刷新模块列表以确保最新
        self.refresh_modules()
        
        if module_name in self.modules or module_name == "全部":
            self.jump_module_combo.setCurrentText(module_name)
            self.output_manager.append_text(f"触发模块跳转: {module_name} (仅发送一次)", OutputSource.SYSTEM)
            
            # 设置强制不循环标志
            self._force_no_loop = True
            
            # 如果当前没有在连续发送，则启动
            if not self.is_continuous_sending:
                self.start_continuous_send()
            else:
                # 如果已经在连续发送，我们需要停止当前的发送并重新开始。
                # 注意：stop_continuous_sending 会清除 _force_no_loop，所以我们需要在之后重新设置
                self.stop_continuous_sending()
                self._force_no_loop = True
                # 延迟一小段时间后重新启动，确保之前的定时器都已清理
                QTimer.singleShot(self.interval_spin.value(), self.start_continuous_send)
            return True
        else:
            self.output_manager.append_text(f"错误: 找不到模块 '{module_name}'", OutputSource.ERROR)
            return False

    def on_cell_double_clicked(self, row, column):
        """单元格双击事件 - 编辑注释"""
        if column == 0:  # 选择框列
            self.command_table.edit_comment(row)

    def toggle_continuous_send(self):
        """切换连续发送状态"""
        if not self.is_continuous_sending:
            # 手动启动时，清除强制不循环标志
            if hasattr(self, '_force_no_loop'):
                del self._force_no_loop
            self.start_continuous_send()
        else:
            self.stop_continuous_send()

    def start_continuous_send(self):
        """开始连续发送"""
        if not self.is_connected:
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            return

        # 获取当前选中的模块名称
        selected_module = self.send_module_combo.currentText()

        self.is_continuous_sending = True
        self.continuous_btn.setText("停止")
        self.continuous_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)

        # 添加系统消息显示当前发送的模块名称
        self.output_manager.append_text(f"开始连续发送模块: '{selected_module}'", OutputSource.SYSTEM)

        # 使用单次定时器启动连续发送
        QTimer.singleShot(0, self.send_continuous_commands)

    def stop_continuous_sending(self):
        """停止连续发送"""
        self.is_continuous_sending = False
        # 清除强制不循环标志
        if hasattr(self, '_force_no_loop'):
            del self._force_no_loop
            
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

        selected_module = self.send_module_combo.currentText()

        # 收集需要发送的命令
        commands_to_send = []
        for row in range(self.command_table.rowCount()):
            enable, command, comment = self.command_table.get_row_data(row)

            # 检查是否属于选择的模块
            if selected_module != "全部" and row not in self.modules.get(selected_module, []):
                continue

            if enable:  # 只要勾选了就处理
                # 检查是否为特殊指令
                cmd_type_str, param = UIUtils.parse_special_command(command)
                if cmd_type_str:
                    # 尝试匹配枚举类型
                    command_type = None
                    for ct in SpecialCommandType:
                        if ct.value == cmd_type_str:
                            command_type = ct
                            break

                    if command_type:
                        # 是已知的特殊指令
                        commands_to_send.append((row, command, True, command_type, param))
                    else:
                        # 未知特殊指令，按普通命令处理
                        unescaped_command = UIUtils.unescape_text(command)
                        commands_to_send.append((row, unescaped_command, False))
                else:
                    # 普通命令
                    unescaped_command = UIUtils.unescape_text(command)
                    commands_to_send.append((row, unescaped_command, False))
            else:
                # 未勾选，但如果是 mode 指令，虽然不发送，但可能需要识别（目前逻辑是勾选才发送/执行）
                # 这里保持原样，未勾选的不加入发送队列
                pass

        # 发送命令
        def send_next_command(index=0):
            # 检查是否需要跳过到循环（StopContinuous:0 的效果）
            if getattr(self, '_skip_to_loop', False):
                self._skip_to_loop = False  # 清除标志
                # 立即跳转到循环检查逻辑
                index = len(commands_to_send)  # 设置为最后，触发循环检查

            if not self.is_continuous_sending or index >= len(commands_to_send):
                # 检查是否开启了循环发送
                if self.is_continuous_sending and self.loop_send_check.isChecked() and commands_to_send:
                    # 等待循环间隔后再次开始
                    QTimer.singleShot(self.loop_interval_spin.value(), self.send_continuous_commands)
                else:
                    self.stop_continuous_sending()
                return

            row, command, is_special, *special_args = commands_to_send[index]

            if is_special:
                # 处理特殊指令
                command_type, param = special_args
                if command_type == SpecialCommandType.DELAY:
                    try:
                        delay_ms = float(param.strip())
                        self.output_manager.append_text(f"连续发送延迟: {delay_ms}ms", OutputSource.SYSTEM)
                        QTimer.singleShot(int(delay_ms), lambda: send_next_command(index + 1))
                        return
                    except ValueError:
                        self.output_manager.append_text(f"错误: 无效的延迟参数: {param}", OutputSource.ERROR)
                elif command_type == SpecialCommandType.SENDHEX:
                    # 执行 SendHex 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    else:
                        # 执行失败时记录错误并继续下一条，不中断连续发送
                        self.output_manager.append_text(f"错误: SendHex 执行失败: {param}", OutputSource.ERROR)
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type == SpecialCommandType.BAUDRATE:
                    # 执行 BaudRate 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        # 波特率切换可能导致串口重启，等待 500ms 确保稳定
                        QTimer.singleShot(500, lambda: send_next_command(index + 1))
                    else:
                        self.output_manager.append_text(f"错误: BaudRate 执行失败: {param}", OutputSource.ERROR)
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SETENDLOG:
                    # 执行 SetEndlog 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    else:
                        self.output_manager.append_text(f"错误: SetEndlog 执行失败: {param}", OutputSource.ERROR)
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SENDMODE:
                    # 执行 SendMode 指令 - 发送指定模块的内容后继续当前模块
                    def on_sendmode_complete():
                        # SendMode 执行完毕后，继续当前模块的下一条命令
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))

                    self.special_command_manager.execute_sendmode_inline(param.strip(), self, on_sendmode_complete)
                    return
                elif command_type == SpecialCommandType.STOPCONTINUOUS:
                    # 执行 StopContinuous 指令
                    self.special_command_manager.execute(command_type, param, self)
                    # 如果设置了 _skip_to_loop 标志（模式0），立即触发循环检查
                    # 否则（模式1）会调用 stop_continuous_sending，is_continuous_sending 会变为 False
                    send_next_command(len(commands_to_send))  # 跳转到结束，触发循环检查
                    return

                # 其他特殊指令（如mode）在发送时忽略
                QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
            else:
                # 发送普通命令
                if self.send_command(command, row):
                    QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                else:
                    # 发送失败时停止连续发送
                    self.stop_continuous_sending()

        # 开始发送
        if commands_to_send:
            send_next_command()
        else:
            self.output_manager.append_text("警告: 没有找到可发送的命令", OutputSource.SYSTEM)
            self.stop_continuous_sending()

    def stop_continuous_send(self):
        """停止连续发送 (公共接口) """
        self.stop_continuous_sending()

    def refresh_modules(self, silent=False):
        """刷新模块列表"""
        # 添加视觉反馈 - 按钮闪烁和颜色变化
        if not silent:
            # 闪烁效果：改变按钮样式为运行中状态
            self.refresh_modules_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.RED_BUTTON};
                    color: white;
                }}
            """)
            # 200ms后恢复原始样式
            QTimer.singleShot(200, lambda: self.refresh_modules_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.GREEN_BUTTON};
                    color: white;
                }}
            """))

        # 保存当前选择
        jump_selection = self.jump_module_combo.currentText() if self.jump_module_combo.currentText() else "全部"
        send_selection = self.send_module_combo.currentText() if self.send_module_combo.currentText() else "全部"

        self.modules.clear()
        self.jump_module_combo.clear()
        self.send_module_combo.clear()
        self.jump_module_combo.addItem("全部")
        self.send_module_combo.addItem("全部")

        current_module = "默认"
        self.modules[current_module] = []

        for row in range(self.command_table.rowCount()):
            enable, command, comment = self.command_table.get_row_data(row)

            # 检查是否为特殊指令
            cmd_type_str, param = UIUtils.parse_special_command(command)
            
            # 无论是否勾选，都要处理 mode/modeend 控制逻辑，以建立正确的模块结构
            if cmd_type_str == "mode":
                # mode指令：开始新模块
                current_module = param.strip()
                self.modules[current_module] = []
                self.jump_module_combo.addItem(current_module)
                self.send_module_combo.addItem(current_module)
            elif cmd_type_str == "modeend":
                # modeend指令：如果是勾选的，先加入当前模块（这样才能在运行时执行）
                if enable:
                    self.modules[current_module].append(row)
                    
                # 结束当前模块，切换回默认模块
                # 注意：如果 param 是 '0' 才执行结束动作？不，定义时只要是 modeend 就结束定义范围
                # 但运行时只有 param 不为 -1 (或特定值) 才结束？
                # 这里我们保持定义结束的语义
                if current_module != "默认":
                    current_module = "默认"
                    # 如果默认模块还未初始化，创建它
                    if current_module not in self.modules:
                        self.modules[current_module] = []
            else:
                # 其他指令（普通指令或非结构化特殊指令）：只有所属的模块正确
                # 但只有勾选的才会被 execute_xxx 用到？不，refresh_modules 只是建立映射
                # 实际发送时会检查 enable
                self.modules[current_module].append(row)

        # 恢复之前的选择(如果仍然存在)
        if jump_selection and self.jump_module_combo.findText(jump_selection) >= 0:
            self.jump_module_combo.setCurrentText(jump_selection)
        if send_selection and self.send_module_combo.findText(send_selection) >= 0:
            self.send_module_combo.setCurrentText(send_selection)

        if not silent:
            self.output_manager.append_text("模块列表已刷新", OutputSource.SYSTEM)

    def save_receive_data(self):
        """保存接收数据"""
        # 添加视觉反馈 - 按钮闪烁效果
        self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """))

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
        # 添加视觉反馈 - 按钮闪烁效果
        self.clear_receive_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.clear_receive_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """))

        self.output_manager.clear()
        self.output_manager.append_text("接收数据已清空", OutputSource.SYSTEM)

    def update_statistics(self):
        """更新统计信息"""
        self.send_count_label.setText(f"发送: {self.send_count} 字节")
        self.receive_count_label.setText(f"接收: {self.receive_count} 字节")

    def reset_statistics(self):
        """复位统计"""
        # 添加视觉反馈 - 按钮闪烁效果
        self.reset_stats_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.reset_stats_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """))

        self.send_count = 0
        self.receive_count = 0
        self.update_statistics()
        self.output_manager.append_text("统计数据已复位", OutputSource.SYSTEM)

    def import_template(self):
        """导入模板"""
        # 添加视觉反馈 - 按钮闪烁效果
        self.import_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.import_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """))

        last_dir = self.config_manager.get_last_used_directory()
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入模板", last_dir, "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")

        if not filename:
            return

        self.config_manager.set_last_used_directory(os.path.dirname(filename))

        try:
            # 清空现有命令
            self.command_table.clear_all()
            self.modules.clear()
            self.jump_module_combo.clear()
            self.send_module_combo.clear()
            self.jump_module_combo.addItem("全部")
            self.send_module_combo.addItem("全部")

            current_module = "默认"
            self.modules[current_module] = []

            row = 0
            with open(filename, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.reader(f)
                for csv_row in reader:
                    # 跳过空行
                    if not csv_row or len(csv_row) == 0:
                        continue

                    # 跳过注释行（第一列以//或#开头）
                    if csv_row[0].strip().startswith('//') or csv_row[0].strip().startswith('#'):
                        continue

                    # 检查是否为有效数据行（至少2列）
                    if len(csv_row) < 2:
                        continue

                    # 解析第一列（True/False）
                    enable_str = csv_row[0].strip().lower()
                    if enable_str not in ['true', 'false']:
                        continue  # 跳过非数据行

                    enable = (enable_str == 'true')

                    # 解析命令和注释
                    # 第2列是命令，第3列及之后的所有内容都是注释（包括逗号）
                    command = UIUtils.unescape_csv_text(csv_row[1].strip())

                    # 如果有第3列及以后的内容，将它们全部合并为注释
                    if len(csv_row) > 2:
                        # 将第3列及之后的所有列用逗号连接起来作为完整注释
                        comment_parts = [csv_row[i].strip() for i in range(2, len(csv_row))]
                        comment = ','.join(comment_parts)
                        comment = UIUtils.unescape_csv_text(comment)
                    else:
                        comment = ""

                    # 检查特殊指令
                    cmd_type_str, param = UIUtils.parse_special_command(command)
                    if not enable and cmd_type_str:
                        if cmd_type_str == 'mode':
                            current_module = param.strip()
                            self.modules[current_module] = []
                            self.jump_module_combo.addItem(current_module)
                            self.send_module_combo.addItem(current_module)
                            # 添加系统消息提示模块已创建
                            self.output_manager.append_text(f"已创建模块: '{current_module}'", OutputSource.SYSTEM)
                        elif cmd_type_str == 'modeend':
                            # modeend指令：结束当前模块，切换回默认模块
                            if current_module != "默认":
                                current_module = "默认"
                                # 如果默认模块还未初始化，创建它
                                if current_module not in self.modules:
                                    self.modules[current_module] = []
                                self.output_manager.append_text(f"模块定义已结束，切换回默认模块", OutputSource.SYSTEM)
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
        # 添加视觉反馈 - 按钮闪烁效果
        self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.GREEN_BUTTON};
                color: white;
            }}
        """))

        last_dir = self.config_manager.get_last_used_directory()
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出模板", last_dir, "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")

        if not filename:
            return

        self.config_manager.set_last_used_directory(os.path.dirname(filename))

        try:
            with open(filename, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                # 写入注释头
                writer.writerow(["// 选择框,串口需要发送的数据,注释"])

                commands = self.command_table.get_all_commands()
                for enable, command, comment in commands:
                    # CSV writer会自动处理引号和特殊字符，无需手动转义
                    # 只对不可见字符进行转义，保持可读性
                    escaped_command = UIUtils.escape_text(command)
                    escaped_comment = UIUtils.escape_text(comment)
                    writer.writerow([str(enable), escaped_command, escaped_comment])

            self.output_manager.append_text(f"模板已导出: {filename}", OutputSource.SYSTEM)

        except Exception as e:
            self.output_manager.append_text(f"错误: 导出模板失败: {str(e)}", OutputSource.ERROR)

    def _jump_to_module_row(self):
        """跳转到所选模块的第一个命令行"""
        # 添加视觉反馈 - 按钮闪烁效果
        self.jump_to_module_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON};
                color: white;
            }}
        """)
        # 200ms后恢复原始样式
        QTimer.singleShot(200, lambda: self.jump_to_module_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """))

        selected_module_name = self.jump_module_combo.currentText()

        if selected_module_name == "全部":
            self.output_manager.append_text("提示: 请选择一个具体的模块进行跳转。", OutputSource.ERROR)
            return

        # 获取所选模块的行号列表
        module_rows = self.modules.get(selected_module_name)
        if not module_rows:
            self.output_manager.append_text(f"模块 '{selected_module_name}' 中没有找到命令。", OutputSource.ERROR)
            return

        # 获取模块的第一个命令的行号
        first_command_row = module_rows[0]

        # 滚动到该行
        self.command_table.scrollToItem(self.command_table.item(first_command_row, 0))

        # 选中该行
        self.command_table.selectRow(first_command_row)
        self.output_manager.append_text(f"已跳转到模块 '{selected_module_name}' 的第一个命令。", OutputSource.SYSTEM)

    def save_state(self):
        """保存当前状态到配置文件"""
        try:
            state = OrderedDict([
                ("basic_settings", {
                    "port": self.port_combo.currentText(),
                    "baudrate": self.baud_combo.currentText(),
                    "databits": self.data_bits_combo.currentText(),
                    "parity": self.parity_combo.currentText(),
                    "stopbits": self.stop_bits_combo.currentText()
                }),
                ("receive_settings", {
                    "show_send_source": self.show_send_source_check.isChecked(),
                    "show_recv_source": self.show_recv_source_check.isChecked(),
                    "show_sys_source": self.show_sys_source_check.isChecked(),
                    "show_err_source": self.show_err_source_check.isChecked()
                }),
                ("send_settings", {
                    "loop_send": self.loop_send_check.isChecked(),
                    "loop_interval": self.loop_interval_spin.value(),
                    "continuous_interval": self.interval_spin.value(),
                    "show_send": self.show_send_check.isChecked(),
                    "send_color": self.send_color_combo.currentText(),
                    "ending": self.ending_combo.currentText(),
                    "jump_module": self.jump_module_combo.currentText(),
                    "send_module": self.send_module_combo.currentText()
                }),
                ("other_settings", {
                    "show_timestamp": self.timestamp_check.isChecked()
                }),
                ("ui_settings", {
                    "h_splitter_sizes": self.h_splitter.sizes()
                }),
                ("tool_settings", {
                    "tools_group_expanded": self.tools_group.isExpanded()
                }),
                ("commands", self.command_table.get_all_commands())
            ])
            self.config_manager.set("last_state", state)
        except Exception as e:
            print(f"保存状态失败: {e}")

    def load_state(self):
        """从配置文件加载状态"""
        try:
            state = self.config_manager.get("last_state")
            if not state:
                # 兼容旧版本配置 (如果存在)
                self._load_legacy_state()
                return

            # 1. 基本设置
            basic_settings = state.get("basic_settings") or state.get("serial_settings", {})
            port = basic_settings.get("port")
            if port and self.port_combo.findText(port) >= 0:
                self.port_combo.setCurrentText(port)
            self.baud_combo.setCurrentText(basic_settings.get("baudrate", "115200"))
            self.data_bits_combo.setCurrentText(basic_settings.get("databits", "8"))
            self.parity_combo.setCurrentText(basic_settings.get("parity", "None"))
            self.stop_bits_combo.setCurrentText(basic_settings.get("stopbits", "1"))

            # 2. 接收设置
            receive_settings = state.get("receive_settings")
            if receive_settings:
                self.show_send_source_check.setChecked(receive_settings.get("show_send_source", True))
                self.show_recv_source_check.setChecked(receive_settings.get("show_recv_source", True))
                self.show_sys_source_check.setChecked(receive_settings.get("show_sys_source", True))
                self.show_err_source_check.setChecked(receive_settings.get("show_err_source", True))
            else:
                # 兼容旧版本 (从 send_settings 中读取)
                old_send_settings = state.get("send_settings", {})
                self.show_send_source_check.setChecked(old_send_settings.get("show_send_source", True))
                self.show_recv_source_check.setChecked(old_send_settings.get("show_recv_source", True))
                self.show_sys_source_check.setChecked(old_send_settings.get("show_sys_source", True))
                self.show_err_source_check.setChecked(old_send_settings.get("show_err_source", True))

            # 3. 发送设置
            send_settings = state.get("send_settings", {})
            self.loop_send_check.setChecked(send_settings.get("loop_send", False))
            self.loop_interval_spin.setValue(send_settings.get("loop_interval", 1000))
            self.interval_spin.setValue(send_settings.get("continuous_interval", 100))
            self.show_send_check.setChecked(send_settings.get("show_send", False))
            self.send_color_combo.setCurrentText(send_settings.get("send_color", "红色"))
            self.ending_combo.setCurrentText(send_settings.get("ending", r"\r\n"))
            
            # 4. 其他设置
            other_settings = state.get("other_settings", {})
            self.timestamp_check.setChecked(other_settings.get("show_timestamp", False))

            # 5. UI 设置
            ui_settings = state.get("ui_settings") or state.get("ui_state", {})
            h_sizes = ui_settings.get("h_splitter_sizes")
            if h_sizes:
                self.h_splitter.setSizes(h_sizes)
            
            # 6. 工具设置
            tool_settings = state.get("tool_settings") or state.get("ui_state", {})
            self.tools_group.setExpanded(tool_settings.get("tools_group_expanded", False))

            # 加载命令
            saved_commands = state.get("commands")
            if saved_commands:
                self.command_table.clear_all()
                for row, cmd_data in enumerate(saved_commands):
                    self.command_table.add_command_row(cmd_data[0], cmd_data[1], cmd_data[2], row)
                self.refresh_modules()

                # 恢复选择的模块
                jump_module = send_settings.get("jump_module")
                send_module = send_settings.get("send_module")

                # 兼容旧版本: 如果没有新的设置，尝试使用旧的 selected_module
                if not jump_module and not send_module:
                    old_module = send_settings.get("selected_module")
                    if old_module:
                        jump_module = old_module
                        send_module = old_module

                if jump_module and self.jump_module_combo.findText(jump_module) >= 0:
                    self.jump_module_combo.setCurrentText(jump_module)
                if send_module and self.send_module_combo.findText(send_module) >= 0:
                    self.send_module_combo.setCurrentText(send_module)
            else:
                self.add_initial_commands(10)
        except Exception as e:
            print(f"加载状态失败: {e}")
            if self.command_table.rowCount() == 0:
                self.add_initial_commands(10)

    def _sync_mode_selection(self):
        """同步发送模式到跳转模式"""
        current_send_mode = self.send_module_combo.currentText()
        if current_send_mode:
            index = self.jump_module_combo.findText(current_send_mode)
            if index >= 0:
                self.jump_module_combo.setCurrentIndex(index)
                
                # 添加视觉反馈
                original_style = self.sync_mode_btn.styleSheet()
                self.sync_mode_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {Colors.RED_BUTTON};
                        color: white;
                        padding: 2px;
                    }}
                """)
                QTimer.singleShot(200, lambda: self.sync_mode_btn.setStyleSheet(original_style))

    def set_ending(self, ending_type):
        """设置结尾标识符
        
        Args:
            ending_type (str): 结尾标识符类型，例如: "None", "\r\n", "\r", "\n"
                               如果是转义后的字符串 (如 r"\r\n") 也会被尝试匹配
        """
        # 尝试在下拉框中找到对应的文本
        index = self.ending_combo.findText(ending_type)
        if index >= 0:
            self.ending_combo.setCurrentIndex(index)
            # 添加系统消息
            self.output_manager.append_text(f"SetEndlog: 结尾标识符已设置为 '{ending_type}'", OutputSource.SYSTEM)
        else:
            # 尝试处理转义字符的匹配 (例如输入 literal 的 \r\n 匹配 r"\r\n")
            # 这里简单做个反向查找
            found = False
            for i in range(self.ending_combo.count()):
                item_text = self.ending_combo.itemText(i)
                # 比较 item_text 和 ending_type 是否在此上下文中等价
                # 简单比较: 
                if item_text == ending_type or \
                   item_text.replace(r"\r", "\r").replace(r"\n", "\n") == ending_type:
                    self.ending_combo.setCurrentIndex(i)
                    self.output_manager.append_text(f"SetEndlog: 结尾标识符已设置为 '{item_text}'", OutputSource.SYSTEM)
                    found = True
                    break
            
            if not found:
                self.output_manager.append_text(f"SetEndlog 错误: 找不到标识符类型 '{ending_type}'", OutputSource.ERROR)

    def _load_legacy_state(self):
        """加载旧版本的平铺式配置 (用于平滑迁移) """
        try:
            self.loop_send_check.setChecked(self.config_manager.get("loop_send", False))
            self.loop_interval_spin.setValue(self.config_manager.get("loop_interval", 1000))
            self.interval_spin.setValue(self.config_manager.get("continuous_interval", 100))
            self.show_send_check.setChecked(self.config_manager.get("show_send", False))
            self.send_color_combo.setCurrentText(self.config_manager.get("send_color", "红色"))
            self.ending_combo.setCurrentText(self.config_manager.get("ending", r"\r\n"))
            
            port = self.config_manager.get("port")
            if port and self.port_combo.findText(port) >= 0:
                self.port_combo.setCurrentText(port)
            self.baud_combo.setCurrentText(self.config_manager.get("baudrate", "115200"))
            self.data_bits_combo.setCurrentText(self.config_manager.get("databits", "8"))
            self.parity_combo.setCurrentText(self.config_manager.get("parity", "None"))
            self.stop_bits_combo.setCurrentText(self.config_manager.get("stopbits", "1"))
            
            self.timestamp_check.setChecked(self.config_manager.get("show_timestamp", False))

            h_sizes = self.config_manager.get("h_splitter_sizes")
            if h_sizes:
                self.h_splitter.setSizes(h_sizes)

            saved_commands = self.config_manager.get("saved_commands")
            if saved_commands:
                self.command_table.clear_all()
                for row, cmd_data in enumerate(saved_commands):
                    self.command_table.add_command_row(cmd_data[0], cmd_data[1], cmd_data[2], row)
                self.refresh_modules()
                selected_module = self.config_manager.get("selected_module")
                if selected_module and self.jump_module_combo.findText(selected_module) >= 0:
                    self.jump_module_combo.setCurrentText(selected_module)
            else:
                self.add_initial_commands(10)
        except:
            self.add_initial_commands(10)

    def closeEvent(self, event):
        """对话框关闭事件"""
        self.save_state()
        event.accept()
