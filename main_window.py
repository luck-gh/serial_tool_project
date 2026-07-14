#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
主窗口模块, 负责串口工具 GUI 布局, 用户交互, 状态保存和远程控制协调。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import sys
import os
import csv
import re
import socket
import tempfile
from collections import OrderedDict

import serial
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QGroupBox, QLabel, QComboBox, QPushButton, QCheckBox,
                             QTextBrowser, QTableWidget, QTableWidgetItem, QHeaderView,
                             QLineEdit, QSpinBox, QDoubleSpinBox, QScrollArea, QFrame,
                             QMessageBox, QFileDialog, QDialog, QDialogButtonBox, QTextEdit,
                             QSplitter, QMenu, QAction, QSizePolicy, QStyleFactory, QInputDialog,
                             QProgressDialog, QStyle, QShortcut)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer, QEvent
from PyQt5.QtGui import QFont, QColor, QTextCursor, QIcon, QPalette, QKeySequence

from utils.ui_utils import UIUtils, Colors, resource_path, OutputSource, SpecialCommandType
from widgets.custom_widgets import CustomTextBrowser, CollapsibleGroupBox, ClickableComboBox
from widgets.command_widgets import CommandRowsTextParser, CommandTableWidget
from widgets.find_widgets import FindBar
from dialogs.replacement_rule_dialog import ReplacementRuleDialog
from dialogs.help_dialog import HelpDialog
from managers.output_manager import OutputManager
from managers.special_command_manager import SpecialCommandManager
from core.serial_thread import SerialThread
from core.remote_control import RemoteControlClient, RemoteControlServer
from core import output_rules
from app_identity import get_config_file
from managers.config_manager import ConfigManager
from dialogs.config_dialog import ConfigDialog
from widgets.base_widgets import BaseWidgetMixin
from core.text_search import find_matches, can_replace, replace_text, normalize_rule

class SerialTool(QMainWindow, BaseWidgetMixin):
    """串口调试工具主窗口"""
    LEFT_PORT_COMBO_VISIBLE_CHARS = 7
    LEFT_PANEL_DEFAULT_EXTRA_WIDTH = 60
    SYNC_BUTTON_WIDTH = 30

    def __init__(self, tool_version="0.0.0", tool_version_date="N/A", exe_name="main"):
        super().__init__()
        self.tool_version = tool_version
        self.tool_version_date = tool_version_date
        self.exe_name = exe_name
        self.serial_thread = None
        self.remote_thread = None
        self.remote_mode = "off"
        self.remote_client_connected = False
        self.remote_serial_connected = False
        self.is_connected = False
        self.send_count = 0
        self.receive_count = 0
        self.continuous_timer = QTimer()
        self.continuous_timer.timeout.connect(self.send_continuous_commands)
        self.is_continuous_sending = False
        self.current_module = "全部"
        self.modules = OrderedDict()  # 存储模块信息
        self.global_replacement_rule = None
        self.receive_find_matches = []
        self.receive_find_index = -1
        self.send_find_matches = []
        self.send_find_index = -1
        self.send_find_highlight_row = None
        self.last_find_target = "receive"
        self.help_dialog = None

        # 使用规范化后的可执行文件名来构建配置文件名。
        config_file = get_config_file(self.exe_name)
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
        """滚轮事件过滤器, 防止指定控件被滚轮误改"""
        if event.type() == QEvent.Wheel:
            if isinstance(obj, (QComboBox, QSpinBox, QDoubleSpinBox)):
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
        self.h_splitter.addWidget(self.left_panel)

        # 右侧数据交互面板
        self.right_panel = self.create_right_panel()
        self.h_splitter.addWidget(self.right_panel)

        # 设置分割器比例
        self._set_h_splitter_sizes()
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

    def _set_h_splitter_sizes(self, sizes=None):
        """设置主分割器宽度，避免左侧设置区初始状态过窄。"""
        if sizes and len(sizes) >= 2:
            try:
                left_width = int(sizes[0])
                total_width = sum(max(int(size), 0) for size in sizes[:2])
            except (TypeError, ValueError):
                left_width = self._left_panel_default_width()
                total_width = self.width()
        else:
            left_width = self._left_panel_default_width()
            total_width = self.width()

        left_panel_min_width = self._left_panel_min_width()
        left_width = max(left_panel_min_width, left_width)
        right_width = max(total_width - left_width, 1)
        self.h_splitter.setSizes([left_width, right_width])

    def _width_for_chars(self, widget, visible_chars, extra_width=36):
        """按当前字体估算控件宽度，适配不同 DPI/缩放。"""
        return widget.fontMetrics().horizontalAdvance("M" * visible_chars) + extra_width

    def _left_panel_min_width(self):
        """按当前字体估算左侧面板最小可读宽度。"""
        label_width = self._left_basic_label_width()
        scrollbar_width = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        frame_width = self.style().pixelMetric(QStyle.PM_DefaultFrameWidth)
        margins_and_spacing = 72 + scrollbar_width + frame_width * 4
        return label_width + self._left_control_area_width() + margins_and_spacing

    def _left_panel_default_width(self):
        return self._left_panel_min_width() + self.LEFT_PANEL_DEFAULT_EXTRA_WIDTH

    def _compact_button_width(self, text):
        return self.fontMetrics().horizontalAdvance(text) + 28

    def _set_compact_button_width(self, button):
        button.setFixedWidth(self._compact_button_width(button.text()))
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _left_port_combo_width(self):
        return self._width_for_chars(self, self.LEFT_PORT_COMBO_VISIBLE_CHARS)

    def _left_control_area_width(self):
        return self._compact_button_width("刷新") + 6 + self._left_port_combo_width()

    def _set_left_control_expanding(self, widget):
        """左侧普通控件只随布局伸缩，不人为设置最小宽度。"""
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _set_port_combo_compact_width(self):
        """端口框只保留短显示宽度，完整名称通过提示查看。"""
        self.port_combo.setMinimumContentsLength(self.LEFT_PORT_COMBO_VISIBLE_CHARS)
        self.port_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.port_combo.setMinimumWidth(self._left_port_combo_width())
        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _left_basic_label_width(self):
        return self.fontMetrics().horizontalAdvance("波特率:") + 8

    def _create_left_basic_label(self, text):
        label = QLabel(text)
        label.setFixedWidth(self._left_basic_label_width())
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return label

    def _add_port_combo_item(self, display):
        """添加串口下拉项，并用提示显示完整端口名称。"""
        index = self.port_combo.count()
        self.port_combo.addItem(display)
        self.port_combo.setItemData(index, display, Qt.ToolTipRole)

    def _sync_port_combo_display(self, text=None):
        """同步端口提示，并让窄输入框优先显示左侧字符。"""
        self.port_combo.setToolTip(text if text is not None else self.port_combo.currentText())
        line_edit = self.port_combo.lineEdit() if self.port_combo.isEditable() else None
        if line_edit:
            line_edit.deselect()
            line_edit.setCursorPosition(0)

    def update_tools_state(self):
        """根据配置更新所有工具按钮状态（通用方法）"""
        for tool_name, button in self.tool_buttons.items():
            is_enabled = self.config_manager.is_tool_enabled(tool_name)
            button.setEnabled(is_enabled)

            if is_enabled:
                self._apply_button_color(button, Colors.BLUE_BUTTON)
            else:
                button.setStyleSheet("QPushButton { background-color: #cccccc; color: #888888; }")

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
        self.basic_group = CollapsibleGroupBox("基本设置")
        basic_layout = self.basic_group.content_layout
        basic_layout.setSpacing(8)

        # 添加刷新按钮
        self.refresh_ports_btn = QPushButton("刷新")
        self._set_compact_button_width(self.refresh_ports_btn)
        self._bind_momentary_button_feedback(self.refresh_ports_btn, Colors.GREEN_BUTTON)

        # 左侧面板最小宽度以端口行控制区为基准，普通控件随布局自动伸缩。
        # 端口选择
        port_layout = QHBoxLayout()
        port_layout.addWidget(self._create_left_basic_label("端口:"))
        self.port_combo = ClickableComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.popupAboutToBeShown.connect(self.refresh_ports)
        self.port_combo.currentTextChanged.connect(self._sync_port_combo_display)
        self.port_combo.installEventFilter(self)            # 禁用滚轮
        self._set_port_combo_compact_width()
        port_layout.addWidget(self.refresh_ports_btn)
        port_layout.addWidget(self.port_combo)
        basic_layout.addLayout(port_layout)

        # 波特率
        baud_layout = QHBoxLayout()
        baud_layout.addWidget(self._create_left_basic_label("波特率:"))
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["9600", "115200", "57600", "38400", "19200", "4800"])
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.installEventFilter(self)            # 禁用滚轮
        self._set_left_control_expanding(self.baud_combo)
        baud_layout.addWidget(self.baud_combo)
        basic_layout.addLayout(baud_layout)

        # 数据位
        data_bits_layout = QHBoxLayout()
        data_bits_layout.addWidget(self._create_left_basic_label("数据位:"))
        self.data_bits_combo = QComboBox()
        self.data_bits_combo.addItems(["5", "6", "7", "8"])
        self.data_bits_combo.setCurrentText("8")
        self.data_bits_combo.installEventFilter(self)       # 禁用滚轮
        self._set_left_control_expanding(self.data_bits_combo)
        data_bits_layout.addWidget(self.data_bits_combo)
        basic_layout.addLayout(data_bits_layout)

        # 校验位
        parity_layout = QHBoxLayout()
        parity_layout.addWidget(self._create_left_basic_label("校验位:"))
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(["None", "Even", "Odd", "Mark"])
        self.parity_combo.setCurrentText("None")
        self.parity_combo.installEventFilter(self)          # 禁用滚轮
        self._set_left_control_expanding(self.parity_combo)
        parity_layout.addWidget(self.parity_combo)
        basic_layout.addLayout(parity_layout)

        # 停止位
        stop_bits_layout = QHBoxLayout()
        stop_bits_layout.addWidget(self._create_left_basic_label("停止位:"))
        self.stop_bits_combo = QComboBox()
        self.stop_bits_combo.addItems(["1", "1.5", "2"])
        self.stop_bits_combo.setCurrentText("1")
        self.stop_bits_combo.installEventFilter(self)       # 禁用滚轮
        self._set_left_control_expanding(self.stop_bits_combo)
        stop_bits_layout.addWidget(self.stop_bits_combo)
        basic_layout.addLayout(stop_bits_layout)

        # 打开串口按钮
        self.connect_btn = QPushButton("打开串口")
        self.connect_btn.setToolTip("打开/关闭串口 (Ctrl+O)")
        self._set_left_control_expanding(self.connect_btn)
        self.connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """)
        basic_layout.addWidget(self.connect_btn)

        layout.addWidget(self.basic_group)

        # 远程控制
        self.remote_group = CollapsibleGroupBox("远程控制")
        remote_layout = self.remote_group.content_layout
        remote_layout.setSpacing(8)

        remote_mode_layout = QHBoxLayout()
        remote_mode_layout.addWidget(QLabel("模式:"))
        self.remote_mode_combo = QComboBox()
        self.remote_mode_combo.addItems(["主控端", "远程端"])
        self.remote_mode_combo.installEventFilter(self)
        self._set_left_control_expanding(self.remote_mode_combo)
        remote_mode_layout.addWidget(self.remote_mode_combo)
        remote_layout.addLayout(remote_mode_layout)

        remote_host_layout = QHBoxLayout()
        remote_host_layout.addWidget(QLabel("地址:"))
        self.remote_host_input = QComboBox()
        self.remote_host_input.setEditable(True)
        self.remote_host_input.addItem("127.0.0.1")
        self.remote_host_input.installEventFilter(self)
        self._set_left_control_expanding(self.remote_host_input)
        remote_host_layout.addWidget(self.remote_host_input)
        remote_layout.addLayout(remote_host_layout)

        remote_port_layout = QHBoxLayout()
        remote_port_layout.addWidget(QLabel("端口:"))
        self.remote_port_spin = QSpinBox()
        self.remote_port_spin.setRange(1, 65535)
        self.remote_port_spin.setValue(8765)
        self._set_left_control_expanding(self.remote_port_spin)
        self.remote_port_spin.installEventFilter(self)            # 禁用滚轮
        remote_port_layout.addWidget(self.remote_port_spin)
        remote_layout.addLayout(remote_port_layout)

        remote_token_layout = QHBoxLayout()
        remote_token_layout.addWidget(QLabel("密码:"))
        self.remote_show_token_check = QCheckBox("显示密码")
        remote_token_layout.addWidget(self.remote_show_token_check)
        self.remote_token_input = QLineEdit()
        self.remote_token_input.setEchoMode(QLineEdit.Password)
        self._set_left_control_expanding(self.remote_token_input)
        remote_token_layout.addWidget(self.remote_token_input)
        remote_layout.addLayout(remote_token_layout)

        self.remote_toggle_btn = QPushButton("启用远程控制")
        self._set_left_control_expanding(self.remote_toggle_btn)
        self._apply_button_color(self.remote_toggle_btn, Colors.BLUE_BUTTON)
        remote_layout.addWidget(self.remote_toggle_btn)

        self.remote_status_label = QLabel("远程: 未启用")
        remote_layout.addWidget(self.remote_status_label)

        layout.addWidget(self.remote_group)

        # 接收设置
        self.receive_group = CollapsibleGroupBox("接收设置")
        receive_layout = self.receive_group.content_layout
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
        self._set_left_control_expanding(self.save_btn)
        self._bind_momentary_button_feedback(self.save_btn, Colors.GREEN_BUTTON)

        self.clear_receive_btn = QPushButton("清空数据")
        self._set_left_control_expanding(self.clear_receive_btn)
        self._bind_momentary_button_feedback(self.clear_receive_btn, Colors.GREEN_BUTTON)

        receive_layout.addWidget(self.save_btn)
        receive_layout.addWidget(self.clear_receive_btn)

        layout.addWidget(self.receive_group)

        # 发送设置
        self.send_group = CollapsibleGroupBox("发送设置")
        send_layout = self.send_group.content_layout
        send_layout.setSpacing(8)

        # 结尾标识符
        self.ending_combo = QComboBox()
        self.ending_combo.addItems(["None", r"\r\n", r"\r", r"\n"])
        self.ending_combo.setCurrentText(r"\r\n")
        self._set_left_control_expanding(self.ending_combo)
        self.ending_combo.installEventFilter(self)          # 禁用滚轮
        # 发送选项统一使用两列网格：左侧对齐，右侧随侧栏宽度伸缩。
        send_options_grid = QGridLayout()
        send_options_grid.setContentsMargins(0, 0, 0, 0)
        send_options_grid.setHorizontalSpacing(8)
        send_options_grid.setVerticalSpacing(8)
        send_options_grid.setColumnStretch(0, 0)
        send_options_grid.setColumnStretch(1, 1)

        ending_label = QLabel("结尾标识符")
        send_options_grid.addWidget(ending_label, 0, 0)
        send_options_grid.addWidget(self.ending_combo, 0, 1)

        # 显示发送字符串
        self.show_send_check = QCheckBox("显示发送字符串")
        self.show_send_check.setChecked(False)
        send_options_grid.addWidget(self.show_send_check, 1, 0)

        self.send_color_combo = QComboBox()
        self.send_color_combo.addItems(["红色", "蓝色", "绿色", "紫色", "黑色"])
        self.send_color_combo.setCurrentText("红色")
        self._set_left_control_expanding(self.send_color_combo)
        self.send_color_combo.installEventFilter(self)      # 禁用滚轮
        send_options_grid.addWidget(self.send_color_combo, 1, 1)

        self.replace_send_check = QCheckBox("替换发送")
        self.replace_send_check.setToolTip("仅在发送瞬间替换字符串，快捷键 Ctrl+T")
        send_options_grid.addWidget(self.replace_send_check, 2, 0)
        self.replacement_rule_btn = QPushButton("全局规则...")
        self._set_left_control_expanding(self.replacement_rule_btn)
        self._bind_momentary_button_feedback(
            self.replacement_rule_btn,
            Colors.PURPLE_BUTTON,
            Colors.PURPLE_BUTTON_DARK,
        )
        send_options_grid.addWidget(self.replacement_rule_btn, 2, 1)

        # 循环发送设置
        self.loop_send_check = QCheckBox("循环发送(ms)")
        self.loop_send_check.setChecked(False)
        send_options_grid.addWidget(self.loop_send_check, 3, 0)
        self.loop_interval_spin = QSpinBox()
        self.loop_interval_spin.setRange(10, 60000)
        self.loop_interval_spin.setValue(1000)
        self.loop_interval_spin.installEventFilter(self)
        self._set_left_control_expanding(self.loop_interval_spin)
        send_options_grid.addWidget(self.loop_interval_spin, 3, 1)

        # 连续发送间隔
        send_options_grid.addWidget(QLabel("连续发送间隔(ms)"), 4, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 10000)
        self.interval_spin.setValue(100)
        self.interval_spin.installEventFilter(self)
        self._set_left_control_expanding(self.interval_spin)
        send_options_grid.addWidget(self.interval_spin, 4, 1)

        send_layout.addLayout(send_options_grid)

        layout.addWidget(self.send_group)

        # 其他设置
        self.other_group = CollapsibleGroupBox("其他设置")
        other_layout = self.other_group.content_layout
        other_layout.setSpacing(8)

        self.timestamp_check = QCheckBox("显示时间戳")
        self.timestamp_check.setChecked(False)
        other_layout.addWidget(self.timestamp_check)

        layout.addWidget(self.other_group)

        # 模板相关
        self.template_group = CollapsibleGroupBox("模板相关")
        template_layout = self.template_group.content_layout
        template_layout.setSpacing(8)

        template_buttons_layout = QHBoxLayout()
        self.import_btn = QPushButton("导入模板")
        self._set_left_control_expanding(self.import_btn)
        self._set_action_button_running(self.import_btn, False, Colors.GREEN_BUTTON)

        self.export_btn = QPushButton("导出模板")
        self._set_left_control_expanding(self.export_btn)
        self._set_action_button_running(self.export_btn, False, Colors.GREEN_BUTTON)

        template_buttons_layout.addWidget(self.import_btn)
        template_buttons_layout.addWidget(self.export_btn)
        template_layout.addLayout(template_buttons_layout)

        layout.addWidget(self.template_group)

        # 添加弹性空间
        layout.addStretch()

        # 工具相关
        self.tools_group = CollapsibleGroupBox("工具相关")

        # 动态创建工具按钮（基于配置管理器的注册表）
        self.tool_buttons = {}  # 保存工具按钮引用 {tool_name: button}
        for tool_name in self.config_manager.get_all_tool_names():
            button_text = self.config_manager.get_tool_button_text(tool_name)
            btn = QPushButton(button_text)
            self._set_left_control_expanding(btn)
            self.tools_group.addWidget(btn)
            self.tool_buttons[tool_name] = btn

        layout.addWidget(self.tools_group)

        # 帮助与配置按钮
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)
        footer_button_height = 36
        footer_button_style = (
            "font-size: 13px; font-weight: bold; padding: 0px; "
            "border-radius: 4px;"
        )

        self.help_btn = QPushButton("?")
        self.help_btn.setToolTip("打开帮助 (F1)")
        self.help_btn.setAccessibleName("帮助")
        self._bind_momentary_button_feedback(
            self.help_btn,
            Colors.BLUE_BUTTON,
            extra_styles=footer_button_style,
        )
        footer_layout.addWidget(self.help_btn)

        self.config_btn = QPushButton("配置")
        self._set_left_control_expanding(self.config_btn)
        self.config_btn.setMinimumWidth(self.config_btn.sizeHint().width())
        self._bind_momentary_button_feedback(
            self.config_btn,
            Colors.BLUE_BUTTON,
            extra_styles=footer_button_style,
        )
        self.help_btn.setFixedSize(footer_button_height, footer_button_height)
        self.config_btn.setFixedHeight(footer_button_height)

        footer_layout.addWidget(self.config_btn, 1)
        layout.addLayout(footer_layout)

        scroll_area.setMinimumWidth(self._left_panel_min_width())
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
        self.receive_browser = CustomTextBrowser(
            self.config_manager,
            save_callback=self.save_receive_data,
            clear_callback=self.clear_receive_data
        )
        self.receive_browser.setFont(QFont("Consolas", 10))
        self.receive_find_bar = FindBar()
        self.receive_find_bar.hide()
        receive_layout.addWidget(self.receive_find_bar)
        receive_layout.addWidget(self.receive_browser)

        # 发送编辑区
        send_group = QGroupBox("发送编辑区")
        send_layout = QVBoxLayout(send_group)

        # 表头
        header_layout = QHBoxLayout()
        send_layout.addLayout(header_layout)

        self.send_find_bar = FindBar(show_scope=True)
        self.send_find_bar.hide()
        send_layout.addWidget(self.send_find_bar)

        # 命令表格
        self.command_table = CommandTableWidget(self.config_manager)
        self.command_table.cellDoubleClicked.connect(self.on_cell_double_clicked)
        send_layout.addWidget(self.command_table)

        # 追加命令按钮
        self.add_command_btn = QPushButton("追加命令")
        self._bind_momentary_button_feedback(self.add_command_btn, Colors.GREEN_BUTTON)
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
        self._bind_momentary_button_feedback(self.refresh_modules_btn, Colors.GREEN_BUTTON)
        continuous_layout.addWidget(self.refresh_modules_btn)

        # 跳转到此 - 模块选择
        jump_label = QLabel("跳转:")
        jump_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        continuous_layout.addWidget(jump_label)

        self.jump_row_input = QLineEdit()
        self.jump_row_input.setPlaceholderText("行号")
        self.jump_row_input.setMaximumWidth(self._width_for_chars(self.jump_row_input, 5, 24))
        continuous_layout.addWidget(self.jump_row_input)

        self.jump_row_btn = QPushButton("跳行")
        self._bind_momentary_button_feedback(self.jump_row_btn, Colors.GREEN_BUTTON)
        continuous_layout.addWidget(self.jump_row_btn)

        self.jump_module_combo = QComboBox()
        self.jump_module_combo.addItem("全部")
        self.jump_module_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        continuous_layout.addWidget(self.jump_module_combo, 1)

        # Mode Sync Button - 将跳转模块同步到发送模块
        self.sync_mode_forward_btn = QPushButton("->")
        self.sync_mode_forward_btn.setFixedWidth(self.SYNC_BUTTON_WIDTH)
        self.sync_mode_forward_btn.setToolTip("将跳转模式同步到发送模式")
        self._bind_momentary_button_feedback(self.sync_mode_forward_btn, Colors.BLUE_BUTTON, extra_styles="padding: 2px;")
        continuous_layout.addWidget(self.sync_mode_forward_btn)

        # 新增的跳转到此按钮
        self.jump_to_module_btn = QPushButton("跳转到此")
        self._bind_momentary_button_feedback(self.jump_to_module_btn, Colors.BLUE_BUTTON)
        continuous_layout.addWidget(self.jump_to_module_btn)

        # Mode Sync Button - 将发送模块同步到跳转模块
        self.sync_mode_btn = QPushButton("<-")
        self.sync_mode_btn.setFixedWidth(self.SYNC_BUTTON_WIDTH)
        self.sync_mode_btn.setToolTip("将发送模式同步到跳转模式")
        self._bind_momentary_button_feedback(self.sync_mode_btn, Colors.BLUE_BUTTON, extra_styles="padding: 2px;")
        continuous_layout.addWidget(self.sync_mode_btn)

        # 连续发送 - 模块选择
        send_label = QLabel("发送:")
        send_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        continuous_layout.addWidget(send_label)

        self.send_module_combo = QComboBox()
        self.send_module_combo.addItem("全部")
        self.send_module_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        continuous_layout.addWidget(self.send_module_combo, 1)

        self.continuous_btn = QPushButton("连续发送")
        self.continuous_btn.setToolTip("开始/停止连续发送 (Ctrl+R)")
        self._set_action_button_running(self.continuous_btn, False, Colors.GREEN_BUTTON)
        continuous_layout.addWidget(self.continuous_btn)

        layout.addWidget(continuous_group)

        # 统计栏
        stats_layout = QHBoxLayout()

        self.send_count_label = QLabel("发送: 0 字节")
        self.receive_count_label = QLabel("接收: 0 字节")
        self.reset_stats_btn = QPushButton("复位")
        self._bind_momentary_button_feedback(self.reset_stats_btn, Colors.BLUE_BUTTON)

        stats_layout.addWidget(self.send_count_label)
        stats_layout.addWidget(self.receive_count_label)
        stats_layout.addStretch()
        stats_layout.addWidget(self.reset_stats_btn)

        layout.addLayout(stats_layout)

        return panel

    def connect_signals(self):
        """连接信号槽"""
        self.connect_btn.clicked.connect(self.toggle_serial_connection)
        self.remote_toggle_btn.clicked.connect(self.toggle_remote_control)
        self.remote_mode_combo.currentTextChanged.connect(self.update_remote_host_options)
        self.remote_show_token_check.toggled.connect(self.toggle_remote_token_visibility)
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
        self.help_btn.clicked.connect(self.open_help_dialog)
        self.jump_to_module_btn.clicked.connect(self._jump_to_module_row)
        self.jump_row_btn.clicked.connect(self._jump_to_row)
        self.sync_mode_forward_btn.clicked.connect(self._sync_mode_forward_selection)
        self.sync_mode_btn.clicked.connect(self._sync_mode_selection)
        self.replace_send_check.toggled.connect(self.update_replacement_mode_ui)
        self.replacement_rule_btn.clicked.connect(self.edit_global_replacement_rule)
        self.command_table.commandsChanged.connect(self._on_command_table_changed)
        self.command_table.replacementRulesChanged.connect(self.update_replacement_mode_ui)
        self.receive_browser.textChanged.connect(self._refresh_receive_find_if_visible)
        self.receive_find_bar.searchChanged.connect(self._search_receive_text)
        self.receive_find_bar.navigateRequested.connect(self._navigate_receive_find)
        self.receive_find_bar.closed.connect(self._clear_receive_find)
        self.send_find_bar.searchChanged.connect(self._search_send_commands)
        self.send_find_bar.navigateRequested.connect(self._navigate_send_find)
        self.send_find_bar.closed.connect(self._clear_send_find)

        self.find_shortcut = QShortcut(QKeySequence.Find, self)
        self.find_shortcut.setContext(Qt.WindowShortcut)
        self.find_shortcut.activated.connect(self.open_context_find)
        self.replace_mode_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.replace_mode_shortcut.setContext(Qt.WindowShortcut)
        self.replace_mode_shortcut.activated.connect(self.toggle_replacement_mode)
        self.help_shortcut = QShortcut(QKeySequence.HelpContents, self)
        self.help_shortcut.setContext(Qt.WindowShortcut)
        self.help_shortcut.activated.connect(self.open_help_dialog)
        self.serial_toggle_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.serial_toggle_shortcut.setContext(Qt.WindowShortcut)
        self.serial_toggle_shortcut.activated.connect(self.toggle_serial_connection)
        self.continuous_send_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        self.continuous_send_shortcut.setContext(Qt.WindowShortcut)
        self.continuous_send_shortcut.activated.connect(self.toggle_continuous_send)

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
        """工具按钮点击处理"""
        self._apply_button_color(button, Colors.RED_BUTTON)
        try:
            method()
        finally:
            if button.isEnabled():
                self._apply_button_color(button, Colors.BLUE_BUTTON)

    def refresh_ports(self):
        """刷新可用串口列表"""
        if self.is_remote_client_active():
            self.refresh_remote_ports()
            return
        current_text = self.port_combo.currentText()
        self.port_combo.clear()

        ports = serial.tools.list_ports.comports()
        for port in ports:
            self._add_port_combo_item(f"{port.device} - {port.description}")

        # 恢复之前的选择
        if current_text and self.port_combo.findText(current_text) >= 0:
            self.port_combo.setCurrentText(current_text)
        self._sync_port_combo_display()

    def get_selected_port_id(self):
        return self.port_combo.currentText().strip().split(" ")[0]

    def get_selected_port_node(self):
        text = self.port_combo.currentText().strip()
        port_id = self.get_selected_port_id()
        suffix = f" ({port_id})"
        if port_id and text.endswith(suffix):
            text = text[:-len(suffix)].strip()
        return text

    def refresh_remote_ports(self):
        """远程端请求主控端扫描串口并同步基本设置"""
        if not self.remote_thread or not self.remote_client_connected:
            self.output_manager.append_text("错误: 远程串口未连接", OutputSource.ERROR)
            return
        self.output_manager.append_text("主控端刷新端口", OutputSource.SYSTEM)
        self.remote_thread.send_serial_control("refresh_ports")

    def get_local_ipv4_addresses(self):
        """获取本机可用于局域网连接的 IPv4 地址"""
        addresses = []
        try:
            hostname = socket.gethostname()
            for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = item[4][0]
                if ip not in addresses:
                    addresses.append(ip)
        except OSError:
            pass

        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if ip not in addresses:
                addresses.insert(0, ip)
            probe.close()
        except OSError:
            pass

        if "127.0.0.1" not in addresses:
            addresses.append("127.0.0.1")
        return addresses

    def update_remote_host_options(self):
        """根据远程模式刷新地址选项"""
        current_text = self.remote_host_input.currentText().strip()
        self.remote_host_input.blockSignals(True)
        self.remote_host_input.clear()

        if self.remote_mode_combo.currentText() == "主控端":
            for ip in self.get_local_ipv4_addresses():
                self.remote_host_input.addItem(ip)
            if current_text and self.remote_host_input.findText(current_text) >= 0:
                self.remote_host_input.setCurrentText(current_text)
            elif self.remote_host_input.count() > 0:
                self.remote_host_input.setCurrentIndex(0)
        else:
            if current_text:
                self.remote_host_input.addItem(current_text)
                self.remote_host_input.setCurrentText(current_text)
            else:
                self.remote_host_input.addItem("127.0.0.1")
                self.remote_host_input.setCurrentText("127.0.0.1")

        self.remote_host_input.blockSignals(False)

    def toggle_remote_token_visibility(self, checked):
        """显示或隐藏远程控制密码"""
        self.remote_token_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def toggle_serial_connection(self):
        """打开/关闭串口连接"""
        if self.is_remote_client_active():
            self.toggle_remote_serial_connection()
            return
        if not self.is_connected:
            self.open_serial()
        else:
            self.close_serial()

    def toggle_remote_control(self):
        """启用/关闭远程控制"""
        if self.remote_thread:
            self.stop_remote_control()
        else:
            self.start_remote_control()

    def start_remote_control(self):
        """启动远程控制服务端或客户端"""
        mode_text = self.remote_mode_combo.currentText()
        host = self.remote_host_input.currentText().strip()
        port = self.remote_port_spin.value()
        token = self.remote_token_input.text()

        if mode_text == "主控端":
            self.remote_thread = RemoteControlServer("0.0.0.0", port, token)
            self.remote_mode = "server"
            selected_host = host or "本机全部地址"
            self.output_manager.append_text(
                f"主控端将监听端口 {port}。远程端可连接地址: {selected_host}",
                OutputSource.SYSTEM
            )
            if not self.is_connected or not self.serial_thread:
                self.output_manager.append_text("提示: 主控端当前未打开串口，远程端连接后需等待本机串口恢复", OutputSource.SYSTEM)
        else:
            if not host:
                self.output_manager.append_text("错误: 请输入远程主控端地址", OutputSource.ERROR)
                return
            if self.is_connected:
                self.output_manager.append_text("错误: 远程端模式下请先关闭本地串口", OutputSource.ERROR)
                return
            self.output_manager.append_text(f"正在连接远程主控端: {host}:{port}", OutputSource.SYSTEM)
            self.remote_thread = RemoteControlClient(host, port, token)
            self.remote_mode = "client"

        self.remote_thread.data_received.connect(self.on_remote_data_received)
        self.remote_thread.status_changed.connect(self.on_remote_status)
        self.remote_thread.error_occurred.connect(self.on_remote_error)
        self.remote_thread.connected_changed.connect(self.on_remote_connected_changed)
        self.remote_thread.baudrate_requested.connect(self.on_remote_baudrate_requested)
        self.remote_thread.serial_config_received.connect(self.on_remote_serial_config_received)
        self.remote_thread.serial_control_requested.connect(self.on_remote_serial_control_requested)
        self.remote_thread.start()

        self.remote_toggle_btn.setText("关闭远程控制")
        self._apply_button_color(self.remote_toggle_btn, Colors.RED_BUTTON)
        self.remote_mode_combo.setEnabled(False)
        self.remote_host_input.setEnabled(False)
        self.remote_port_spin.setEnabled(False)
        self.remote_token_input.setEnabled(False)
        self.remote_show_token_check.setEnabled(False)
        self._set_remote_status("远程: 启动中")

    def stop_remote_control(self):
        """停止远程控制"""
        was_client = self.remote_mode == "client"
        if self.remote_thread:
            self.remote_thread.stop()
            self.remote_thread = None
        if was_client:
            self.is_connected = False
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("打开串口")
            self._apply_button_color(self.connect_btn, Colors.BLUE_BUTTON)
        self.remote_mode = "off"
        self.remote_client_connected = False
        self.remote_serial_connected = False
        self.remote_toggle_btn.setText("启用远程控制")
        self._apply_button_color(self.remote_toggle_btn, Colors.BLUE_BUTTON)
        self.remote_mode_combo.setEnabled(True)
        self.remote_host_input.setEnabled(True)
        self.remote_port_spin.setEnabled(True)
        self.remote_token_input.setEnabled(True)
        self.remote_show_token_check.setEnabled(True)
        self.set_basic_group_remote_style(False)
        self._set_remote_status("远程: 未启用")
        self.output_manager.append_text("远程控制已关闭", OutputSource.SYSTEM)

    def on_remote_status(self, message):
        """远程控制状态消息"""
        self._set_remote_status(f"远程: {message}")
        self.output_manager.append_text(message, OutputSource.SYSTEM)

    def on_remote_error(self, message, fatal=False):
        """远程控制错误消息"""
        self.output_manager.append_text(f"错误: {message}", OutputSource.ERROR)
        if fatal and self.remote_mode == "client" and self.remote_thread:
            self.stop_remote_control()

    def on_remote_connected_changed(self, connected):
        """远程连接状态变更"""
        self.remote_client_connected = connected
        if self.remote_mode == "client":
            self.connect_btn.setEnabled(connected)
            if not connected:
                self.remote_serial_connected = False
                self.update_connect_button_state(False)
            self.set_basic_group_remote_style(connected)
        elif self.remote_mode == "server" and connected:
            self.send_remote_serial_config()
        status = "已连接" if connected else ("监听中" if self.remote_mode == "server" else "未连接")
        self._set_remote_status(f"远程: {status}")

    def on_remote_data_received(self, data):
        """远程端收到主控端串口回包，或主控端收到远程端发送数据"""
        if self.remote_mode == "server":
            sent_bytes = self._write_serial_data(data)
            if sent_bytes > 0:
                self.send_count += sent_bytes
                self.update_statistics()
                self.output_manager.reset_receive_timestamp()
                if self.show_send_check.isChecked():
                    self.output_manager.append_text(f"[远程发送 HEX]: {data.hex(' ').upper()}", OutputSource.SEND)
            else:
                self.output_manager.append_text("错误: 主控端本地串口未打开，无法转发远程数据", OutputSource.ERROR)
                if self.remote_thread and self.remote_client_connected:
                    self.remote_thread.send_error("主控端本地串口未打开，无法发送")
        elif self.remote_mode == "client":
            self.on_data_received(data)

    def on_remote_baudrate_requested(self, baudrate):
        """远程端请求主控端修改波特率"""
        if self.remote_mode == "server":
            self.update_baudrate(baudrate, notify_remote=False)

    def on_remote_serial_control_requested(self, action, config):
        """主控端处理远程打开/关闭本地串口请求"""
        if self.remote_mode != "server":
            return

        if action == "open":
            self.apply_serial_config_to_basic_settings(config)
            self.output_manager.append_text("收到远程端打开串口请求", OutputSource.SYSTEM)
            self.open_serial()
            if self.is_connected and self.serial_thread:
                self.remote_thread._queue_message({
                    "type": "status",
                    "message": "主控端打开串口"
                })
        elif action == "close":
            self.output_manager.append_text("收到远程端关闭串口请求", OutputSource.SYSTEM)
            if self.is_connected or self.serial_thread:
                self.close_serial()
            else:
                self.send_remote_serial_config()
                if self.remote_thread and self.remote_client_connected:
                    self.remote_thread.send_error("主控端串口已处于关闭状态")
        elif action == "refresh_ports":
            self.output_manager.append_text("收到远程端刷新端口请求", OutputSource.SYSTEM)
            self.refresh_ports()
            self.send_remote_serial_config(include_ports=True)

    def on_remote_serial_config_received(self, config):
        """远程端应用主控端串口参数"""
        if self.remote_mode != "client":
            return
        self.apply_remote_serial_config(config)

    def get_current_serial_config(self, include_ports=False):
        """获取当前基本设置中的串口参数"""
        connected = self.remote_serial_connected if self.remote_mode == "client" else (
            self.is_connected and self.serial_thread is not None
        )
        config = {
            "port": self.port_combo.currentText(),
            "baudrate": self.baud_combo.currentText(),
            "databits": self.data_bits_combo.currentText(),
            "parity": self.parity_combo.currentText(),
            "stopbits": self.stop_bits_combo.currentText(),
            "connected": connected
        }
        if include_ports:
            config["ports"] = [
                {
                    "device": port.device,
                    "description": port.description,
                    "display": f"{port.device} - {port.description}"
                }
                for port in serial.tools.list_ports.comports()
            ]
        return config

    def toggle_remote_serial_connection(self):
        """远程端请求主控端打开或关闭本地串口"""
        if not self.remote_thread or not self.remote_client_connected:
            self.output_manager.append_text("错误: 远程串口未连接", OutputSource.ERROR)
            return

        if self.remote_serial_connected:
            self.output_manager.append_text("主控端关闭串口", OutputSource.SYSTEM)
            self.remote_thread.send_serial_control("close")
        else:
            self.output_manager.append_text("主控端打开串口", OutputSource.SYSTEM)
            self.remote_thread.send_serial_control("open", self.get_current_serial_config())

    def send_remote_serial_config(self, include_ports=False):
        """主控端向远程端同步串口参数"""
        if self.remote_mode == "server" and self.remote_thread and self.remote_client_connected:
            self.remote_thread.send_serial_config(self.get_current_serial_config(include_ports=include_ports))

    def apply_remote_serial_config(self, config):
        """远程端显示主控端硬件串口参数"""
        self.apply_remote_port_list(config)
        self.apply_serial_config_to_basic_settings(config)
        if self.remote_mode == "client":
            self.remote_serial_connected = bool(config.get("connected", False))
            self.connect_btn.setEnabled(self.remote_client_connected)
            self.update_connect_button_state(self.remote_serial_connected)
        self.set_basic_group_remote_style(True)

    def apply_remote_port_list(self, config):
        """远程端应用主控端扫描到的端口列表"""
        ports = config.get("ports")
        if not isinstance(ports, list):
            return

        current_text = self.port_combo.currentText()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for port in ports:
            if isinstance(port, dict):
                display = port.get("display") or port.get("device") or ""
            else:
                display = str(port)
            if display:
                self._add_port_combo_item(display)

        target_port = str(config.get("port", ""))
        if target_port and self.port_combo.findText(target_port) >= 0:
            self.port_combo.setCurrentText(target_port)
        elif target_port:
            for index in range(self.port_combo.count()):
                if self.port_combo.itemText(index).split(" ")[0] == target_port.split(" ")[0]:
                    self.port_combo.setCurrentIndex(index)
                    break
            else:
                self._add_port_combo_item(target_port)
                self.port_combo.setCurrentText(target_port)
        elif current_text and self.port_combo.findText(current_text) >= 0:
            self.port_combo.setCurrentText(current_text)
        self._sync_port_combo_display()
        self.port_combo.blockSignals(False)

    def update_connect_button_state(self, connected):
        """按连接状态刷新打开/关闭串口按钮"""
        self.connect_btn.setText("关闭串口" if connected else "打开串口")
        self.connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.RED_BUTTON if connected else Colors.BLUE_BUTTON};
                color: white;
            }}
        """)

    def is_remote_client_active(self):
        """远程端虚拟基本设置状态：只向主控端发送控制，不操作本机串口"""
        return self.remote_mode == "client" and self.remote_thread is not None and self.remote_client_connected

    def can_send_serial_data(self):
        """当前后端是否允许发送串口数据"""
        if self.is_remote_client_active():
            return self.remote_serial_connected
        return self.is_connected and self.serial_thread is not None

    def apply_serial_config_to_basic_settings(self, config):
        """将串口配置写入基本设置控件"""
        port = str(config.get("port", ""))
        if port:
            if self.port_combo.findText(port) < 0:
                self._add_port_combo_item(port)
            self.port_combo.setCurrentText(port)
            self._sync_port_combo_display()
        self.baud_combo.setCurrentText(str(config.get("baudrate", "115200")))
        self.data_bits_combo.setCurrentText(str(config.get("databits", "8")))
        self.parity_combo.setCurrentText(str(config.get("parity", "None")))
        self.stop_bits_combo.setCurrentText(str(config.get("stopbits", "1")))

    def set_basic_group_remote_style(self, remote_active):
        """远程端连接时标记基本设置来自主控端硬件"""
        if remote_active:
            self.basic_group.setTitle("基本设置 (主控端信息)")
            self.basic_group.content_widget.setStyleSheet("""
                #content_widget {
                    border: 1px solid #7aa7d9;
                    border-radius: 6px;
                    background-color: #eef6ff;
                }
            """)
            self.basic_group.toggle_button.setToolTip("远程端模式：基本设置显示主控端硬件串口参数")
        else:
            self.basic_group.setTitle("基本设置")
            self.basic_group.content_widget.setStyleSheet("""
                #content_widget {
                    border: 1px solid #d0d0d0;
                    border-radius: 6px;
                    background-color: white;
                }
            """)
            self.basic_group.toggle_button.setToolTip("")

    def _set_remote_status(self, text):
        self.remote_status_label.setText(text)

    def _write_serial_data(self, data):
        """向当前连接后端写入原始字节"""
        if self.remote_mode == "client":
            if self.remote_thread and self.remote_client_connected:
                self.remote_thread.send_serial_data(data)
                return len(data)
            return 0
        if self.serial_thread:
            return self.serial_thread.write_data(data)
        return 0

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
            self.send_remote_serial_config()

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
        self.send_remote_serial_config()

    def on_data_received(self, data):
        """接收数据回调"""
        if self.remote_mode == "server" and self.remote_thread and self.remote_client_connected:
            self.remote_thread.send_serial_data(data)
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
        dialog = ConfigDialog(self.config_manager, self)
        if dialog.exec_():
            # 配置已保存，可以执行一些刷新操作
            self.output_manager.append_text("配置已更新", OutputSource.SYSTEM)
            self.update_tools_state()

    def open_help_dialog(self):
        """打开帮助窗口，并重新载入最新的 README 内容。"""
        if self.help_dialog is None:
            self.help_dialog = HelpDialog(self)
        self.help_dialog.reload_content()
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()


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

    def open_context_find(self):
        """根据当前焦点打开接收区或发送区的独立查找栏。"""
        focus = QApplication.focusWidget()
        if focus is self.receive_browser or (
            focus is not None and self.receive_browser.isAncestorOf(focus)
        ):
            self.last_find_target = "receive"
        elif focus is self.command_table or (
            focus is not None and self.command_table.isAncestorOf(focus)
        ):
            self.last_find_target = "send"

        if self.last_find_target == "send":
            selected = focus.selectedText() if hasattr(focus, "selectedText") else ""
            self.send_find_bar.show_and_focus(selected)
        else:
            cursor = self.receive_browser.textCursor()
            selected = cursor.selectedText() if cursor.hasSelection() else ""
            self.receive_find_bar.show_and_focus(selected)

    def _search_receive_text(self, query, options, _scope="全部"):
        self.receive_find_matches = []
        self.receive_find_index = -1
        try:
            self.receive_find_matches = find_matches(
                self.receive_browser.toPlainText(), query, options
            )
        except ValueError as exc:
            self.receive_find_bar.set_result(0, 0, str(exc))
            self.receive_browser.setExtraSelections([])
            return

        if self.receive_find_matches:
            self.receive_find_index = 0
        self._render_receive_find()

    def _render_receive_find(self):
        selections = []
        for index, (start, end) in enumerate(self.receive_find_matches):
            selection = QTextEdit.ExtraSelection()
            cursor = self.receive_browser.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection.cursor = cursor
            selection.format.setBackground(
                QColor("#FF9800" if index == self.receive_find_index else "#FFF59D")
            )
            selections.append(selection)
        self.receive_browser.setExtraSelections(selections)

        total = len(self.receive_find_matches)
        current = self.receive_find_index + 1 if total else 0
        self.receive_find_bar.set_result(current, total)
        if total:
            start, end = self.receive_find_matches[self.receive_find_index]
            cursor = self.receive_browser.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            self.receive_browser.setTextCursor(cursor)
            self.receive_browser.ensureCursorVisible()

    def _navigate_receive_find(self, direction):
        if not self.receive_find_matches:
            return
        self.receive_find_index = (
            self.receive_find_index + direction
        ) % len(self.receive_find_matches)
        self._render_receive_find()

    def _clear_receive_find(self):
        self.receive_find_matches = []
        self.receive_find_index = -1
        self.receive_browser.setExtraSelections([])

    def _refresh_receive_find_if_visible(self):
        if self.receive_find_bar.isVisible():
            self.receive_find_bar._emit_search()

    def _send_find_rows(self, scope):
        if scope == "全部":
            return range(self.command_table.rowCount())
        return self.modules.get(scope, [])

    def _search_send_commands(self, query, options, scope="全部"):
        self.send_find_matches = []
        self.send_find_index = -1
        try:
            if query:
                for row in self._send_find_rows(scope):
                    command_edit = self.command_table.cellWidget(row, 1)
                    if not command_edit:
                        continue
                    for start, end in find_matches(command_edit.text(), query, options):
                        self.send_find_matches.append((row, start, end))
        except ValueError as exc:
            self.send_find_bar.set_result(0, 0, str(exc))
            return

        if self.send_find_matches:
            self.send_find_index = 0
        self._render_send_find()

    def _render_send_find(self):
        total = len(self.send_find_matches)
        current = self.send_find_index + 1 if total else 0
        self.send_find_bar.set_result(current, total)
        if not total:
            self._clear_send_find_highlight()
            return

        row, start, end = self.send_find_matches[self.send_find_index]
        if self.send_find_highlight_row != row:
            self._clear_send_find_highlight()
        command_edit = self.command_table.cellWidget(row, 1)
        if command_edit:
            self.command_table.scrollTo(
                self.command_table.model().index(row, 1),
                QTableWidget.PositionAtCenter,
            )
            self.send_find_highlight_row = row
            self.send_find_bar.search_input.setFocus(Qt.OtherFocusReason)
            QTimer.singleShot(
                0,
                lambda r=row, s=start, e=end: self._apply_send_find_highlight(r, s, e),
            )

    def _apply_send_find_highlight(self, row, start, end):
        if not self.send_find_matches or self.send_find_index < 0:
            return
        if self.send_find_matches[self.send_find_index] != (row, start, end):
            return
        command_edit = self.command_table.cellWidget(row, 1)
        if not command_edit:
            return
        palette = command_edit.palette()
        for color_group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            palette.setColor(color_group, QPalette.Highlight, QColor("#FFEB3B"))
            palette.setColor(color_group, QPalette.HighlightedText, QColor("#000000"))
        command_edit.setPalette(palette)
        command_edit.setSelection(start, end - start)

    def _navigate_send_find(self, direction):
        if not self.send_find_matches:
            return
        self.send_find_index = (
            self.send_find_index + direction
        ) % len(self.send_find_matches)
        self._render_send_find()

    def _clear_send_find(self):
        self._clear_send_find_highlight()
        self.send_find_matches = []
        self.send_find_index = -1

    def _clear_send_find_highlight(self):
        row = self.send_find_highlight_row
        if row is not None and 0 <= row < self.command_table.rowCount():
            command_edit = self.command_table.cellWidget(row, 1)
            if command_edit:
                command_edit.deselect()
        self.send_find_highlight_row = None

    def _on_command_table_changed(self, row_index):
        if self.send_find_bar.isVisible():
            self.send_find_bar._emit_search()
        if 0 <= row_index < self.command_table.rowCount():
            _enable, command, _comment = self.command_table.get_row_data(row_index)
            self.command_table.set_send_button_replaceable(
                row_index, self._is_replaceable_command(command, row_index)
            )
        else:
            self.update_replacement_mode_ui()

    def toggle_replacement_mode(self):
        self.replace_send_check.toggle()

    def edit_global_replacement_rule(self):
        dialog = ReplacementRuleDialog(
            self.global_replacement_rule,
            title="发送编辑区全局替换规则",
            parent=self,
        )
        if dialog.exec_() == QDialog.Accepted:
            self.global_replacement_rule = (
                None if dialog.clear_requested else dialog.get_rule()
            )
            self.update_replacement_mode_ui()

    def _effective_replacement_rule(self, row_index):
        if row_index is not None:
            row_rule = self.command_table.get_replacement_rule(row_index)
            if row_rule:
                return row_rule
        return self.global_replacement_rule

    def _is_replaceable_command(self, command, row_index):
        if not self.replace_send_check.isChecked():
            return False
        cmd_type_str, _param = UIUtils.parse_special_command(command)
        if cmd_type_str and any(ct.value == cmd_type_str for ct in SpecialCommandType):
            return False
        try:
            return can_replace(
                UIUtils.unescape_text(command),
                self._effective_replacement_rule(row_index),
            )
        except ValueError:
            return False

    def update_replacement_mode_ui(self):
        if not hasattr(self, "command_table"):
            return
        for row in range(self.command_table.rowCount()):
            _enable, command, _comment = self.command_table.get_row_data(row)
            self.command_table.set_send_button_replaceable(
                row, self._is_replaceable_command(command, row)
            )
        self._update_continuous_button_style()

    def _update_continuous_button_style(self):
        if not hasattr(self, "continuous_btn"):
            return
        if self.is_continuous_sending:
            color = Colors.RED_BUTTON
        elif self.replace_send_check.isChecked():
            color = Colors.PURPLE_BUTTON
        else:
            color = Colors.GREEN_BUTTON
        self._apply_button_color(self.continuous_btn, color)

    def _replace_outgoing_command(self, command, row_index):
        if not self.replace_send_check.isChecked():
            return command
        try:
            replaced, _matched = replace_text(
                command, self._effective_replacement_rule(row_index)
            )
            return replaced
        except ValueError as exc:
            self.output_manager.append_text(f"错误: 替换规则无效: {exc}", OutputSource.ERROR)
            return command

    def send_command(self, command, row_index=None):
        """发送命令"""
        if not self.can_send_serial_data():
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            if self.is_continuous_sending:
                self.stop_continuous_sending()
            return False

        try:
            command = self._replace_outgoing_command(command, row_index)
            # 添加结尾标识符
            ending = self.get_ending_chars()
            full_command = command.encode('utf-8') + ending

            # 发送数据
            sent_bytes = self._write_serial_data(full_command)
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
        if not self.can_send_serial_data():
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            return False
            
        sent_bytes = self._write_serial_data(data)
        if sent_bytes > 0:
            self.send_count += sent_bytes
            self.update_statistics()
            self.output_manager.reset_receive_timestamp()
            if self.show_send_check.isChecked():
                self.output_manager.append_text(f"[HEX]: {data.hex(' ').upper()}", OutputSource.SEND)
            return True
        return False

    def update_baudrate(self, baudrate, notify_remote=True):
        """更新波特率 (供特殊指令使用) """
        self.baud_combo.setCurrentText(str(baudrate))
        self.save_state()
        if self.remote_mode == "client" and notify_remote:
            if self.remote_thread and self.remote_client_connected:
                self.remote_thread.send_baudrate(baudrate)
                self.output_manager.append_text(f"已请求远程主控端调整波特率至: {baudrate}", OutputSource.SYSTEM)
            else:
                self.output_manager.append_text("调整波特率失败: 远程串口未连接", OutputSource.ERROR)
            return
        if self.is_connected and self.serial_thread:
            # 动态调整波特率，无需关闭串口
            self.output_manager.append_text(f"正在调整波特率至: {baudrate}", OutputSource.SYSTEM)
            if self.serial_thread.set_baudrate(baudrate):
                self.output_manager.append_text(output_rules.baudrate_updated(baudrate), OutputSource.SYSTEM)
                self.send_remote_serial_config()
            else:
                self.output_manager.append_text(f"调整波特率失败", OutputSource.ERROR)

    def update_com_port(self, port):
        """更新 COM 口 (供特殊指令和 CLI 配置复用) """
        port_id = port.strip().split(" ")[0]
        if not port_id:
            self.output_manager.append_text("错误: COM口不能为空", OutputSource.ERROR)
            return False

        matched = False
        for index in range(self.port_combo.count()):
            if self.port_combo.itemText(index).split(" ")[0] == port_id:
                self.port_combo.setCurrentIndex(index)
                matched = True
                break
        if not matched:
            self._add_port_combo_item(port_id)
            self.port_combo.setCurrentText(port_id)
        self._sync_port_combo_display()

        self.save_state()
        self.output_manager.append_text(output_rules.comport_updated(port_id), OutputSource.SYSTEM)
        return True

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
            self.output_manager.append_text(output_rules.ending_set(target), OutputSource.SYSTEM)
            return

        for i in range(self.ending_combo.count()):
            item_text = self.ending_combo.itemText(i)
            if item_text == target or item_text.replace(r"\r", "\r").replace(r"\n", "\n") == target:
                self.ending_combo.setCurrentIndex(i)
                self.output_manager.append_text(output_rules.ending_set(item_text), OutputSource.SYSTEM)
                return

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
        if not self.can_send_serial_data():
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            return

        # 获取当前选中的模块名称
        selected_module = self.send_module_combo.currentText()

        self.is_continuous_sending = True
        self.continuous_btn.setText("停止发送")
        self._update_continuous_button_style()

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
        self._update_continuous_button_style()
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
                        self.output_manager.append_text(output_rules.continuous_delay(delay_ms), OutputSource.SYSTEM)
                        QTimer.singleShot(int(delay_ms), lambda: send_next_command(index + 1))
                        return
                    except ValueError:
                        self.output_manager.append_text(output_rules.invalid_delay(param), OutputSource.ERROR)
                elif command_type == SpecialCommandType.SENDHEX:
                    # 执行 SendHex 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    else:
                        # 执行失败时记录错误并继续下一条，不中断连续发送
                        self.output_manager.append_text(
                            output_rules.special_command_failed("SendHex", param),
                            OutputSource.ERROR,
                        )
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type == SpecialCommandType.BAUDRATE:
                    # 执行 BaudRate 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        # 波特率切换可能导致串口重启，等待 500ms 确保稳定
                        QTimer.singleShot(500, lambda: send_next_command(index + 1))
                    else:
                        self.output_manager.append_text(
                            output_rules.special_command_failed("BaudRate", param),
                            OutputSource.ERROR,
                        )
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SETENDLOG:
                    # 执行 SetEndlog 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        QTimer.singleShot(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    else:
                        self.output_manager.append_text(
                            output_rules.special_command_failed("SetEndlog", param),
                            OutputSource.ERROR,
                        )
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

        if hasattr(self, "send_find_bar"):
            self.send_find_bar.set_scopes(self.modules.keys())

        if not silent:
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
        self.output_manager.append_text("接收数据已清空", OutputSource.SYSTEM)

    def update_statistics(self):
        """更新统计信息"""
        self.send_count_label.setText(f"发送: {self.send_count} 字节")
        self.receive_count_label.setText(f"接收: {self.receive_count} 字节")

    def reset_statistics(self):
        """复位统计"""
        self.send_count = 0
        self.receive_count = 0
        self.update_statistics()
        self.output_manager.append_text("统计数据已复位", OutputSource.SYSTEM)

    def _create_progress_dialog(self, title, maximum):
        """创建可取消的进度弹窗"""
        progress = QProgressDialog(title, "取消", 0, maximum, self)
        if hasattr(progress, "setWindowTitle"):
            progress.setWindowTitle(self.windowTitle() or "串口调试助手")
        progress.setWindowModality(Qt.WindowModal)
        if hasattr(progress, "setWindowFlags") and hasattr(progress, "windowFlags"):
            progress.setWindowFlags(progress.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setValue(0)
        return progress

    def _count_importable_rows(self, filename):
        """统计可导入的数据行数"""
        count = 0
        with open(filename, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            for csv_row in reader:
                if not csv_row or len(csv_row) == 0:
                    continue
                if csv_row[0].strip().startswith('//') or csv_row[0].strip().startswith('#'):
                    continue
                if len(csv_row) < 2:
                    continue
                enable_str = csv_row[0].strip().lower()
                if enable_str not in ['true', 'false']:
                    continue
                count += 1
        return count

    def _should_process_progress(self, value, maximum):
        """控制长任务进度刷新频率，避免大文件逐行刷新阻塞界面。"""
        if maximum <= 100:
            return True
        return value == maximum or value % 100 == 0

    def _snapshot_template_state(self):
        """保存导入前模板状态，用于取消时回滚"""
        return {
            "commands": self.command_table.get_all_commands(),
            "replacement_rules": self.command_table.get_replacement_rules(),
            "jump_module": self.jump_module_combo.currentText(),
            "send_module": self.send_module_combo.currentText(),
        }

    def _restore_template_state(self, snapshot):
        """恢复模板状态"""
        self.command_table.clear_all()
        for row, (enable, command, comment) in enumerate(snapshot["commands"]):
            self.command_table.add_command_row(enable, command, comment, row)
        self.command_table.load_replacement_rules(snapshot.get("replacement_rules", {}))
        self.refresh_modules(silent=True)

        jump_module = snapshot.get("jump_module")
        send_module = snapshot.get("send_module")
        if jump_module and self.jump_module_combo.findText(jump_module) >= 0:
            self.jump_module_combo.setCurrentText(jump_module)
        if send_module and self.send_module_combo.findText(send_module) >= 0:
            self.send_module_combo.setCurrentText(send_module)

    def _set_long_task_ui_busy(self, busy):
        """长任务期间禁用主界面交互，避免可重入操作"""
        central_widget = self.centralWidget()
        if central_widget:
            central_widget.setEnabled(not busy)

    def _apply_button_color(self, button, color, extra_styles=""):
        """应用按钮颜色样式"""
        extra = f"\n                {extra_styles}" if extra_styles else ""
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                color: white;{extra}
            }}
        """)

    def _bind_momentary_button_feedback(self, button, normal_color, pressed_color=Colors.RED_BUTTON, extra_styles=""):
        """为瞬时按钮绑定按下变色、释放恢复"""
        self._apply_button_color(button, normal_color, extra_styles)
        button.pressed.connect(lambda b=button, c=pressed_color, s=extra_styles: self._apply_button_color(b, c, s))
        button.released.connect(lambda b=button, c=normal_color, s=extra_styles: self._apply_button_color(b, c, s))

    def _set_action_button_running(self, button, running, normal_color, running_color=Colors.RED_BUTTON, extra_styles=""):
        """设置执行型按钮运行态颜色"""
        target_color = running_color if running else normal_color
        self._apply_button_color(button, target_color, extra_styles)

    def _safe_remove_file(self, file_path):
        """安全删除文件，不让清理异常覆盖主异常"""
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

    def _process_progress(self, progress, value):
        """更新进度并处理取消事件"""
        progress.setValue(value)
        QApplication.processEvents()
        return progress.wasCanceled()

    def import_template(self):
        """导入模板"""
        self._set_action_button_running(self.import_btn, True, Colors.GREEN_BUTTON)

        filename = ""
        parsed_commands = []
        try:
            while True:
                last_dir = self.config_manager.get_last_used_directory()
                filename, _ = QFileDialog.getOpenFileName(
                    self, "导入模板", last_dir, "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")

                if not filename:
                    return

                self.config_manager.set_last_used_directory(os.path.dirname(filename))

                with open(filename, 'r', encoding='utf-8-sig', newline='') as f:
                    file_text = f.read()

                try:
                    parsed_commands = CommandRowsTextParser.parse_template_rows(file_text)
                    break
                except ValueError as parse_error:
                    user_choice = self._show_template_format_error_dialog(filename, parse_error)
                    if user_choice == "reselect":
                        continue
                    if user_choice != "plain_text":
                        return

                    try:
                        parsed_commands = CommandRowsTextParser.parse_plain_text_rows(file_text)
                    except ValueError as plain_text_error:
                        QMessageBox.warning(self, "导入失败", str(plain_text_error))
                        return
                    break

            total_rows = len(parsed_commands)
            progress_maximum = max(total_rows, 1)
            progress = self._create_progress_dialog("正在导入模板...", progress_maximum)
            snapshot = self._snapshot_template_state()
            self._set_long_task_ui_busy(True)

            parsed_modules, parsed_module_names, parsed_messages = self._build_template_import_data(parsed_commands)

            # 解析完成后再批量刷新 UI，避免边读文件边频繁创建控件和重绘。
            self.command_table.setUpdatesEnabled(False)
            self.command_table.clear_all()
            self.modules.clear()
            self.modules.update(parsed_modules)
            self.jump_module_combo.clear()
            self.send_module_combo.clear()
            self.jump_module_combo.addItem("全部")
            self.send_module_combo.addItem("全部")
            for module_name in parsed_module_names:
                self.jump_module_combo.addItem(module_name)
                self.send_module_combo.addItem(module_name)

            for row, (enable, command, comment) in enumerate(parsed_commands):
                self.command_table.add_command_row(enable, command, comment, row)
                if self._should_process_progress(row + 1, total_rows) and self._process_progress(progress, row + 1):
                    self._restore_template_state(snapshot)
                    self.output_manager.append_text("导入已取消", OutputSource.SYSTEM)
                    return

            for message in parsed_messages:
                self.output_manager.append_text(message, OutputSource.SYSTEM)

            progress.setValue(progress_maximum)
            self.output_manager.append_text(f"模板已导入: {filename}", OutputSource.SYSTEM)

        except Exception as e:
            self.output_manager.append_text(f"错误: 导入模板失败: {str(e)}", OutputSource.ERROR)
        finally:
            self.command_table.setUpdatesEnabled(True)
            self.command_table.viewport().update()
            self._set_long_task_ui_busy(False)
            self._set_action_button_running(self.import_btn, False, Colors.GREEN_BUTTON)

    def _show_template_format_error_dialog(self, filename, error):
        """模板格式不匹配时，让用户选择重新选文件或按纯文本导入。"""
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Warning)
        message_box.setWindowTitle("模板格式不正确")
        message_box.setText(f"文件不是有效的模板格式:\n{os.path.basename(filename)}")
        message_box.setInformativeText(
            f"{error}\n\n正确格式:\n"
            "True,发送字符串,注释\n"
            "False,发送字符串,注释\n\n"
            "是否重新选择文件，还是依然按纯文本导入？"
        )
        reselect_button = message_box.addButton("重新选择文件", QMessageBox.AcceptRole)
        plain_text_button = message_box.addButton("依然导入", QMessageBox.DestructiveRole)
        message_box.setDefaultButton(reselect_button)
        message_box.exec_()

        clicked_button = message_box.clickedButton()
        if clicked_button == plain_text_button:
            return "plain_text"
        if clicked_button == reselect_button:
            return "reselect"
        return "cancel"

    def _build_template_import_data(self, parsed_commands):
        """根据命令行内容构建模块信息，供模板导入和纯文本兜底导入复用。"""
        current_module = "默认"
        parsed_modules = OrderedDict([(current_module, [])])
        parsed_module_names = []
        parsed_messages = []

        for row, (enable, command, comment) in enumerate(parsed_commands):
            cmd_type_str, param = UIUtils.parse_special_command(command)
            if cmd_type_str == 'mode':
                current_module = param.strip()
                parsed_modules[current_module] = []
                parsed_module_names.append(current_module)
                parsed_messages.append(f"已创建模块: '{current_module}'")
            elif cmd_type_str == 'modeend':
                if enable:
                    parsed_modules[current_module].append(row)
                if current_module != "默认":
                    current_module = "默认"
                    if current_module not in parsed_modules:
                        parsed_modules[current_module] = []
                    parsed_messages.append("模块定义已结束，切换回默认模块")

            if cmd_type_str not in ('mode', 'modeend') and not (enable is False and command == "" and comment):
                parsed_modules[current_module].append(row)

        return parsed_modules, parsed_module_names, parsed_messages

    def export_template(self):
        """导出模板"""
        self._set_action_button_running(self.export_btn, True, Colors.GREEN_BUTTON)

        last_dir = self.config_manager.get_last_used_directory()
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出模板", last_dir, "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")

        if not filename:
            self._set_action_button_running(self.export_btn, False, Colors.GREEN_BUTTON)
            return

        self.config_manager.set_last_used_directory(os.path.dirname(filename))

        temp_path = None
        try:
            commands = self.command_table.get_all_commands()
            progress = self._create_progress_dialog("正在导出模板...", len(commands))
            self._set_long_task_ui_busy(True)

            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', newline='', delete=False,
                dir=os.path.dirname(filename) or None,
                suffix='.tmp'
            ) as temp_file:
                temp_path = temp_file.name
                writer = csv.writer(temp_file, quoting=csv.QUOTE_MINIMAL)
                writer.writerow(["// 选择框,串口需要发送的数据,注释"])

                for index, (enable, command, comment) in enumerate(commands, start=1):
                    # CSV writer会自动处理引号和特殊字符，无需手动转义
                    # 只对不可见字符进行转义，保持可读性
                    escaped_command = UIUtils.escape_text(command)
                    escaped_comment = UIUtils.escape_text(comment)
                    writer.writerow([str(enable), escaped_command, escaped_comment])

                    if self._should_process_progress(index, len(commands)) and self._process_progress(progress, index):
                        temp_file.close()
                        self._safe_remove_file(temp_path)
                        self.output_manager.append_text("导出已取消", OutputSource.SYSTEM)
                        return

            os.replace(temp_path, filename)
            progress.setValue(len(commands))
            self.output_manager.append_text(f"模板已导出: {filename}", OutputSource.SYSTEM)

        except Exception as e:
            self._safe_remove_file(temp_path)
            self.output_manager.append_text(f"错误: 导出模板失败: {str(e)}", OutputSource.ERROR)
        finally:
            self._set_long_task_ui_busy(False)
            self._set_action_button_running(self.export_btn, False, Colors.GREEN_BUTTON)

    def _jump_to_module_row(self):
        """跳转到所选模块的第一个命令行"""
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
        self.command_table.setCurrentCell(first_command_row, 0)
        self.command_table.scrollTo(self.command_table.model().index(first_command_row, 0))

        # 选中该行
        self.command_table.selectRow(first_command_row)
        self.output_manager.append_text(f"已跳转到模块 '{selected_module_name}' 的第一个命令。", OutputSource.SYSTEM)

    def _jump_to_row(self):
        """跳转到指定行号"""
        text = self.jump_row_input.text().strip()
        if not text:
            self.output_manager.append_text("请输入行号。", OutputSource.ERROR)
            return

        try:
            target_row = int(text) - 1  # 转换为 0-based
        except ValueError:
            self.output_manager.append_text("行号必须是整数。", OutputSource.ERROR)
            return

        total_rows = self.command_table.rowCount()
        if target_row < 0 or target_row >= total_rows:
            self.output_manager.append_text(f"行号超出范围，当前共有 {total_rows} 行。", OutputSource.ERROR)
            return

        self.command_table.setCurrentCell(target_row, 0)
        self.command_table.scrollTo(self.command_table.model().index(target_row, 0))
        self.command_table.selectRow(target_row)
        self.output_manager.append_text(f"已跳转到第 {target_row + 1} 行。", OutputSource.SYSTEM)

    def save_state(self):
        """保存当前状态到配置文件"""
        try:
            state = OrderedDict([
                ("basic_settings", {
                    "port": self.get_selected_port_id(),
                    "port_node": self.get_selected_port_node(),
                    "baudrate": self.baud_combo.currentText(),
                    "databits": self.data_bits_combo.currentText(),
                    "parity": self.parity_combo.currentText(),
                    "stopbits": self.stop_bits_combo.currentText()
                }),
                ("remote_settings", {
                    "mode": self.remote_mode_combo.currentText(),
                    "host": self.remote_host_input.currentText(),
                    "port": self.remote_port_spin.value(),
                    "token": self.remote_token_input.text()
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
                    "replace_send": self.replace_send_check.isChecked(),
                    "global_replacement_rule": self.global_replacement_rule,
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
                    "left_group_expanded": {
                        "basic": self.basic_group.isExpanded(),
                        "remote": self.remote_group.isExpanded(),
                        "receive": self.receive_group.isExpanded(),
                        "send": self.send_group.isExpanded(),
                        "other": self.other_group.isExpanded(),
                        "template": self.template_group.isExpanded(),
                        "tools": self.tools_group.isExpanded()
                    }
                }),
                ("commands", self.command_table.get_all_commands()),
                ("command_replacement_rules", self.command_table.get_replacement_rules())
            ])
            self.config_manager.set("state", state)
        except Exception as e:
            print(f"保存状态失败: {e}")

    def load_state(self):
        """从配置文件加载状态"""
        try:
            state = self.config_manager.get("state") or self.config_manager.get("last_state")
            if not state:
                # 兼容旧版本配置 (如果存在)
                self._load_legacy_state()
                return

            # 1. 基本设置
            basic_settings = state.get("basic_settings") or state.get("serial_settings", {})
            port = basic_settings.get("port")
            port_node = basic_settings.get("port_node") or port
            if port_node and self.port_combo.findText(port_node) >= 0:
                self.port_combo.setCurrentText(port_node)
            elif port:
                for index in range(self.port_combo.count()):
                    if self.port_combo.itemText(index).split(" ")[0] == port:
                        self.port_combo.setCurrentIndex(index)
                        break
            self.baud_combo.setCurrentText(basic_settings.get("baudrate", "115200"))
            self.data_bits_combo.setCurrentText(basic_settings.get("databits", "8"))
            self.parity_combo.setCurrentText(basic_settings.get("parity", "None"))
            self.stop_bits_combo.setCurrentText(basic_settings.get("stopbits", "1"))

            # 2. 远程控制设置
            remote_settings = state.get("remote_settings", {})
            self.remote_mode_combo.setCurrentText(remote_settings.get("mode", "主控端"))
            self.update_remote_host_options()
            self.remote_host_input.setCurrentText(remote_settings.get("host", "127.0.0.1"))
            self.remote_port_spin.setValue(remote_settings.get("port", 8765))
            self.remote_token_input.setText(remote_settings.get("token", ""))

            # 3. 接收设置
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

            # 4. 发送设置
            send_settings = state.get("send_settings", {})
            self.loop_send_check.setChecked(send_settings.get("loop_send", False))
            self.loop_interval_spin.setValue(send_settings.get("loop_interval", 1000))
            self.interval_spin.setValue(send_settings.get("continuous_interval", 100))
            self.show_send_check.setChecked(send_settings.get("show_send", False))
            self.send_color_combo.setCurrentText(send_settings.get("send_color", "红色"))
            self.ending_combo.setCurrentText(send_settings.get("ending", r"\r\n"))
            self.replace_send_check.setChecked(send_settings.get("replace_send", False))
            self.global_replacement_rule = normalize_rule(
                send_settings.get("global_replacement_rule")
            )
            
            # 5. 其他设置
            other_settings = state.get("other_settings", {})
            self.timestamp_check.setChecked(other_settings.get("show_timestamp", False))

            # 6. UI 设置
            ui_settings = state.get("ui_settings") or state.get("ui_state", {})
            h_sizes = ui_settings.get("h_splitter_sizes")
            if h_sizes:
                self._set_h_splitter_sizes(h_sizes)
            
            # 7. 工具设置
            tool_settings = state.get("tool_settings") or state.get("ui_state", {})
            default_left_expanded = {
                "basic": True,
                "remote": False,
                "receive": True,
                "send": True,
                "other": True,
                "template": True,
                "tools": tool_settings.get("tools_group_expanded", False)
            }
            left_group_expanded = default_left_expanded.copy()
            left_group_expanded.update(tool_settings.get("left_group_expanded", {}))
            self.basic_group.setExpanded(left_group_expanded.get("basic", True))
            self.remote_group.setExpanded(left_group_expanded.get("remote", False))
            self.receive_group.setExpanded(left_group_expanded.get("receive", True))
            self.send_group.setExpanded(left_group_expanded.get("send", True))
            self.other_group.setExpanded(left_group_expanded.get("other", True))
            self.template_group.setExpanded(left_group_expanded.get("template", True))
            self.tools_group.setExpanded(left_group_expanded.get("tools", False))

            # 加载命令
            saved_commands = state.get("commands")
            if saved_commands:
                self.command_table.blockSignals(True)
                self.command_table.setUpdatesEnabled(False)
                try:
                    self.command_table.clear_all()
                    for row, cmd_data in enumerate(saved_commands):
                        self.command_table.add_command_row(cmd_data[0], cmd_data[1], cmd_data[2], row)
                finally:
                    self.command_table.setUpdatesEnabled(True)
                    self.command_table.blockSignals(False)
                self.command_table.load_replacement_rules(
                    state.get("command_replacement_rules", {}),
                    notify=False,
                )
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
            self.update_replacement_mode_ui()
        except Exception as e:
            print(f"加载状态失败: {e}")
            if self.command_table.rowCount() == 0:
                self.add_initial_commands(10)

    def _sync_mode_forward_selection(self):
        """同步跳转模式到发送模式"""
        current_jump_mode = self.jump_module_combo.currentText()
        if current_jump_mode:
            index = self.send_module_combo.findText(current_jump_mode)
            if index >= 0:
                self.send_module_combo.setCurrentIndex(index)

    def _sync_mode_selection(self):
        """同步发送模式到跳转模式"""
        current_send_mode = self.send_module_combo.currentText()
        if current_send_mode:
            index = self.jump_module_combo.findText(current_send_mode)
            if index >= 0:
                self.jump_module_combo.setCurrentIndex(index)

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
            elif port:
                for index in range(self.port_combo.count()):
                    if self.port_combo.itemText(index).split(" ")[0] == port:
                        self.port_combo.setCurrentIndex(index)
                        break
            self.baud_combo.setCurrentText(self.config_manager.get("baudrate", "115200"))
            self.data_bits_combo.setCurrentText(self.config_manager.get("databits", "8"))
            self.parity_combo.setCurrentText(self.config_manager.get("parity", "None"))
            self.stop_bits_combo.setCurrentText(self.config_manager.get("stopbits", "1"))
            self.remote_mode_combo.setCurrentText("主控端")
            self.update_remote_host_options()
            self.remote_host_input.setCurrentText("127.0.0.1")
            self.remote_port_spin.setValue(8765)
            self.basic_group.setExpanded(True)
            self.remote_group.setExpanded(False)
            self.receive_group.setExpanded(True)
            self.send_group.setExpanded(True)
            self.other_group.setExpanded(True)
            self.template_group.setExpanded(True)
            self.tools_group.setExpanded(False)
            
            self.timestamp_check.setChecked(self.config_manager.get("show_timestamp", False))

            h_sizes = self.config_manager.get("h_splitter_sizes")
            if h_sizes:
                self._set_h_splitter_sizes(h_sizes)

            saved_commands = self.config_manager.get("saved_commands")
            if saved_commands:
                self.command_table.blockSignals(True)
                self.command_table.setUpdatesEnabled(False)
                try:
                    self.command_table.clear_all()
                    for row, cmd_data in enumerate(saved_commands):
                        self.command_table.add_command_row(cmd_data[0], cmd_data[1], cmd_data[2], row)
                finally:
                    self.command_table.setUpdatesEnabled(True)
                    self.command_table.blockSignals(False)
                self.update_replacement_mode_ui()
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
        if self.remote_thread:
            self.stop_remote_control()
        self.save_state()
        event.accept()
