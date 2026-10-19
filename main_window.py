#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
主窗口模块, 负责串口工具 GUI 布局, 用户交互, 状态保存和远程控制协调.

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import sys
import os
import copy
import time
from core.command_templates import template_path
import re
import socket
import weakref
import base64
import codecs
import uuid
from collections import OrderedDict

import serial
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QGroupBox, QLabel, QComboBox, QPushButton, QCheckBox,
                             QTextBrowser, QTableWidget, QTableWidgetItem, QHeaderView,
                             QLineEdit, QSpinBox, QDoubleSpinBox, QScrollArea, QFrame,
                             QMessageBox, QFileDialog, QDialog, QDialogButtonBox, QTextEdit,
                             QSplitter, QSizePolicy, QStyleFactory, QInputDialog,
                             QProgressDialog, QStyle, QShortcut)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer, QEvent, QProcess
from PyQt5.QtGui import QFont, QColor, QTextCursor, QIcon, QKeySequence

from core.command_types import OutputSource, SpecialCommandType
from utils.ui_utils import UIUtils, Colors, resource_path
from core.command_parser import command_parts, unescape_text, normalize_ending_text, get_ending_bytes
from widgets.custom_widgets import CustomTextBrowser, CollapsibleGroupBox, ClickableComboBox
from widgets.command_widgets import CommandTableWidget
from widgets.find_widgets import FindBar
from dialogs.replacement_rule_dialog import ReplacementRuleDialog
from dialogs.save_data_dialog import SaveDataSelectionDialog, SystemLogLevelDialog
from dialogs.help_dialog import HelpDialog
from managers.output_manager import OutputManager
from managers.special_command_manager import SpecialCommandManager
from core.serial_thread import SerialThread
from core.serial_config import validate_baudrate, validate_serial_config, serial_kwargs
from core.remote_control import RemoteControlClient, RemoteControlServer
from core.remote_firmware import RemoteFirmwareHost, RemoteFirmwareClient
from core.gui_cli_bridge import GuiCliBridge
from core import output_rules
from app_identity import get_config_file
from managers.config_manager import ConfigManager
from dialogs.config_dialog import ConfigDialog
from widgets.base_widgets import BaseWidgetMixin
from core.text_search import find_matches, can_replace, replace_text, normalize_rule
from core.text_search import (
    compile_pattern,
    compile_expanded_response_pattern,
    compile_diagnostic_response_pattern,
)
from core.response_validation import ResponseValidationManager
from core.firmware_worker import FirmwareDownloadWorker
from core.template_worker import TemplateWorker
from core.firmware_download import (
    FIRMWARE_TOOL_NAME,
    build_download_config,
    resolve_firmware_path,
    get_firmware_downloader_class,
)


class SerialTool(QMainWindow, BaseWidgetMixin):
    """串口助手主窗口,协调本地串口,远程会话,固件任务及 CLI 桥接.

    控件与业务协调器在 GUI 线程运行,实际串口读取,TCP 收发和固件下载
    由专用工作线程执行.设备入口共享下载占用检查,连续命令通过请求 ID
    等待设备端回执.窗口保存用户配置,但 CLI 桥接消息不经配置文件中转.
    下载未结束时关闭窗口只请求取消,待资源释放后才能正常关闭.
    """
    firmware_log = pyqtSignal(str, str)
    firmware_progress = pyqtSignal(int, int, str)

    LEFT_PORT_COMBO_VISIBLE_CHARS = 7
    LEFT_PANEL_DEFAULT_EXTRA_WIDTH = 60
    SYNC_BUTTON_WIDTH = 30
    STATE_SAVE_DEBOUNCE_MS = 800
    STATE_SAVE_FALLBACK_MS = 30_000
    DEFAULT_GLOBAL_REPLACEMENT_RULE = {
        "find": r"^write\s+(\d+)\s+(?:0x)?([0-9A-Fa-f]+)\s+(?:0x)?0*([0-9A-Fa-f]+)\s*$",
        "replace": "read $1 0x$2 1",
        "options": {
            "case_sensitive": False,
            "use_regex": True,
            "whole_word": False,
        },
        "response_validation": {
            "enabled": False,
            "expression": (
                r"\bread[ \t]+$1[ \t]+0x$2[ \t]+1[ \t]*\r?\n"
                r"[ \t]*(?:0x)?$2[ \t]*=[ \t]*(?:0x)?0*$3[ \t]*\r?\n"
            ),
            "timeout_ms": 100,
            "on_failure": "continue",
            "color_policy": "sticky_failure",
            "show_error": False,
        },
    }

    def __init__(self, tool_version="0.0.0", tool_version_date="N/A", exe_name="main", config_file=None):
        """创建窗口,读取配置并启动本机 CLI 桥接,不自动打开串口或远程连接.

        Args:
            tool_version (str): 应用版本,默认 0.0.0,用于配置版本检查.
            tool_version_date (str): 版本日期,默认 N/A,用于配置和界面元信息.
            exe_name (str): 当前入口名称,默认 main;归一化后决定配置文件名,
                相对配置路径基于进程工作目录,也用于定位 CLI 桥接端点.
            config_file (str | None): 显式配置路径;缺省按入口名生成,
                相对路径与 CLI 一样基于进程工作目录.
        """
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
        self.validation_pass_count = 0
        self.validation_complete_count = 0
        self.response_validation_manager = ResponseValidationManager()
        self.response_validation_timer = QTimer(self)
        self.response_validation_timer.setInterval(20)
        self.response_validation_timer.timeout.connect(
            self._expire_response_validations
        )
        self.continuous_timer = QTimer()
        self.continuous_timer.timeout.connect(self.send_continuous_commands)
        self.is_continuous_sending = False
        self.firmware_download_running = False
        self.firmware_download_cancelled = False
        self.firmware_download_worker = None
        self.firmware_download_process = None
        self._firmware_download_completion = None
        self._firmware_download_restore_serial = False
        self.remote_capabilities = {}
        self._remote_device_pending = {}
        self._remote_sequence_failed = False
        self._command_generation = 0
        self._receive_decoder = codecs.getincrementaldecoder("utf-8")()
        self.remote_firmware_host = RemoteFirmwareHost(
            self._send_host_task_message, self._prepare_host_download,
            self._acquire_download_serial, self._release_download_serial,
            self._firmware_download_log, self)
        self.remote_firmware_client = RemoteFirmwareClient(self._send_remote_message, self)
        self.remote_firmware_host.state_changed.connect(self.send_remote_serial_config)
        self.remote_firmware_client.progress.connect(self._remote_download_progress)
        self.remote_firmware_client.finished.connect(self._complete_firmware_download)
        self._download_progress_dialog = None
        self.gui_cli_bridge = None
        self._last_firmware_result = (False, "下载未启动")
        self.current_module = "全部"
        self.modules = OrderedDict()  # 存储模块信息
        self.global_replacement_rule = normalize_rule(self.DEFAULT_GLOBAL_REPLACEMENT_RULE)
        self.receive_find_matches = []
        self.receive_find_index = -1
        self.send_find_matches = []
        self.send_find_index = -1
        self.send_find_highlight_row = None
        self.last_find_target = "receive"
        self.help_dialog = None
        self.save_source_selection = set(OutputSource)
        self.system_level_selection = {"normal", "warning", "info", "debug"}
        self.save_system_level_selection = {"normal", "warning", "info", "debug"}
        from core.log_settings import settings_from_state
        self.log_settings = settings_from_state({})
        self.log_settings.pop("save_full_log")
        self._send_display_selected = True
        self._loading_state = True
        self._state_dirty = False
        self._template_task = None
        self._closing_logs = False

        self._state_save_timer = QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.setInterval(self.STATE_SAVE_DEBOUNCE_MS)
        self._state_save_timer.timeout.connect(self._flush_pending_state)

        self._state_fallback_timer = QTimer(self)
        self._state_fallback_timer.setInterval(self.STATE_SAVE_FALLBACK_MS)
        self._state_fallback_timer.timeout.connect(self._flush_pending_state)

        # 显式路径与 CLI 共用配置管理器和端点解析,缺省保留原命名规则.
        config_file = config_file or get_config_file(self.exe_name)
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

        try:
            self.init_ui()
            self.refresh_ports()
            # 保留未加载工作文件时的控件默认值,切换缺省字段不继承旧工作内容.
            self._default_work_state = self.snapshot_state()
            self._default_work_state['commands'] = [[False, '', ''] for _ in range(10)]
            self.load_state()
        finally:
            self._loading_state = False
        self._apply_log_settings()
        self._state_fallback_timer.start()
        self.gui_cli_bridge = GuiCliBridge(self, self.config_manager.config_file)
        self.gui_cli_bridge.start()

    def add_initial_commands(self, addlen):
        """通过统一建行入口追加 addlen 条空命令,避免重复绑定发送按钮."""
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
            self.get_send_color,
            self.get_source_filter,
            self.get_system_level_filter,
            capture_filter=self.get_log_capture_filter,
            keep_timestamp=lambda: self.log_settings["keep_timestamp"],
        )
        self.output_manager.archive_failed.connect(self._on_log_archive_failed)
        self.output_manager.archive_idle.connect(self._on_log_archive_idle)
        self.output_manager.archive_changed.connect(self._update_log_status)

        # 连接信号
        self.connect_signals()

        # 更新工具状态
        self.update_tools_state()

    def _set_h_splitter_sizes(self, sizes=None):
        """设置主分割器宽度, 避免左侧设置区初始状态过窄."""
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
        """按当前字体估算控件宽度, 适配不同 DPI/缩放."""
        return widget.fontMetrics().horizontalAdvance("M" * visible_chars) + extra_width

    def _left_panel_min_width(self):
        """按当前字体估算左侧面板最小可读宽度."""
        label_width = self._left_basic_label_width()
        scrollbar_width = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        frame_width = self.style().pixelMetric(QStyle.PM_DefaultFrameWidth)
        margins_and_spacing = 72 + scrollbar_width + frame_width * 4
        return label_width + self._left_control_area_width() + margins_and_spacing

    def _left_panel_default_width(self):
        """返回左侧面板最小宽度加默认扩展量的初始宽度."""
        return self._left_panel_min_width() + self.LEFT_PANEL_DEFAULT_EXTRA_WIDTH

    def _compact_button_width(self, text):
        """按当前字体测量按钮文本宽度并预留水平内边距."""
        return self.fontMetrics().horizontalAdvance(text) + 28

    def _set_compact_button_width(self, button):
        """根据按钮文字固定宽度及尺寸策略,使左侧按钮保持紧凑."""
        button.setFixedWidth(self._compact_button_width(button.text()))
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _left_port_combo_width(self):
        """按预设可见字符数计算端口下拉框的逻辑像素宽度."""
        return self._width_for_chars(self, self.LEFT_PORT_COMBO_VISIBLE_CHARS)

    def _left_control_area_width(self):
        """合计刷新按钮,间距和端口下拉框的宽度."""
        return self._compact_button_width("刷新") + 6 + self._left_port_combo_width()

    def _set_left_control_expanding(self, widget):
        """左侧普通控件只随布局伸缩, 不人为设置最小宽度."""
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _set_port_combo_compact_width(self):
        """端口框只保留短显示宽度, 完整名称通过提示查看."""
        self.port_combo.setMinimumContentsLength(self.LEFT_PORT_COMBO_VISIBLE_CHARS)
        self.port_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.port_combo.setMinimumWidth(self._left_port_combo_width())
        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _left_basic_label_width(self):
        """按预设标签字符数计算左侧基础设置标签宽度."""
        return self.fontMetrics().horizontalAdvance("波特率:") + 8

    def _create_left_basic_label(self, text):
        """创建固定宽度的左侧基础设置标签."""
        label = QLabel(text)
        label.setFixedWidth(self._left_basic_label_width())
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return label

    def _add_port_combo_item(self, display):
        """添加串口下拉项, 并用提示显示完整端口名称."""
        index = self.port_combo.count()
        self.port_combo.addItem(display)
        self.port_combo.setItemData(index, display, Qt.ToolTipRole)

    def _sync_port_combo_display(self, text=None):
        """同步端口提示, 并让窄输入框优先显示左侧字符."""
        self.port_combo.setToolTip(text if text is not None else self.port_combo.currentText())
        line_edit = self.port_combo.lineEdit() if self.port_combo.isEditable() else None
        if line_edit:
            line_edit.deselect()
            line_edit.setCursorPosition(0)

    def update_tools_state(self):
        """根据配置更新所有工具按钮状态(通用方法)"""
        for tool_name, button in self.tool_buttons.items():
            is_enabled = self.config_manager.is_tool_enabled(tool_name)
            is_available = self.config_manager.is_tool_available(tool_name)
            visible = is_enabled and is_available
            button.setVisible(visible)
            button.setEnabled(visible)

            if visible:
                self._apply_button_color(button, Colors.BLUE_BUTTON)
            else:
                button.setStyleSheet("QPushButton { background-color: #cccccc; color: #888888; }")

    def create_left_panel(self):
        """建立可滚动设置面板和工具入口,基础控件随布局伸缩并屏蔽滚轮误改."""
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

        # 左侧面板最小宽度以端口行控制区为基准, 普通控件随布局自动伸缩.
        # 端口选择
        port_layout = QHBoxLayout()
        port_layout.addWidget(self._create_left_basic_label("端口:"))
        self.port_combo = ClickableComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.popupAboutToBeShown.connect(self.refresh_ports)
        self.port_combo.currentTextChanged.connect(self._sync_port_combo_display)
        self.port_combo.installEventFilter(self)  # 禁用滚轮
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
        self.baud_combo.installEventFilter(self)  # 禁用滚轮
        self._set_left_control_expanding(self.baud_combo)
        baud_layout.addWidget(self.baud_combo)
        basic_layout.addLayout(baud_layout)

        # 数据位
        data_bits_layout = QHBoxLayout()
        data_bits_layout.addWidget(self._create_left_basic_label("数据位:"))
        self.data_bits_combo = QComboBox()
        self.data_bits_combo.addItems(["5", "6", "7", "8"])
        self.data_bits_combo.setCurrentText("8")
        self.data_bits_combo.installEventFilter(self)  # 禁用滚轮
        self._set_left_control_expanding(self.data_bits_combo)
        data_bits_layout.addWidget(self.data_bits_combo)
        basic_layout.addLayout(data_bits_layout)

        # 校验位
        parity_layout = QHBoxLayout()
        parity_layout.addWidget(self._create_left_basic_label("校验位:"))
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(["None", "Even", "Odd", "Mark"])
        self.parity_combo.setCurrentText("None")
        self.parity_combo.installEventFilter(self)  # 禁用滚轮
        self._set_left_control_expanding(self.parity_combo)
        parity_layout.addWidget(self.parity_combo)
        basic_layout.addLayout(parity_layout)

        # 停止位
        stop_bits_layout = QHBoxLayout()
        stop_bits_layout.addWidget(self._create_left_basic_label("停止位:"))
        self.stop_bits_combo = QComboBox()
        self.stop_bits_combo.addItems(["1", "1.5", "2"])
        self.stop_bits_combo.setCurrentText("1")
        self.stop_bits_combo.installEventFilter(self)  # 禁用滚轮
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
        # 显示名称说明职责; userData 沿用现有 JSON 配置值,不由文案决定角色.
        self.remote_mode_combo.addItem("设备端（连接硬件）", "主控端")
        self.remote_mode_combo.addItem("控制端（远程操作）", "远程端")
        self.remote_mode_combo.installEventFilter(self)
        self._set_left_control_expanding(self.remote_mode_combo)
        remote_mode_layout.addWidget(self.remote_mode_combo)
        remote_layout.addLayout(remote_mode_layout)

        remote_host_layout = QHBoxLayout()
        self.remote_host_label = QLabel("地址:")
        remote_host_layout.addWidget(self.remote_host_label)
        self.remote_host_input = ClickableComboBox()
        self.remote_host_input.setEditable(True)
        self.remote_host_input.addItem("127.0.0.1")
        self.remote_host_input.popupAboutToBeShown.connect(self.update_remote_host_options)
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
        self.remote_port_spin.installEventFilter(self)  # 禁用滚轮
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

        system_level_layout = QHBoxLayout()
        system_level_layout.addWidget(QLabel("系统级别:"))
        self.system_level_button = QPushButton()
        self.system_level_button.clicked.connect(self._show_system_level_dialog)
        self._update_system_level_button_text()
        system_level_layout.addWidget(self.system_level_button)
        system_level_layout.addStretch()
        receive_layout.addLayout(system_level_layout)

        self.timestamp_check = QCheckBox("显示时间戳")
        self.timestamp_check.setChecked(False)
        receive_layout.addWidget(self.timestamp_check)

        archive_row = QHBoxLayout()
        self.save_full_log_check = QCheckBox("保存完整日志")
        self.save_full_log_check.setToolTip("持续分块保存开启期间收录的信息；右侧选择日志目录，底部“配置”调整日志规则。")
        archive_row.addWidget(self.save_full_log_check)
        archive_row.addStretch()
        self.log_directory_button = QPushButton("日志目录…")
        self.log_directory_button.clicked.connect(self._choose_log_directory)
        archive_row.addWidget(self.log_directory_button)
        receive_layout.addLayout(archive_row)
        self.log_status_label = QLabel()
        self.log_status_label.setTextFormat(Qt.PlainText)
        self.log_status_label.setWordWrap(True)
        self.log_status_label.setVisible(False)
        receive_layout.addWidget(self.log_status_label)
        self.recover_log_button = QPushButton("另存未确认日志…")
        self.recover_log_button.setVisible(False)
        self.recover_log_button.clicked.connect(self._recover_log_archive)
        receive_layout.addWidget(self.recover_log_button)

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
        self.ending_combo.installEventFilter(self)  # 禁用滚轮
        # 发送选项统一使用两列网格:左侧对齐, 右侧随侧栏宽度伸缩.
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
        self.show_send_check.setToolTip("开启时临时启用日志收录和接收区显示中的发送；关闭后恢复各自原选择。")
        send_options_grid.addWidget(self.show_send_check, 1, 0)

        self.send_color_combo = QComboBox()
        self.send_color_combo.addItems(["红色", "蓝色", "绿色", "紫色", "黑色"])
        self.send_color_combo.setCurrentText("红色")
        self._set_left_control_expanding(self.send_color_combo)
        self.send_color_combo.installEventFilter(self)  # 禁用滚轮
        send_options_grid.addWidget(self.send_color_combo, 1, 1)

        self.replace_send_check = QCheckBox("替换发送")
        self.replace_send_check.setToolTip("仅在发送瞬间替换字符串, 快捷键 Ctrl+T")
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

        # 动态创建工具按钮(基于配置管理器的注册表)
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

        # 模块同步按钮 - 将跳转模块同步到发送模块
        self.sync_mode_forward_btn = QPushButton("->")
        self.sync_mode_forward_btn.setFixedWidth(self.SYNC_BUTTON_WIDTH)
        self.sync_mode_forward_btn.setToolTip("将跳转模式同步到发送模式")
        self._bind_momentary_button_feedback(self.sync_mode_forward_btn, Colors.BLUE_BUTTON, extra_styles="padding: 2px;")
        continuous_layout.addWidget(self.sync_mode_forward_btn)

        # 新增的跳转到此按钮
        self.jump_to_module_btn = QPushButton("跳转到此")
        self._bind_momentary_button_feedback(self.jump_to_module_btn, Colors.BLUE_BUTTON)
        continuous_layout.addWidget(self.jump_to_module_btn)

        # 模块同步按钮 - 将发送模块同步到跳转模块
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
        self.validation_count_label = QLabel("校验: 0/0 Err/Pass")
        self.reset_stats_btn = QPushButton("复位")
        self._bind_momentary_button_feedback(self.reset_stats_btn, Colors.BLUE_BUTTON)

        stats_layout.addWidget(self.send_count_label)
        stats_layout.addWidget(self.receive_count_label)
        stats_layout.addWidget(self.validation_count_label)
        stats_layout.addStretch()
        stats_layout.addWidget(self.reset_stats_btn)

        layout.addLayout(stats_layout)

        return panel

    def connect_signals(self):
        """连接控件动作,命令变化与自动保存信号,动态工具按钮按注册名分发."""
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
        self.command_table.commandsChanged.connect(self.schedule_state_save)
        self.command_table.replacementRulesChanged.connect(self.update_replacement_mode_ui)
        self.command_table.replacementRulesChanged.connect(self.schedule_state_save)
        self.receive_browser.textChanged.connect(self._refresh_receive_find_if_visible)
        for source_checkbox in (
            self.show_send_source_check,
            self.show_recv_source_check,
            self.show_sys_source_check,
            self.show_err_source_check,
        ):
            source_checkbox.toggled.connect(self._refresh_output_source_display)
        self.show_sys_source_check.toggled.connect(self.system_level_button.setEnabled)
        self.timestamp_check.toggled.connect(self._refresh_output_source_display)
        self.show_send_source_check.toggled.connect(self._set_send_display_selected)
        self.show_send_check.toggled.connect(self._sync_log_controls)
        self.save_full_log_check.toggled.connect(self._apply_log_settings)

        for combo in (
            self.port_combo,
            self.baud_combo,
            self.data_bits_combo,
            self.parity_combo,
            self.stop_bits_combo,
            self.remote_mode_combo,
            self.remote_host_input,
            self.send_color_combo,
            self.ending_combo,
            self.jump_module_combo,
            self.send_module_combo,
        ):
            combo.currentTextChanged.connect(self.schedule_state_save)

        for checkbox in (
            self.show_send_source_check,
            self.show_recv_source_check,
            self.show_sys_source_check,
            self.show_err_source_check,
            self.loop_send_check,
            self.show_send_check,
            self.replace_send_check,
            self.timestamp_check,
            self.save_full_log_check,
        ):
            checkbox.toggled.connect(self.schedule_state_save)

        for spinbox in (
            self.remote_port_spin,
            self.loop_interval_spin,
            self.interval_spin,
        ):
            spinbox.valueChanged.connect(self.schedule_state_save)

        self.remote_token_input.textChanged.connect(self.schedule_state_save)
        self.h_splitter.splitterMoved.connect(self.schedule_state_save)

        for group in (
            self.basic_group,
            self.remote_group,
            self.receive_group,
            self.send_group,
            self.template_group,
            self.tools_group,
        ):
            group.toggled.connect(self.schedule_state_save)
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

        # 连接工具按钮(动态连接, 基于工具名映射到方法)
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
        """提取当前端口显示文本首个空格前的设备名称."""
        return self.port_combo.currentText().strip().split(" ")[0]

    def get_selected_port_node(self):
        """返回当前端口显示描述,移除末尾重复的括号端口名."""
        text = self.port_combo.currentText().strip()
        port_id = self.get_selected_port_id()
        suffix = f" ({port_id})"
        if port_id and text.endswith(suffix):
            text = text[:-len(suffix)].strip()
        return text

    def refresh_remote_ports(self):
        """控制端请求设备端扫描串口并同步基本设置"""
        if not self.remote_thread or not self.remote_client_connected:
            self.output_manager.append_text("错误: 远程串口未连接", OutputSource.ERROR)
            return
        self.output_manager.append_text("设备端刷新端口", OutputSource.SYSTEM)
        self.request_remote_device("refresh_ports")

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
        """切换模式或展开下拉框时刷新本机地址候选,保留当前输入."""
        current_text = self.remote_host_input.currentText().strip()
        previous = self.remote_host_input.blockSignals(True)
        self.remote_host_input.clear()

        if self.remote_mode_combo.currentIndex() == 0:
            for ip in self.get_local_ipv4_addresses():
                self.remote_host_input.addItem(ip)
            if current_text:
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

        self.remote_host_input.blockSignals(previous)

    def toggle_remote_token_visibility(self, checked):
        """显示或隐藏远程控制密码"""
        self.remote_token_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def toggle_serial_connection(self):
        """打开/关闭串口连接"""
        if self.remote_mode == "client":
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
        """启动设备端监听或控制端连接."""
        if self.serial_operation_busy():
            self.output_manager.append_text("固件任务期间不能切换远程角色", OutputSource.ERROR)
            return
        device_side = self.remote_mode_combo.currentIndex() == 0
        host = self.remote_host_input.currentText().strip()
        port = self.remote_port_spin.value()
        token = self.remote_token_input.text()

        if device_side:
            self.remote_thread = RemoteControlServer("0.0.0.0", port, token)
            self.remote_mode = "server"
            selected_host = host or "本机全部地址"
            self.output_manager.append_text(
                f"设备端将监听端口 {port}。控制端可连接地址: {selected_host}",
                OutputSource.SYSTEM
            )
            if not self.is_connected or not self.serial_thread:
                self.output_manager.append_text("提示: 设备端当前未打开串口, 控制端连接后需等待本机串口恢复", OutputSource.SYSTEM)
        else:
            if not host:
                self.output_manager.append_text("错误: 请输入设备端地址", OutputSource.ERROR)
                return
            if self.is_connected:
                self.output_manager.append_text("错误: 控制端模式下请先关闭本地串口", OutputSource.ERROR)
                return
            self.output_manager.append_text(f"正在连接设备端: {host}:{port}", OutputSource.SYSTEM)
            self.remote_thread = RemoteControlClient(host, port, token)
            self.remote_mode = "client"

        self.remote_thread.status_changed.connect(self.on_remote_status)
        self.remote_thread.error_occurred.connect(self.on_remote_error)
        self.remote_thread.connected_changed.connect(self.on_remote_connected_changed)
        self.remote_thread.message_received.connect(self.on_remote_message)
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
        self._command_generation += 1
        self._receive_decoder.reset()
        self.remote_firmware_client.disconnect()
        self.remote_firmware_host.disconnect()
        self.remote_capabilities = {}
        for task_id in list(self._remote_device_pending):
            self._remote_device_timeout(task_id)
        was_client = self.remote_mode == "client"
        if was_client:
            self._remote_sequence_failed = True
            if self.gui_cli_bridge:
                self.gui_cli_bridge.remote_disconnected()
        if self.remote_thread:
            if not self.remote_thread.stop():
                self.output_manager.append_text("远程线程尚未退出，请稍后再关闭", OutputSource.ERROR)
                return
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

    def serial_operation_busy(self):
        """所有本地及远程设备入口共享的占用门禁;上传也持有租约."""
        return self._template_task is not None or self.firmware_download_running or self.remote_firmware_host.busy

    def _send_remote_message(self, message):
        """向已连接远程会话入队;连接未就绪抛 ConnectionError,不保证设备执行."""
        if not self.remote_thread or not self.remote_client_connected:
            raise ConnectionError("远程连接未就绪")
        self.remote_thread._queue_message(message)

    def _send_host_task_message(self, message, session):
        """仅向任务所属且仍连接的 session 入队结果,防止迟到结果进入新会话."""
        # 断线后排队的旧结果不能投递给新连接.
        if self.remote_thread and self.remote_thread.session == session and self.remote_client_connected:
            self.remote_thread._queue_message(message)

    def _prepare_host_download(self):
        """校验并快照设备端内置下载参数,停止设备端连续发送,不自动恢复该流程.

        Returns:
            tuple[dict, dict]: 设备端串口和协议配置;客户端不能指定设备端文件路径或参数.

        Raises:
            ValueError: 下载占用,工具未启用,使用外部工具或配置无效.
        """
        if self.firmware_download_running:
            raise ValueError("串口已有下载任务")
        if not self.config_manager.is_tool_enabled(FIRMWARE_TOOL_NAME):
            raise ValueError("设备端固件下载未启用")
        if self.config_manager.get_tool_path(FIRMWARE_TOOL_NAME):
            raise ValueError("远程下载要求设备端使用内置下载核心；请在工具配置清空自定义路径")
        downloader = get_firmware_downloader_class()
        config = build_download_config(self.config_manager.get_firmware_downloader_params())
        downloader.validate_config(config)
        port = self._firmware_download_port_config()
        if not port:
            raise ValueError("设备端未选择有效串口")
        # 预留串口前停止设备端自动发送, 后续不自动恢复.
        if self.is_continuous_sending:
            self.stop_continuous_sending()
        return port, config

    def _acquire_download_serial(self):
        """暂停调试读取并等待句柄关闭;仅在释放成功后才允许下载器打开串口."""
        restore = None
        if self.is_connected and self.serial_thread:
            port = self._firmware_download_port_config()
            restore = dict(port=port["port"], baudrate=str(port["baudrate"]),
                           databits=str(port["bytesize"]),
                           parity={"N": "None", "E": "Even", "O": "Odd", "M": "Mark"}[port["parity"]],
                           stopbits=str(port["stopbits"]).removesuffix(".0"))
        if self.serial_thread and not self.close_serial(stop_continuous=False, internal=True):
            raise RuntimeError("调试串口线程未退出；未启动下载")
        return restore

    def _release_download_serial(self, restore):
        """只恢复此前打开的调试串口与参数,不恢复设备端连续发送,不发送设备命令."""
        if restore:
            self.apply_serial_config_to_basic_settings(restore)
            if not self.open_serial(internal=True):
                raise RuntimeError("原调试串口未能重新打开")
        self.send_remote_serial_config()

    def _remote_download_progress(self, current, total, message):
        """把上传字节或下载包进度同步到 CLI,进度框和日志;完成前最多显示 99%."""
        self.firmware_progress.emit(current, total, message)
        if self.gui_cli_bridge:
            self.gui_cli_bridge.progress(current, total, message)
        if self._download_progress_dialog:
            self._download_progress_dialog.setLabelText(message)
            self._download_progress_dialog.setValue(min(99, int(current * 100 / max(total, 1))))
        # 进度另有结构化订阅事件;这里仅保留 GUI 日志,避免控制台重复显示.
        self._firmware_download_log("INFO", f"{message} ({current}/{total})", publish=False)

    def _show_download_progress(self):
        """创建带取消入口的非模态进度框,由实际完成回调关闭."""
        dialog = QProgressDialog("准备固件下载", "取消", 0, 100, self)
        dialog.setWindowTitle("固件下载")
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self.cancel_firmware_download)
        dialog.show()
        self._download_progress_dialog = dialog

    def on_remote_message(self, message):
        """处理能力协商,固件状态和设备操作回执;旧会话的排队信号直接丢弃."""
        if not self.remote_thread or message.get("_session") != self.remote_thread.session:
            return
        kind = message.get("type")
        if kind == "serial_data":
            if self.remote_mode == "client":
                try:
                    self.on_data_received(base64.b64decode(message.get("data", ""), validate=True))
                except (ValueError, TypeError):
                    self.output_manager.append_text("远程串口数据编码无效", OutputSource.ERROR)
            else:
                self.remote_thread.send_error("发送必须使用带请求 ID 的 device_request")
        elif kind == "serial_config" and self.remote_mode == "client":
            if isinstance(message.get("config"), dict):
                self.apply_remote_serial_config(message["config"])
        elif kind == "capabilities" and self.remote_mode == "client":
            self.remote_capabilities = message
        elif kind == "firmware":
            if self.remote_mode == "server":
                self.remote_firmware_host.handle(message)
            elif self.remote_mode == "client":
                self.remote_firmware_client.handle(message)
        elif kind == "device_request" and self.remote_mode == "server":
            self._execute_remote_device(message)
        elif kind == "device_result" and self.remote_mode == "client":
            if isinstance(message.get("id"), str) and message["id"] in self._remote_device_pending:
                pending = self._remote_device_pending.pop(message["id"])
                success = message.get("success") is True
                self._remote_sequence_failed |= not success
                self.output_manager.append_text(message.get("message", ""),
                                                OutputSource.SYSTEM if success else OutputSource.ERROR)
                if pending:
                    pending(success)

    def request_remote_device(self, action, config=None, callback=None):
        """提交带唯一 ID 的设备端设备操作,十秒无回执按未知处理.

        Args:
            action (str): write/baudrate/comport/configure/open/close/refresh_ports.
            config (dict | None): 对应动作参数,默认 None 发送空字典.
            callback (Callable | None): callback(success: bool),收到终态或超时后
                调用;默认 None.同步拒绝时仅返回 False,不调用该回调.

        Returns:
            bool: 是否已排队,不是设备执行结果;真正结果通过 ID 回执关联.
        """
        if not self.is_remote_client_active() or not self.remote_capabilities.get("device_results"):
            self._remote_sequence_failed = True
            self.output_manager.append_text("远程未连接或设备端不支持设备操作回执，请更新两端源码", OutputSource.ERROR)
            return False
        task_id = uuid.uuid4().hex
        if self.serial_operation_busy() and action != "refresh_ports":
            self.output_manager.append_text("固件任务占用串口", OutputSource.ERROR)
            return False
        self._remote_device_pending[task_id] = callback
        self._send_remote_message(dict(type="device_request", id=task_id, action=action, config=config or {}))
        QTimer.singleShot(10000, lambda: self._remote_device_timeout(task_id))
        return True

    def _remote_device_timeout(self, task_id):
        """移除未确认请求并标记命令链失败,按结果未知通知回调,不重发."""
        if task_id in self._remote_device_pending:
            callback = self._remote_device_pending.pop(task_id)
            self._remote_sequence_failed = True
            self.output_manager.append_text("远程操作结果未知：未收到设备端回执；未自动重试", OutputSource.ERROR)
            if callback:
                callback(False)

    def schedule_command_next(self, delay, callback, on_failure=None):
        """等待当前远程请求完成后延时调度下一步,失败时停止当前命令链.

        Args:
            delay (int): 回执通过后等待的毫秒数.
            callback (Callable): 无参数的下一步函数.
            on_failure (Callable | None): 当前代命令失败时的无参数回调,默认 None.

        捕获命令代号;停止或断线使旧代回调失效,失效回调不再调用下一步.
        """
        generation = self._command_generation
        def ready():
            """仅为当前代命令轮询待确认请求,全部通过后安排下一步."""
            if generation != self._command_generation:
                return
            if self._remote_device_pending:
                QTimer.singleShot(20, ready)
            elif self._remote_sequence_failed:
                self.stop_continuous_sending()
                if on_failure:
                    on_failure()
            else:
                QTimer.singleShot(delay, lambda: callback() if generation == self._command_generation else None)
        ready()

    def _execute_remote_device(self, message):
        """执行设备端设备白名单,每个请求帧尝试一次并回传关联结果.

        Args:
            message (dict): 已由网络层关联 _session 的 device_request,包含
                id,action,config.ID 必须为 32 位小写十六进制.

        上传/下载和设备端连续发送期间拒绝冲突操作,不保存已完成请求历史.
        write 只确认串口完整写入,comport 只更新选择,不热切换已打开句柄.
        """
        task_id = message.get("id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9]{32}", task_id):
            return
        success = False
        try:
            action = message.get("action")
            config = message.get("config", {})
            if not isinstance(config, dict):
                raise ValueError("参数必须为对象")
            if self.serial_operation_busy() and action != "refresh_ports":
                raise ValueError("固件任务占用串口，操作已拒绝")
            if self.is_continuous_sending and action != "refresh_ports":
                raise ValueError("设备端正在连续发送，请先在设备端停止")
            if action == "write":
                data = base64.b64decode(config.get("data", ""), validate=True)
                if not data or len(data) > 65536:
                    raise ValueError("发送长度必须为 1–65536 字节")
                success = self._write_serial_data(data) == len(data)
                if success:
                    self.output_manager.reset_receive_timestamp()
                result = "设备端串口已写入" if success else "设备端串口写入失败；设备结果未知，未重发"
            elif action == "baudrate":
                success = bool(self.update_baudrate(validate_baudrate(config.get("value")), notify_remote=False))
                result = "设备端波特率已设置" if success else "设备端波特率设置失败"
            elif action == "comport":
                port = config.get("port")
                if not isinstance(port, str) or not port.strip() or len(port) > 256 or any(c in port for c in "\r\n\0"):
                    raise ValueError("串口名称无效")
                success = bool(self.update_com_port(port))
                result = "设备端 COM 选择已更新（已打开的连接不切换，重新打开后生效）" if success else "COM 选择失败"
            elif action == "configure":
                if self.serial_thread or self.is_connected:
                    raise ValueError("修改完整串口参数前请先关闭设备端串口")
                from core import gui_config
                valid = validate_serial_config(config)
                basic = {name: str(valid[name]) for name in ("port", "baudrate", "databits", "parity", "stopbits")}
                basic["port"] = basic["port"].strip().split(" ")[0]
                gui_config.apply(self, {"state": {"basic_settings": basic}})
                success, result = True, "设备端串口参数已保存，打开串口后生效"
            elif action == "open":
                if self.serial_thread:
                    raise ValueError("设备端串口已打开或正在关闭")
                self.apply_serial_config_to_basic_settings(validate_serial_config(config))
                success = bool(self.open_serial())
                result = "设备端串口已打开" if success else "设备端串口打开失败"
            elif action == "close":
                success = bool(self.close_serial())
                result = "设备端串口已关闭" if success else "设备端串口关闭失败"
            elif action == "refresh_ports":
                self.send_remote_serial_config(include_ports=True)
                success, result = True, "设备端串口列表已刷新"
            else:
                raise ValueError("未实现的远程设备操作")
        except Exception as exc:
            result = str(exc)
        reply = dict(type="device_result", id=task_id, success=success, message=result)
        self._send_remote_message(reply)
        self.send_remote_serial_config()

    def on_remote_status(self, message):
        """远程控制状态消息"""
        self._set_remote_status(f"远程: {message}")
        self.output_manager.append_text(message, OutputSource.SYSTEM)

    def on_remote_error(self, message, fatal=False):
        """远程控制错误消息"""
        message = f"错误: {message}"
        record = self.output_manager.append_text(message, OutputSource.ERROR)
        if self.gui_cli_bridge:
            self.gui_cli_bridge.publish_state(message, error=True, remote=True, timestamp=record.timestamp)
        if fatal and self.remote_mode == "client" and self.remote_thread:
            self.stop_remote_control()

    def on_remote_connected_changed(self, connected, session=None):
        """远程连接状态变更"""
        if session is not None and (not self.remote_thread or self.remote_thread.session != session):
            return
        self.remote_client_connected = connected
        if self.gui_cli_bridge:
            self.gui_cli_bridge.publish_state("远程已连接" if connected else "远程已断开", remote=True,
                                              connected=connected)
        if not connected:
            self._command_generation += 1
            self._receive_decoder.reset()
            self.remote_capabilities = {}
            self.remote_firmware_client.disconnect()
            self.remote_firmware_host.disconnect()
            for task_id in list(self._remote_device_pending):
                self._remote_device_timeout(task_id)
        if self.remote_mode == "client":
            self.connect_btn.setEnabled(connected)
            if connected:
                self._remote_sequence_failed = False
            if not connected:
                self.remote_serial_connected = False
                self.update_connect_button_state(False)
            self.set_basic_group_remote_style(connected)
        elif self.remote_mode == "server" and connected:
            task = self.remote_firmware_host.active
            if task and task["session"] != self.remote_thread.session:
                self.remote_firmware_host.disconnect()
            self._send_remote_message(dict(type="capabilities", firmware_upload=1, device_results=1, serial_config=1))
            self.send_remote_serial_config()
        status = "已连接" if connected else ("监听中" if self.remote_mode == "server" else "未连接")
        self._set_remote_status(f"远程: {status}")


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
            "connected": connected,
            "busy": self.serial_operation_busy(),
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
        """控制端请求设备端打开或关闭本地串口"""
        if not self.remote_thread or not self.remote_client_connected:
            self.output_manager.append_text("错误: 远程串口未连接", OutputSource.ERROR)
            return

        if self.remote_serial_connected:
            self.output_manager.append_text("设备端关闭串口", OutputSource.SYSTEM)
            self.request_remote_device("close")
        else:
            self.output_manager.append_text("设备端打开串口", OutputSource.SYSTEM)
            self.request_remote_device("open", self.get_current_serial_config())

    def send_remote_serial_config(self, include_ports=False):
        """设备端向控制端同步串口参数"""
        if self.remote_mode == "server" and self.remote_thread and self.remote_client_connected:
            self.remote_thread.send_serial_config(self.get_current_serial_config(include_ports=include_ports))

    def apply_remote_serial_config(self, config):
        """控制端显示设备端硬件串口参数"""
        self.apply_remote_port_list(config)
        self.apply_serial_config_to_basic_settings(config)
        if self.remote_mode == "client":
            self.remote_serial_connected = bool(config.get("connected", False))
            self.connect_btn.setEnabled(self.remote_client_connected)
            self.update_connect_button_state(self.remote_serial_connected)
        self.set_basic_group_remote_style(True)

    def apply_remote_port_list(self, config):
        """控制端应用设备端扫描到的端口列表"""
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
        """控制端虚拟基本设置状态:只向设备端发送控制, 不操作本机串口"""
        return self.remote_mode == "client" and self.remote_thread is not None and self.remote_client_connected

    def can_send_serial_data(self):
        """当前后端是否允许发送串口数据"""
        if self.serial_operation_busy():
            return False
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
        """控制端连接时标记基本设置来自设备端硬件"""
        if remote_active:
            self.basic_group.setTitle("基本设置 (设备端信息)")
            self.basic_group.content_widget.setStyleSheet("""
                #content_widget {
                    border: 1px solid #7aa7d9;
                    border-radius: 6px;
                    background-color: #eef6ff;
                }
            """)
            self.basic_group.toggle_button.setToolTip("控制端模式：基本设置显示设备端硬件串口参数")
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
        """更新窗口中的远程连接状态标签."""
        self.remote_status_label.setText(text)

    def _write_serial_data(self, data, callback=None):
        """经当前本地或远程后端提交串口字节.

        本地返回完整写入计数,失败返回 0;远程返回入队长度,最终结果仍须等待
        device_result,callback 仅接收远程实际回执.不得仅根据远程返回值认定设备操作完成.
        """
        if self.serial_operation_busy():
            return 0
        if self.remote_mode == "client":
            if self.remote_thread and self.remote_client_connected:
                queued = self.request_remote_device("write", {"data": base64.b64encode(data).decode("ascii")}, callback)
                if queued and self.gui_cli_bridge:
                    self.gui_cli_bridge.transmit(data)
                return len(data) if queued else 0  # 仅表示排队;命令链等待 device_result.
            return 0
        if self.serial_thread:
            count = self.serial_thread.write_data(data)
            if count == len(data) and self.gui_cli_bridge:
                self.gui_cli_bridge.transmit(data)
            return count
        return 0

    def open_serial(self, internal=False):
        """打开串口"""
        if (self.serial_operation_busy() and not internal) or self.serial_thread:
            self.output_manager.append_text("串口被占用或已打开", OutputSource.ERROR)
            return False
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
            self.serial_thread = SerialThread(**serial_kwargs(self.get_current_serial_config()))
            opening_thread = self.serial_thread
            self.serial_thread.data_received.connect(
                lambda data: self.on_data_received(data) if self.serial_thread is opening_thread else None)
            self.serial_thread.error_occurred.connect(
                lambda message: self.on_serial_error(message) if self.serial_thread is opening_thread else None)
            self.serial_thread.start()

            if not self.serial_thread.open_completed.wait(1.5) or not self.serial_thread.serial or not self.serial_thread.serial.is_open:
                self.close_serial(stop_continuous=False, internal=True)
                return False

            self.is_connected = True
            self.connect_btn.setText("关闭串口")
            self.connect_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.RED_BUTTON};
                    color: white;
                }}
            """)
            self.output_manager.append_text("串口已打开", OutputSource.SYSTEM)
            if self.gui_cli_bridge:
                self.gui_cli_bridge.publish_state("串口已打开", connected=True)
            self.send_remote_serial_config()
            return True

        except Exception as e:
            error_str = str(e)
            if "PermissionError(13, '拒绝访问。'" in error_str or "Access is denied" in error_str:
                self.output_manager.append_text(f"错误: 串口 {port} 正在使用中", OutputSource.ERROR)
            else:
                self.output_manager.append_text(f"错误: 打开串口失败: {error_str}", OutputSource.ERROR)
            
            # 打开失败时也刷新一下串口列表
            self.refresh_ports()

    def close_serial(self, stop_continuous=True, internal=False):
        """关闭串口"""
        if self.serial_operation_busy() and not internal:
            self.output_manager.append_text("固件任务占用串口，关闭操作已拒绝", OutputSource.ERROR)
            return False
        # 如果正在连续发送, 停止它
        if stop_continuous and self.is_continuous_sending:
            self.stop_continuous_sending()

        if self.serial_thread:
            if not self.serial_thread.stop():
                self.output_manager.append_text("串口线程尚未退出，保留占用", OutputSource.ERROR)
                return False
            self.serial_thread = None

        self.is_connected = False
        self.connect_btn.setText("打开串口")
        self._receive_decoder.reset()
        self.connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
            }}
        """)
        self.output_manager.append_text("串口已关闭", OutputSource.SYSTEM)
        if self.gui_cli_bridge:
            self.gui_cli_bridge.publish_state("串口已关闭", connected=False)
        self.send_remote_serial_config()
        return True

    def _firmware_download_port_config(self):
        """读取下载所需的本地串口配置,优先保留当前连接的真实配置."""
        if self.serial_thread:
            return {
                "port": self.serial_thread.port,
                "baudrate": self.serial_thread.baudrate,
                "bytesize": self.serial_thread.bytesize,
                "parity": self.serial_thread.parity,
                "stopbits": self.serial_thread.stopbits,
            }

        port_text = self.port_combo.currentText().strip()
        port = port_text.split(" ")[0] if port_text else ""
        if not port:
            return None
        try:
            return serial_kwargs(self.get_current_serial_config())
        except ValueError:
            return None

    def _firmware_download_log(self, level, message, publish=True):
        """为下载日志添加来源,并将内置/外部下载日志交给控制台订阅.

        Args:
            level (str): 下载器日志级别,错误独立归入 error 来源.
            message (str): 下载器原始说明.
            publish (bool): 是否发布订阅;结构化进度已有单独事件时为 False.
        """
        level_name = str(level or "").upper()
        self.firmware_log.emit(level_name, message)
        text = f"FirmwareDownload: {message}"
        system_level = {
            "WARNING": "warning",
            "INFO": "info",
            "DEBUG": "debug",
        }.get(level_name, "normal")
        if publish and self.gui_cli_bridge:
            self.gui_cli_bridge.publish_state(text, error=level_name == "ERROR",
                                              system_level=system_level, firmware=True)
        if level_name == "ERROR":
            self.output_manager.append_text(text, OutputSource.ERROR)
            return
        self.output_manager.append_text(
            text,
            OutputSource.SYSTEM,
            system_level=system_level,
        )

    def _forward_external_firmware_output(self, raw_text, default_level):
        """解析外部下载器标准输出中的级别前缀,保持与内置模式一致."""
        for raw_line in str(raw_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(
                r"^(NORMAL|SUCCESS|WARNING|INFO|DEBUG|ERROR)\s*:\s*(.*)$",
                line,
            )
            if match:
                level, message = match.groups()
                self._firmware_download_log(level, message)
            else:
                self._firmware_download_log(default_level, line)

    def _complete_firmware_download(self, success, message):
        """在工作线程释放串口后收尾;失败/未知终止命令链,恢复原调试状态不重烧."""
        if not self.firmware_download_running:
            return
        self.firmware_download_worker = None
        self.firmware_download_process = None
        if self.firmware_download_cancelled and success:
            success, message = False, "取消与完成回执交错，结果未知；请核对设备端日志"
        self.firmware_download_cancelled = False
        if self._download_progress_dialog:
            self._download_progress_dialog.close()
            self._download_progress_dialog.deleteLater()
            self._download_progress_dialog = None
        try:
            self._release_download_serial(self._firmware_download_restore_serial)
        except Exception as exc:
            success = False
            message += f"；恢复调试串口失败: {exc}"
        self._firmware_download_restore_serial = False
        self.firmware_download_running = False
        self._last_firmware_result = (success, message)
        self.send_remote_serial_config()
        if not success:
            self._remote_sequence_failed = True
        self._firmware_download_log("SUCCESS" if success else "ERROR", message)
        callback = self._firmware_download_completion
        self._firmware_download_completion = None
        if callback:
            callback(success)

    def _start_builtin_firmware_download(self, firmware_path, port_config, download_config):
        """持有线程引用直至 QThread.finished;不能按业务信号提前恢复串口."""
        worker = FirmwareDownloadWorker(firmware_path, port_config, download_config, self)
        self.firmware_download_worker = worker
        worker.log_signal.connect(self._firmware_download_log)
        worker.progress_signal.connect(self._remote_download_progress)
        worker.finished.connect(lambda: self._complete_firmware_download(*worker.result))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _build_external_firmware_arguments(self, firmware_path, port_config, params):
        """复用 CLI 和 GUI 共用的外部下载器参数生成规则."""
        from core.firmware_download import build_external_firmware_arguments
        return build_external_firmware_arguments(firmware_path, port_config, params)

    def _start_external_firmware_download(self, custom_path, firmware_path, port_config, params):
        """按现有参数启动本机外部下载进程,输出接入窗口日志.

        Args:
            custom_path (str): 本机外部下载器脚本或 EXE 路径.
            firmware_path (str): 本机 BIN 路径.
            port_config (dict): 本机串口参数快照.
            params (dict): 工具配置中的平铺下载与 ACK 字段.

        外部程序退出码不足以证明设备完成,进程结束按未知处理;该路径不用于
        远程设备端固件任务,调用方已先释放调试串口.
        """
        process = QProcess(self)
        self.firmware_download_process = process
        arguments = self._build_external_firmware_arguments(
            firmware_path, port_config, params
        )
        if custom_path.lower().endswith(".py"):
            program = sys.executable
            arguments.insert(0, custom_path)
        else:
            program = custom_path
        process.setProgram(program)
        process.setArguments(arguments)
        process.setWorkingDirectory(os.path.dirname(custom_path) or os.getcwd())
        process.readyReadStandardOutput.connect(
            lambda: self._forward_external_firmware_output(
                bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace"),
                "INFO",
            )
        )
        process.readyReadStandardError.connect(
            lambda: self._forward_external_firmware_output(
                bytes(process.readAllStandardError()).decode("utf-8", errors="replace"),
                "ERROR",
            )
        )
        process.errorOccurred.connect(
            lambda error: self._complete_firmware_download(False, "外部固件下载工具无法启动")
            if error == QProcess.FailedToStart else self._firmware_download_log("ERROR", "外部下载器异常，等待进程释放串口")
        )
        process.finished.connect(
            lambda exit_code, _status: self._complete_firmware_download(
                False,
                f"结果未知：外部下载器退出码 {exit_code}，无法核实其完成确认语义",
            )
        )
        process.start()

    def start_firmware_download_command(self, parameter, completion_callback=None, *,
                                        port_settings=None, download_params=None, show_progress=True):
        """按钮,特殊命令及 CLI 共用的固件下载入口.

        Args:
            parameter (str): 执行 UI 所在电脑的 BIN 路径;为空时使用本机工具默认文件.
            completion_callback (Callable | None): callback(success: bool),启动被拒绝
                或任务收尾后调用,默认 None;结果含恢复调试连接是否成功.
            port_settings (dict | None): 下载窗口本次选择的应用格式串口参数,仅本机使用.
            download_params (dict | None): 下载窗口本次选择的平铺协议参数,仅本机使用.
            show_progress (bool): 是否创建独立进度框;完整下载窗口自行显示进度.

        Returns:
            bool: 是否已启动本次任务;不是下载完成结果.远程路径要求能力协商,
                只上传客户端文件,由设备端使用自己的串口和协议配置.

        已有任务不会被本次拒绝调用清理;失败说明同步保存到 _last_firmware_result.
        """
        started = False
        try:
            if self.serial_operation_busy():
                raise ValueError("已有固件任务占用串口")
            if not self.config_manager.is_tool_enabled(FIRMWARE_TOOL_NAME):
                raise ValueError("固件下载工具未启用")
            params = (self.config_manager.get_firmware_downloader_params()
                      if download_params is None else download_params)
            path, error = resolve_firmware_path(parameter, params)
            if error:
                raise ValueError(error)
            if not path.lower().endswith(".bin") or not 0 < os.path.getsize(path) <= 64 * 1024 * 1024:
                raise ValueError("请选择 1 字节至 64 MiB 的 BIN 文件")
            remote = self.remote_mode == "client"
            if remote:
                if port_settings is not None or download_params is not None:
                    raise ValueError("远程下载使用设备端串口和协议配置")
                if self._remote_device_pending:
                    raise ValueError("请等待上一条设备端操作回执后再下载")
                if not self.is_remote_client_active() or not self.remote_capabilities.get("firmware_upload"):
                    raise ValueError("远程未连接或设备端不支持 BIN 上传；请更新两端源码")
            else:
                port = (self._firmware_download_port_config() if port_settings is None
                        else serial_kwargs(validate_serial_config(port_settings)))
                if not port:
                    raise ValueError("未选择有效串口")
                config = build_download_config(params)
                get_firmware_downloader_class().validate_config(config)
            self.firmware_download_running = True
            started = True
            self.firmware_download_cancelled = False
            self._firmware_download_completion = completion_callback
            self._firmware_download_restore_serial = False
            if show_progress:
                self._show_download_progress()
            if remote:
                self.remote_firmware_client.start(path)
            else:
                self._firmware_download_restore_serial = self._acquire_download_serial()
                custom_path = self.config_manager.get_tool_path(FIRMWARE_TOOL_NAME)
                if custom_path:
                    self._start_external_firmware_download(custom_path, path, port, params)
                else:
                    self._start_builtin_firmware_download(path, port, config)
            return True
        except Exception as exc:
            # 只结算本次创建的任务; 不影响已占用串口的其它任务.
            if started:
                self._complete_firmware_download(False, str(exc))
            else:
                self._firmware_download_log("ERROR", str(exc))
                self._last_firmware_result = (False, str(exc))
                if completion_callback:
                    completion_callback(False)
            return False

    def cancel_firmware_download(self):
        """请求取消当前固件下载,完成回调仍会负责恢复串口."""
        self.remote_firmware_host.cancel()
        self.remote_firmware_client.cancel()
        if not self.firmware_download_running:
            return
        self.firmware_download_cancelled = True
        self._firmware_download_log("INFO", "正在停止下载")
        if self.firmware_download_worker:
            self.firmware_download_worker.cancel()
        if self.firmware_download_process:
            process = self.firmware_download_process
            process.terminate()
            QTimer.singleShot(1500, lambda: process.kill()
                              if self.firmware_download_process is process and process.state() != QProcess.NotRunning else None)

    def on_data_received(self, data):
        """接收数据回调"""
        if self.gui_cli_bridge:
            self.gui_cli_bridge.receive(data)
        if self.remote_mode == "server" and self.remote_thread and self.remote_client_connected:
            self.remote_thread.send_serial_data(data)
        self.receive_count += len(data)
        self.update_statistics()
        try:
            text = self._receive_decoder.decode(data)
            if not text:
                return
            self._process_response_validation_text(text)
            self.output_manager.append_text(text, OutputSource.RECEIVE)
        except UnicodeDecodeError:
            self._receive_decoder.reset()
            self.output_manager.append_text(f"[非UTF-8数据: {data.hex()}]", OutputSource.ERROR)

    def on_serial_error(self, error_msg):
        """显示串口错误,停止连续/循环发送并刷新端口列表."""
        self.output_manager.append_text(f"错误: {error_msg}", OutputSource.ERROR)
        if self.gui_cli_bridge:
            self.gui_cli_bridge.publish_state(error_msg, error=True, connected=False)
        
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
        try:
            if dialog.exec_():
                self.output_manager.append_text("配置已更新", OutputSource.SYSTEM)
                self.update_tools_state()
        finally:
            dialog.deleteLater()

    def choose_work_config(self):
        """选择工作 JSON;与 CLI 共用无设备操作的切换事务."""
        from core import gui_config
        timer_active, loading = self._state_save_timer.isActive(), self._loading_state
        self._state_save_timer.stop()
        self._loading_state = True
        switched = False
        try:
            path, _ = QFileDialog.getOpenFileName(self, "切换工作 JSON", self.config_manager.config_file,
                                                "JSON 配置 (*.json)")
            self._loading_state = loading
            if not path:
                return
            result = gui_config.switch_file(self, path)
            switched = result['switched']
            self.output_manager.append_text("工作 JSON: " + result['working_config_file'], OutputSource.SYSTEM)
        except Exception as exc:
            self.output_manager.append_text("切换工作 JSON 失败: " + str(exc), OutputSource.ERROR)
        finally:
            self._loading_state = loading
            if not switched and timer_active:
                self._state_save_timer.start()

    def open_help_dialog(self):
        """打开帮助窗口, 并重新载入最新的 README 内容."""
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

    def get_system_level_filter(self, level):
        """获取系统来源中指定日志级别的显示状态."""
        return str(level).lower() in self.system_level_selection

    def get_log_capture_filter(self, source, level):
        """日志配置决定收录;发送快捷入口以或逻辑覆盖,不改基础选择."""
        if self.log_settings["retain_all"]:
            return True
        if source == OutputSource.SEND and self.show_send_check.isChecked():
            return True
        return self.log_settings["sources"][source.value] and (
            source != OutputSource.SYSTEM or self.log_settings["system_levels"][level])

    def _set_send_display_selected(self, checked):
        """仅用户/加载基础选择时更新;联动显示更新会阻断此信号."""
        self._send_display_selected = checked

    def _sync_log_controls(self, _checked=None):
        """呈现或逻辑后的状态;不把联动选中值写回基础选择或快照."""
        if self._loading_state:
            return
        self._render_log_controls()
        self._refresh_output_source_display()

    def _render_log_controls(self):
        """临时发送联动只改变有效显示状态,保留用户基础选择."""
        shortcut = self.show_send_check.isChecked()
        checkbox = self.show_send_source_check
        previous = checkbox.blockSignals(True)
        checkbox.setChecked(self._send_display_selected or shortcut)
        checkbox.setEnabled(not shortcut)
        checkbox.setToolTip("由发送设置中的“显示发送字符串”开启；关闭后恢复原选择" if shortcut else "")
        checkbox.blockSignals(previous)

    def _show_system_level_dialog(self):
        """以可持续勾选的对话框编辑系统日志级别."""
        dialog = SystemLogLevelDialog(self.system_level_selection, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.system_level_selection = dialog.selected_levels()
        self._update_system_level_button_text()
        self._refresh_output_source_display()
        self.schedule_state_save()

    def _update_system_level_button_text(self):
        """按当前选择的系统日志级别更新筛选按钮文字."""
        selected_count = len(self.system_level_selection)
        self.system_level_button.setText(f"选择级别 ({selected_count}/4)")

    def _refresh_output_source_display(self, _checked=None):
        """输出来源只影响显示,不删除已记录的数据."""
        if not hasattr(self, "output_manager"):
            return
        self.output_manager.refresh_display()
        self._refresh_receive_find_if_visible()

    def get_ending_chars(self):
        """获取结尾标识符"""
        return get_ending_bytes(self.ending_combo.currentText())

    def open_context_find(self):
        """根据当前焦点打开接收区或发送区的独立查找栏."""
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
        """根据查找条件重建接收区匹配列表,报告无效表达式并刷新结果."""
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
        """更新接收区查找计数,当前匹配高亮及可见位置."""
        selections = []
        for index, (start, end) in enumerate(self.receive_find_matches):
            selection = QTextEdit.ExtraSelection()
            cursor = self.receive_browser.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection.cursor = cursor
            selection.format.setBackground(
                QColor("#FFD54F" if index == self.receive_find_index else "#FFF59D")
            )
            selections.append(selection)
        self.receive_browser.setExtraSelections(selections)

        total = len(self.receive_find_matches)
        current = self.receive_find_index + 1 if total else 0
        self.receive_find_bar.set_result(current, total)
        if total:
            start, _end = self.receive_find_matches[self.receive_find_index]
            cursor = self.receive_browser.textCursor()
            cursor.setPosition(start)
            self.receive_browser.setTextCursor(cursor)
            self.receive_browser.ensureCursorVisible()

    def _navigate_receive_find(self, direction):
        """按 direction 前后循环切换接收区当前匹配并刷新显示."""
        if not self.receive_find_matches:
            return
        self.receive_find_index = (
            self.receive_find_index + direction
        ) % len(self.receive_find_matches)
        self._render_receive_find()

    def _clear_receive_find(self):
        """清空接收区匹配和选中状态,移除查找高亮."""
        self.receive_find_matches = []
        self.receive_find_index = -1
        self.receive_browser.setExtraSelections([])

    def _refresh_receive_find_if_visible(self):
        """仅在接收区查找栏可见时按当前条件重新查找."""
        if self.receive_find_bar.isVisible():
            self.receive_find_bar._emit_search()

    def _send_find_rows(self, scopes):
        """根据模块筛选范围确定发送区查找所覆盖的行号."""
        if not scopes or scopes == "全部":
            return range(self.command_table.rowCount())
        if isinstance(scopes, str):
            scopes = (scopes,)
        rows = set()
        for scope in scopes:
            rows.update(self.modules.get(scope, []))
        return sorted(rows)

    def _search_send_commands(self, query, options, scopes="全部"):
        """按范围搜索命令和注释,刷新匹配数量及高亮,不主动滚动表格."""
        self.send_find_matches = []
        self.send_find_index = -1
        self._clear_send_find_highlight()
        search_area = self.send_find_bar.search_area()
        search_commands = search_area in (
            FindBar.SEARCH_AREA_COMMAND,
            FindBar.SEARCH_AREA_BOTH,
        )
        search_comments = search_area in (
            FindBar.SEARCH_AREA_COMMENT,
            FindBar.SEARCH_AREA_BOTH,
        )
        try:
            if query:
                for row in self._send_find_rows(scopes):
                    command_edit = self.command_table.cellWidget(row, 1)
                    if not command_edit:
                        continue
                    if search_commands:
                        for start, end in find_matches(command_edit.text(), query, options):
                            self.send_find_matches.append((row, "command", start, end))
                    if search_comments:
                        comment = self.command_table.comments.get(row, "")
                        for start, end in find_matches(comment, query, options):
                            self.send_find_matches.append((row, "comment", start, end))
        except ValueError as exc:
            self.send_find_bar.set_result(0, 0, str(exc))
            return

        # 搜索条件或命令内容变化时只刷新匹配数量, 不主动滚动表格.
        # 跳转和高亮仅由回车,Shift+回车或查找栏上下按钮触发.
        self._apply_all_send_find_highlights()
        self.send_find_bar.set_result(0, len(self.send_find_matches))

    def _render_send_find(self):
        """显示发送区当前匹配,将对应行滚动到可见位置并保留查找栏焦点."""
        total = len(self.send_find_matches)
        current = self.send_find_index + 1 if total else 0
        self.send_find_bar.set_result(current, total)
        if not total:
            self._clear_send_find_highlight()
            return

        row, field, start, end = self.send_find_matches[self.send_find_index]
        self._apply_all_send_find_highlights(
            self.send_find_matches[self.send_find_index]
        )
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
                lambda r=row, f=field, s=start, e=end: self._apply_send_find_highlight(
                    r, f, s, e
                ),
            )

    def _apply_send_find_highlight(self, row, field, start, end):
        """在指定行的命令或注释字段中突出 start/end 对应的当前匹配."""
        if not self.send_find_matches or self.send_find_index < 0:
            return
        if self.send_find_matches[self.send_find_index] != (row, field, start, end):
            return
        command_edit = self.command_table.cellWidget(row, 1)
        if not command_edit:
            return
        if field == "comment":
            command_edit.set_comment_search_highlight(start, end)
            return
        command_edit.set_command_search_matches(
            command_edit.command_search_matches or [(start, end)],
            (start, end),
        )

    def _apply_all_send_find_highlights(self, active_match=None):
        """为所有发送区匹配设置高亮,另标记可选的当前匹配."""
        self._clear_send_find_highlight()
        command_matches = {}
        comment_matches = {}
        for row, field, start, end in self.send_find_matches:
            if field == "command":
                command_matches.setdefault(row, []).append((start, end))
            else:
                comment_matches.setdefault(row, []).append((start, end))

        for row, matches in command_matches.items():
            command_edit = self.command_table.cellWidget(row, 1)
            if not command_edit:
                continue
            active_command = None
            if active_match and active_match[0] == row and active_match[1] == "command":
                active_command = (active_match[2], active_match[3])
            command_edit.set_command_search_matches(matches, active_command)

        for row, matches in comment_matches.items():
            command_edit = self.command_table.cellWidget(row, 1)
            if not command_edit:
                continue
            active_comment = None
            if active_match and active_match[0] == row and active_match[1] == "comment":
                active_comment = (active_match[2], active_match[3])
            command_edit.set_comment_search_matches(matches, active_comment)

    def _navigate_send_find(self, direction):
        """按 direction 循环切换发送区匹配并定位对应行."""
        if not self.send_find_matches:
            return
        if self.send_find_index < 0:
            self.send_find_index = 0 if direction > 0 else len(self.send_find_matches) - 1
        else:
            self.send_find_index = (
                self.send_find_index + direction
            ) % len(self.send_find_matches)
        self._render_send_find()

    def _clear_send_find(self):
        """清空发送区匹配和当前索引,移除查找高亮."""
        self._clear_send_find_highlight()
        self.send_find_matches = []
        self.send_find_index = -1

    def _clear_send_find_highlight(self):
        """移除各命令输入框及注释中的发送区查找高亮."""
        for row in range(self.command_table.rowCount()):
            command_edit = self.command_table.cellWidget(row, 1)
            if command_edit:
                command_edit.deselect()
                command_edit.clear_command_search_highlight()
                command_edit.clear_comment_search_highlight()
        self.send_find_highlight_row = None

    def _on_command_table_changed(self, row_index):
        """命令行变化后刷新相关模块,替换指示和查找结果."""
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
        """切换替换发送复选框的启用状态."""
        if not self.replace_send_check.isEnabled():
            return
        self.replace_send_check.toggle()

    def edit_global_replacement_rule(self):
        """编辑全局替换规则,接受后更新显示并安排保存."""
        dialog = ReplacementRuleDialog(
            self.global_replacement_rule,
            title="发送编辑区全局替换规则",
            parent=self,
            default_rule=self.DEFAULT_GLOBAL_REPLACEMENT_RULE,
            default_timeout_ms=self.interval_spin.value(),
        )
        if dialog.exec_() == QDialog.Accepted:
            self.global_replacement_rule = (
                None if dialog.clear_requested else dialog.get_rule()
            )
            self.command_table.clear_validation_states()
            self.update_replacement_mode_ui()
            self.schedule_state_save()

    def _effective_replacement_rule(self, row_index):
        """优先返回指定行的独立替换规则,否则返回全局规则."""
        if row_index is not None:
            row_rule = self.command_table.get_replacement_rule(row_index)
            if row_rule:
                return row_rule
        return self.global_replacement_rule

    def _is_replaceable_command(self, command, row_index):
        """判断当前命令是否能按有效规则替换,特殊命令不作为普通文本替换."""
        if not self.replace_send_check.isChecked():
            return False
        cmd_type_str, _param = command_parts(command)
        if cmd_type_str and any(ct.value == cmd_type_str for ct in SpecialCommandType):
            return False
        try:
            return can_replace(
                unescape_text(command),
                self._effective_replacement_rule(row_index),
            )
        except ValueError:
            return False

    def update_replacement_mode_ui(self):
        """同步替换发送开关,按钮和各行可替换指示."""
        if not hasattr(self, "command_table"):
            return
        for row in range(self.command_table.rowCount()):
            _enable, command, _comment = self.command_table.get_row_data(row)
            self.command_table.set_send_button_replaceable(
                row, self._is_replaceable_command(command, row)
            )
        self._update_continuous_button_style()

    def _update_continuous_button_style(self):
        """按连续发送及替换模式更新连续发送按钮样式."""
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
        """返回准备发送时替换后的文本,不注册响应校验."""
        return self._prepare_outgoing_command(command, row_index)[0]

    def _prepare_outgoing_command(self, command, row_index):
        """返回 (替换后的文本, 可选响应校验配置);规则异常时记录错误并保留原命令."""
        if not self.replace_send_check.isChecked():
            return command, None
        rule = self._effective_replacement_rule(row_index)
        try:
            source_pattern = compile_pattern(rule["find"], rule["options"]) if rule else None
            source_match = source_pattern.search(command) if source_pattern else None
            replaced, matched = replace_text(command, rule)
            if not matched or source_match is None:
                return replaced, None

            response_validation = rule.get("response_validation") or {}
            expression = response_validation.get("expression", "")
            if not response_validation.get("enabled", False) or not expression:
                return replaced, None
            expanded, response_pattern = compile_expanded_response_pattern(
                expression,
                source_match,
                rule["options"],
            )
            (
                _diagnostic_expression,
                diagnostic_pattern,
                diagnostic_checks,
            ) = compile_diagnostic_response_pattern(
                expression,
                source_match,
                rule["options"],
            )
            return replaced, {
                "pattern": response_pattern,
                "expanded_expression": expanded,
                "replaced_command": replaced,
                "diagnostic_pattern": diagnostic_pattern,
                "diagnostic_checks": diagnostic_checks,
                "timeout_ms": response_validation.get("timeout_ms", 100),
                "on_failure": response_validation.get("on_failure", "continue"),
                "color_policy": self._effective_response_color_policy(
                    response_validation.get("color_policy", "sticky_failure")
                ),
                "show_error": response_validation.get("show_error", False),
            }
        except ValueError as exc:
            self.output_manager.append_text(f"错误: 替换规则无效: {exc}", OutputSource.ERROR)
            return command, None

    def _effective_response_color_policy(self, loop_policy):
        """循环策略只作用于已开启循环的连续发送."""
        if self.is_continuous_sending and self.loop_send_check.isChecked():
            return loop_policy
        return "latest"

    def _register_response_validation(self, validation, row_index, start_offset):
        """从接收流 start_offset 登记一次响应校验,弱引用关联行控件并启动超时检查."""
        if not validation:
            return
        edit = None
        if row_index is not None and 0 <= row_index < self.command_table.rowCount():
            edit = self.command_table.cellWidget(row_index, 1)
        self.response_validation_manager.add_pending(
            row_index=-1 if row_index is None else row_index,
            row_widget_ref=weakref.ref(edit) if edit is not None else None,
            pattern=validation["pattern"],
            expanded_expression=validation["expanded_expression"],
            timeout_ms=validation["timeout_ms"],
            on_failure=validation["on_failure"],
            color_policy=validation["color_policy"],
            diagnostic_pattern=validation.get("diagnostic_pattern"),
            diagnostic_checks=validation.get("diagnostic_checks"),
            show_error=validation.get("show_error", False),
            replaced_command=validation.get("replaced_command", ""),
            start_offset=start_offset,
        )
        if not self.response_validation_timer.isActive():
            self.response_validation_timer.start()

    def _process_response_validation_text(self, text):
        """将新增接收文本交给流式校验器,并处理其中已完成的校验结果."""
        for result in self.response_validation_manager.feed(text):
            self._handle_response_validation_result(result)
        if not self.response_validation_manager.pending:
            self.response_validation_timer.stop()

    def _expire_response_validations(self):
        """处理到期的响应校验;没有待确认项目时停止轮询计时器."""
        for result in self.response_validation_manager.expire():
            self._handle_response_validation_result(result)
        if not self.response_validation_manager.pending:
            self.response_validation_timer.stop()

    def _handle_response_validation_result(self, result):
        """更新校验统计和行状态;失败时按配置显示诊断并决定是否停止连续发送."""
        self.validation_complete_count += 1
        if result.passed:
            self.validation_pass_count += 1

        pending = result.pending
        edit = pending.row_widget_ref() if pending.row_widget_ref else None
        if edit is not None:
            try:
                edit.set_validation_state(
                    "passed" if result.passed else "failed",
                    pending.color_policy,
                )
            except RuntimeError:
                edit = None

        self.update_statistics()
        if result.passed:
            return

        if pending.show_error:
            current_row = getattr(edit, "row_index", pending.row_index)
            row_text = (
                f"第 {current_row + 1} 行" if current_row >= 0 else "发送命令"
            )
            replaced_text = (
                f"替换后 {pending.replaced_command!r}; "
                if pending.replaced_command
                else ""
            )
            if result.reason == "value_mismatch":
                differences = "; ".join(
                    f"{item['reference']}: [{item['actual']}] != [{item['expected']}]"
                    for item in result.comparisons
                )
                message = (
                    f"校验失败 ({row_text}): {replaced_text}"
                    f"值不一致, {differences}"
                )
            else:
                received = result.received_text
                if len(received) > 500:
                    received = "..." + received[-500:]
                message = (
                    f"校验失败 ({row_text}): {replaced_text}超时 "
                    f"{pending.expanded_expression!r}; 期间接收 {received!r}"
                )
            self.output_manager.append_text(message, OutputSource.ERROR)
        if pending.on_failure == "stop" and self.is_continuous_sending:
            self.stop_continuous_sending()

    def send_command(self, command, row_index=None):
        """发送命令"""
        if not self.can_send_serial_data():
            self.output_manager.append_text("错误: 请先打开串口", OutputSource.ERROR)
            if self.is_continuous_sending:
                self.stop_continuous_sending()
            return False

        try:
            command, response_validation = self._prepare_outgoing_command(
                command, row_index
            )
            # 添加结尾标识符
            ending = self.get_ending_chars()
            full_command = command.encode('utf-8') + ending
            validation_start_offset = self.response_validation_manager.end_offset
            remote = self.is_remote_client_active()

            # 发送数据
            if remote and response_validation:
                edit = self.command_table.cellWidget(row_index, 1) if row_index is not None else None
                edit_ref = weakref.ref(edit) if edit is not None else None
                edit_text = edit.text() if edit is not None else None
                validation_queue = self.response_validation_manager.pending
                def confirmed(success):
                    # clear() 会替换列表;复位后不能由迟到回执重新登记旧校验.
                    if not success or self.response_validation_manager.pending is not validation_queue:
                        return
                    current = edit_ref() if edit_ref else None
                    current_row = None
                    try:
                        if (current is not None and current.text() == edit_text
                                and self.command_table.cellWidget(current.row_index, 1) is current):
                            current_row = current.row_index
                    except RuntimeError:
                        pass  # 原控件已删除,仍可校验/计数,但不能给新行染色.
                    self._register_response_validation(response_validation, current_row,
                        self.response_validation_manager.end_offset)
                sent_bytes = self._write_serial_data(full_command, confirmed)
            else:
                sent_bytes = self._write_serial_data(full_command)
            if sent_bytes > 0:
                if not remote:
                    self._register_response_validation(
                        response_validation,
                        row_index,
                        validation_start_offset,
                    )
                self.send_count += sent_bytes
                self.update_statistics()

                # 重置接收时间戳标志
                self.output_manager.reset_receive_timestamp()

                # 收录规则统一由日志配置决定,显示开关不拦截事件.
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
        if not self.is_continuous_sending and not self._remote_device_pending:
            self._remote_sequence_failed = False
            self._command_generation += 1
        enable, command, comment = self.command_table.get_row_data(row)
        if command is not None:  # 允许空字符串, 只要行数据存在
            # 检查是否为特殊指令
            cmd_type_str, param = command_parts(command)
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
            unescaped_command = unescape_text(command)
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
            self.output_manager.append_text(f"[HEX]: {data.hex(' ').upper()}", OutputSource.SEND)
            return True
        return False

    def update_baudrate(self, baudrate, notify_remote=True):
        """设置当前后端波特率;远程返回值是排队状态,最终状态由设备端回执报告."""
        try:
            baudrate = validate_baudrate(baudrate)
        except ValueError as exc:
            self.output_manager.append_text(str(exc), OutputSource.ERROR)
            return False
        if self.serial_operation_busy():
            return False
        if self.remote_mode == "client" and notify_remote:
            return self.request_remote_device("baudrate", {"value": baudrate})
        if self.serial_thread and not self.serial_thread.set_baudrate(baudrate):
            return False
        self.baud_combo.setCurrentText(str(baudrate))
        self.save_state()
        self.output_manager.append_text(output_rules.baudrate_updated(baudrate), OutputSource.SYSTEM)
        self.send_remote_serial_config()
        return True

    def update_com_port(self, port):
        """更新 COM 口 (供特殊指令和 CLI 配置复用) """
        if self.serial_operation_busy():
            return False
        port_id = port.strip().split(" ")[0]
        if not port_id:
            self.output_manager.append_text("错误: COM口不能为空", OutputSource.ERROR)
            return False

        if self.remote_mode == "client":
            return self.request_remote_device("comport", {"port": port_id})

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
        target = normalize_ending_text(ending_text)
        index = self.ending_combo.findText(target)
        if index >= 0:
            self.ending_combo.setCurrentIndex(index)
            self.output_manager.append_text(output_rules.ending_set(target), OutputSource.SYSTEM)
            return True

        for i in range(self.ending_combo.count()):
            item_text = self.ending_combo.itemText(i)
            if item_text == target or item_text.replace(r"\r", "\r").replace(r"\n", "\n") == target:
                self.ending_combo.setCurrentIndex(i)
                self.output_manager.append_text(output_rules.ending_set(item_text), OutputSource.SYSTEM)
                return True

        self.output_manager.append_text(f"错误: 不支持的结尾标识符: {ending_text}", OutputSource.ERROR)
        return False

    def trigger_send_mode(self, module_name):
        """刷新并选择模块,以单次非循环模式启动;已有流程先停止,再延时重启."""
        module_name = module_name.strip()
        # 刷新模块列表以确保最新
        self.refresh_modules()
        
        if module_name in self.modules or module_name == "全部":
            self.jump_module_combo.setCurrentText(module_name)
            self.output_manager.append_text(f"触发模块跳转: {module_name} (仅发送一次)", OutputSource.SYSTEM)
            
            # 设置强制不循环标志
            self._force_no_loop = True
            
            # 如果当前没有在连续发送, 则启动
            if not self.is_continuous_sending:
                self.start_continuous_send()
            else:
                # 如果已经在连续发送, 我们需要停止当前的发送并重新开始.
                # 注意:stop_continuous_sending 会清除 _force_no_loop, 所以我们需要在之后重新设置
                self.stop_continuous_sending()
                self._force_no_loop = True
                # 延迟一小段时间后重新启动, 确保之前的定时器都已清理
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
            # 手动启动时, 清除强制不循环标志
            if hasattr(self, '_force_no_loop'):
                del self._force_no_loop
            self.start_continuous_send()
        else:
            self.stop_continuous_send()

    def start_continuous_send(self):
        """开始连续发送"""
        if not self._remote_device_pending:
            self._remote_sequence_failed = False
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
        self._command_generation += 1
        self.is_continuous_sending = False
        if self.firmware_download_running:
            self.cancel_firmware_download()
        # 清除强制不循环标志
        if hasattr(self, '_force_no_loop'):
            del self._force_no_loop
            
        self.continuous_btn.setText("连续发送")
        self._update_continuous_button_style()
        self.continuous_timer.stop()

    def send_continuous_commands(self):
        """收集当前模块勾选的命令,启动由定时器,模块及下载回调驱动的发送链.

        模块结构由 refresh_modules 独立解析;未勾选命令不加入当前发送队列.
        远程设备步骤经 schedule_command_next 等待实际回执,失败停止后续步骤.
        """
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
                cmd_type_str, param = command_parts(command)
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
                        # 未知特殊指令, 按普通命令处理
                        unescaped_command = unescape_text(command)
                        commands_to_send.append((row, unescaped_command, False))
                else:
                    # 普通命令
                    unescaped_command = unescape_text(command)
                    commands_to_send.append((row, unescaped_command, False))
            else:
                # 未勾选, 但如果是 mode 指令, 虽然不发送, 但可能需要识别(目前逻辑是勾选才发送/执行)
                # 这里保持原样, 未勾选的不加入发送队列
                pass

        # 发送命令
        def send_next_command(index=0):
            """执行连续发送的一步,依据流程标志,延时及异步完成回调决定后续步骤."""
            # 检查是否需要跳过到循环(StopContinuous:0 的效果)
            if getattr(self, '_skip_to_loop', False):
                self._skip_to_loop = False  # 清除标志
                # 立即跳转到循环检查逻辑
                index = len(commands_to_send)  # 设置为最后, 触发循环检查

            if not self.is_continuous_sending or index >= len(commands_to_send):
                # 检查是否开启了循环发送
                if self.is_continuous_sending and self.loop_send_check.isChecked() and commands_to_send:
                    # 等待循环间隔后再次开始
                    self.schedule_command_next(self.loop_interval_spin.value(), self.send_continuous_commands)
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
                        self.schedule_command_next(int(delay_ms), lambda: send_next_command(index + 1))
                        return
                    except ValueError:
                        self.output_manager.append_text(output_rules.invalid_delay(param), OutputSource.ERROR)
                elif command_type == SpecialCommandType.SENDHEX:
                    # 执行 SendHex 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    else:
                        # 执行失败时记录错误并继续下一条, 不中断连续发送
                        self.output_manager.append_text(
                            output_rules.special_command_failed("SendHex", param),
                            OutputSource.ERROR,
                        )
                        self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type in (SpecialCommandType.BAUDRATE, SpecialCommandType.COMPORT):
                    # 执行 BaudRate 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        # 波特率切换可能导致串口重启, 等待 500ms 确保稳定
                        self.schedule_command_next(500, lambda: send_next_command(index + 1))
                    else:
                        self.output_manager.append_text(
                            output_rules.special_command_failed("BaudRate", param),
                            OutputSource.ERROR,
                        )
                        self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SETENDLOG:
                    # 执行 SetEndlog 指令
                    if self.special_command_manager.execute(command_type, param, self):
                        self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    else:
                        self.output_manager.append_text(
                            output_rules.special_command_failed("SetEndlog", param),
                            OutputSource.ERROR,
                        )
                        self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))
                    return
                elif command_type == SpecialCommandType.SENDMODE:
                    # 执行 SendMode 指令 - 发送指定模块的内容后继续当前模块
                    def on_sendmode_complete():
                        """模块执行结束后按发送间隔调度外层下一条命令."""
                        # SendMode 执行完毕后, 继续当前模块的下一条命令
                        self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))

                    self.special_command_manager.execute_sendmode_inline(param.strip(), self, on_sendmode_complete)
                    return
                elif command_type == SpecialCommandType.FIRMWAREDOWNLOAD:
                    def on_firmware_download_complete(success):
                        """下载失败停止连续发送,成功时按间隔调度下一条命令."""
                        if not success:
                            self.stop_continuous_sending()
                            return
                        if self.is_continuous_sending:
                            self.schedule_command_next(
                                self.interval_spin.value(),
                                lambda: send_next_command(index + 1),
                            )

                    if not self.special_command_manager.execute(
                        command_type,
                        param,
                        self,
                        on_firmware_download_complete,
                    ):
                        self.stop_continuous_sending()
                    return
                elif command_type == SpecialCommandType.STOPCONTINUOUS:
                    # 执行 StopContinuous 指令
                    self.special_command_manager.execute(command_type, param, self)
                    # 如果设置了 _skip_to_loop 标志(模式0), 立即触发循环检查
                    # 否则(模式1)会调用 stop_continuous_sending, is_continuous_sending 会变为 False
                    send_next_command(len(commands_to_send))  # 跳转到结束, 触发循环检查
                    return

                # 其他特殊指令(如mode)在发送时忽略
                self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))
            else:
                # 发送普通命令
                if self.send_command(command, row):
                    self.schedule_command_next(self.interval_spin.value(), lambda: send_next_command(index + 1))
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
        """按 mode/modeend 建立行号到模块的映射,并恢复仍有效的下拉选择.

        结构标记无论是否勾选都参与模块定义;勾选的 modeend 还会作为运行命令
        加入该模块.这里只建立映射,是否执行某行由发送阶段的启用状态决定.
        """
        # 保存当前选择
        jump_selection = self.jump_module_combo.currentText() if self.jump_module_combo.currentText() else "全部"
        send_selection = self.send_module_combo.currentText() if self.send_module_combo.currentText() else "全部"

        self.modules.clear()
        self.jump_module_combo.clear()
        self.send_module_combo.clear()
        self.jump_module_combo.addItem("全部")
        self.send_module_combo.addItem("全部")

        from core.command_executor import build_module_index
        modules, names = build_module_index(self.command_table.get_all_commands())
        self.modules.update(modules)
        self.jump_module_combo.addItems(names)
        self.send_module_combo.addItems(names)

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
        selection_dialog = SaveDataSelectionDialog(
            self,
            selected_sources=self.save_source_selection,
            selected_system_levels=self.save_system_level_selection,
        )
        if selection_dialog.exec_() != QDialog.Accepted:
            return
        self.save_source_selection = selection_dialog.selected_sources()
        self.save_system_level_selection = selection_dialog.selected_system_levels()
        self.save_state()

        filename, _ = QFileDialog.getSaveFileName(
            self, "保存接收数据", "", "Text Files (*.txt);;All Files (*)")

        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(
                        self.output_manager.text_for_sources(
                            selection_dialog.selected_sources(),
                            selection_dialog.selected_system_levels(),
                        )
                    )
                self.output_manager.append_text(f"数据已保存到: {filename}", OutputSource.SYSTEM)
            except (OSError, UnicodeError) as e:
                self.output_manager.append_text(f"错误: 保存文件失败: {str(e)}", OutputSource.ERROR)

    def log_directory(self):
        """返回两个配置入口共用的有效目录,空配置使用工作 JSON 旁的 logs."""
        return self.log_settings["log_directory"] or os.path.join(
            os.path.dirname(os.path.abspath(self.config_manager.config_file)), "logs")

    def can_change_log_directory(self):
        """保存关闭且旧批次已妥善结束时,两个目录入口才允许编辑."""
        archive = self.output_manager.archive
        return not self.save_full_log_check.isChecked() and not (
            archive and (archive.busy or archive.pending or archive.error))

    def _choose_log_directory(self):
        """快捷选择目录并通过共用事务立即保存,取消或保存失败保留原设置."""
        from core import gui_config
        directory = QFileDialog.getExistingDirectory(self, "选择日志目录", self.log_directory())
        if directory:
            try:
                gui_config.apply(self, {"state": {"log_settings": {"log_directory": directory}}})
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "日志目录未保存", str(exc))

    def _apply_log_settings(self, _value=None):
        """在初始化/配置事务结束后同步日志运行态,不在控件回滚中裁剪历史."""
        if self._loading_state or self._closing_logs:
            return
        self._sync_log_controls()
        self.output_manager.set_limit(self.log_settings["limit_mb"] * 1_000_000)
        try:
            self.output_manager.configure_archive(self.save_full_log_check.isChecked(), self.log_directory())
        except (OSError, ValueError) as exc:
            self._on_log_archive_failed(str(exc))
            return
        self._update_log_status()

    def _update_log_status(self):
        """由批次状态信号更新主界面,不轮询磁盘或内存."""
        archive = self.output_manager.archive
        available = self.can_change_log_directory()
        self.log_directory_button.setEnabled(available)
        hint = f"日志目录: {self.log_directory()}"
        if archive:
            hint += f"\n日志文件: {archive.path}"
        if not available:
            hint += "\n修改目录前请停止保存并等待尾部写完；失败时先另存未确认日志"
        self.log_directory_button.setToolTip(hint)
        if archive and archive.error:
            text = archive.error
        elif archive and archive.busy and not self.save_full_log_check.isChecked():
            text = "正在保存尾部"
        else:
            text = ""
        self.log_status_label.setText(text)
        self.log_status_label.setVisible(bool(text))
        self.log_status_label.setToolTip(archive.error or str(archive.path) if archive else "")

    def _on_log_archive_failed(self, message):
        """独立于日志来源筛选显示文件失败,停止后续归档但保留未确认批次."""
        previous = self.save_full_log_check.blockSignals(True)
        self.save_full_log_check.setChecked(False)
        self.save_full_log_check.blockSignals(previous)
        self.output_manager.archive_enabled = False
        self._update_log_status()
        self.log_status_label.setText(message)
        self.log_status_label.setToolTip(message)
        self.log_status_label.setVisible(True)
        archive = self.output_manager.archive
        self.recover_log_button.setVisible(archive is not None and bool(archive.error))
        self.schedule_state_save()

    def _recover_log_archive(self):
        """用户显式另存未确认数据;原文件可能部分写入,不会自动拼接或重试."""
        archive = self.output_manager.archive
        if archive.busy:
            QMessageBox.information(self, "日志保存", "正在结束写入批次，请稍后另存。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "另存未确认日志到新文件", "recovered.log", "Log Files (*.log)")
        if path:
            self.recover_log_archive(path)

    def log_archive_status(self):
        """查询真实归档状态,不读取文件或触发配置保存."""
        archive = self.output_manager.archive
        return dict(scope="current-process", enabled=self.output_manager.archive_enabled,
                    archive=archive.status() if archive else None)

    def recover_log_archive(self, path, completed=None):
        """GUI按钮和CLI共用显式恢复;回调只在写者finished后执行.

        Args:
            path (str): 用户显式指定的新文件路径,独占新建.
            completed (callable | None): 实际写入结束后接收(success,message).
        """
        archive = self.output_manager.archive
        if archive is not None and archive.busy:
            raise ValueError("日志批次尚未结束，请稍后另存")
        if archive is None or not archive.error:
            raise ValueError("当前没有失败的未确认日志可另存")
        if completed is not None:
            def finished():
                archive.idle.disconnect(finished)
                completed(not bool(archive.error), archive.error or "未确认日志已另存完成")
            archive.idle.connect(finished)
        try:
            archive.recover_to(path)
        except (ValueError, OSError):
            if completed is not None:
                archive.idle.disconnect(finished)
            raise
        self.recover_log_button.setEnabled(False)
        self.log_status_label.setText("正在另存未确认日志…")

    def _on_log_archive_idle(self):
        """处理写盘完成及等待中的窗口关闭;线程已经 finished 后才销毁窗口."""
        archive = self.output_manager.archive
        self.recover_log_button.setEnabled(True)
        self.recover_log_button.setVisible(bool(archive.error))
        self._update_log_status()
        if self._closing_logs:
            QTimer.singleShot(0, self.close)

    def clear_receive_data(self):
        """清空接收数据"""
        self.output_manager.clear()
        self.output_manager.append_text("接收数据已清空", OutputSource.SYSTEM)

    def update_statistics(self):
        """更新统计信息"""
        self.send_count_label.setText(f"发送: {self.send_count} 字节")
        self.receive_count_label.setText(f"接收: {self.receive_count} 字节")
        self.validation_count_label.setText(
            f"校验 : {self.validation_pass_count}/{self.validation_complete_count} Err/Pass"
        )

    def reset_statistics(self):
        """复位统计"""
        self.send_count = 0
        self.receive_count = 0
        self.validation_pass_count = 0
        self.validation_complete_count = 0
        self.response_validation_manager.clear()
        self.response_validation_timer.stop()
        self.command_table.clear_validation_states()
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

    def _set_long_task_ui_busy(self, busy):
        """长任务期间禁用主界面交互, 避免可重入操作"""
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
        """为瞬时按钮绑定按下变色,释放恢复"""
        self._apply_button_color(button, normal_color, extra_styles)
        button.pressed.connect(lambda b=button, c=pressed_color, s=extra_styles: self._apply_button_color(b, c, s))
        button.released.connect(lambda b=button, c=normal_color, s=extra_styles: self._apply_button_color(b, c, s))

    def _set_action_button_running(self, button, running, normal_color, running_color=Colors.RED_BUTTON, extra_styles=""):
        """设置执行型按钮运行态颜色"""
        target_color = running_color if running else normal_color
        self._apply_button_color(button, target_color, extra_styles)

    def publish_file_event(self, event):
        """记录执行端唯一文件结果并转发既有 GUI 订阅,显示过滤不改变事件."""
        event = dict(event)
        event.setdefault("file_event_id", uuid.uuid4().hex)
        self.output_manager.append_text(event["message"], OutputSource(event["source"]),
            system_level=event["system_level"], timestamp=event["timestamp"])
        self.statusBar().showMessage(event["message"])
        if self.gui_cli_bridge:
            self.gui_cli_bridge.broadcast(event)
        return event

    def start_template_operation(self, operation, path, completed=None, plain_text=False, owner=None):
        """GUI 按钮与 CLI 共用无对话框模板入口,IO 在线程,控件分块只在 Qt 线程处理.

        Args:
            operation: template-import 或 template-export.
            path: 客户端已解析或文件对话框选择的绝对路径.
            completed: 唯一终态回调,接收共同文件事件;发布后调用.
            plain_text: 仅 GUI 明确选择的纯文本导入方式.
            owner: 桥接当前请求对象,只有相同对象才允许复用其任务占用;GUI 按钮传 None.
        """
        path = template_path(path)
        if (self.serial_operation_busy() or self.is_continuous_sending or self._remote_device_pending
                or (self.gui_cli_bridge and self.gui_cli_bridge.active is not None
                    and self.gui_cli_bridge.active is not owner)):
            event = self.publish_file_event(output_rules.file_operation_event(operation, path, "failed", error="GUI 当前有任务运行"))
            if completed:
                completed(event)
            return False
        if operation not in ("template-import", "template-export"):
            raise ValueError("未知模板操作")
        task = dict(operation=operation, path=path, completed=completed, rows=[], cancelled=False,
            old_loading=self._loading_state, old_dirty=self._state_dirty, plain_text=plain_text,
            before_modules=copy.deepcopy(self.modules),
            before_names=[self.jump_module_combo.itemText(index) for index in range(1, self.jump_module_combo.count())])
        self._template_task = task
        self._loading_state = True
        self._state_save_timer.stop()
        self._set_long_task_ui_busy(True)
        task["progress"] = self._create_progress_dialog("正在处理模板...", 1000)
        # 中央控件已禁用;非模态 setValue 不隐式 processEvents,避免取消重入提交.
        task["progress"].setWindowModality(Qt.NonModal)
        task["progress"].canceled.connect(self.cancel_template_operation)
        self._snapshot_template_chunk()
        return True

    def cancel_template_operation(self):
        """只取消当前模板流程;配置原子提交开始后等待实际保存结果."""
        task = self._template_task
        if task and task.get("phase") != "save-config":
            task["cancelled"] = True
            if task.get("worker"):
                task["worker"].requestInterruption()

    def _snapshot_template_chunk(self):
        task = self._template_task
        if task["cancelled"]:
            self._finish_template("cancelled")
            return
        deadline = time.monotonic() + 0.008
        while len(task["rows"]) < self.command_table.rowCount() and time.monotonic() < deadline:
            task["rows"].append(self.command_table.get_row_data(len(task["rows"])))
        if len(task["rows"]) < self.command_table.rowCount():
            QTimer.singleShot(0, self._snapshot_template_chunk)
            return
        task["before_state"] = self.snapshot_state(commands=task["rows"])
        task["before_config"] = self.config_manager.config
        if task["operation"] == "template-import":
            self._start_template_worker("read")
        else:
            self._start_template_worker("export", rows=task["rows"])

    def _start_template_worker(self, phase, rows=None, manager=None):
        task = self._template_task
        task["phase"] = phase
        if phase == "save-config":
            task["progress"].setCancelButton(None)
        worker = TemplateWorker(phase, task["path"], rows, manager, task["plain_text"], self)
        task["worker"] = worker
        worker.progress.connect(lambda value: self._template_progress(value, len(rows or [])))
        worker.finished.connect(self._template_worker_finished)
        worker.start()

    def _template_progress(self, value, total):
        task = self._template_task
        if task:
            task["progress"].setValue(min(999, 100 + int(800 * value / max(total, 1))))
            if self.gui_cli_bridge:
                self.gui_cli_bridge.template_progress(value, total)

    def _template_worker_finished(self):
        task = self._template_task
        worker = task.pop("worker")
        error, result, phase = worker.error, worker.result, task["phase"]
        worker.deleteLater()
        if error:
            if task.get("applied"):
                task["error"] = error
                self._apply_template_rows(task["rows"], restoring=True)
            else:
                self._finish_template("cancelled" if isinstance(error, InterruptedError) else "failed", error)
            return
        if phase == "save-config":
            self.config_manager.config = result
            self._state_dirty = False
            self._finish_template("success")
        elif task["cancelled"] and phase != "export":
            self._finish_template("cancelled")
        elif phase == "read":
            task["new_rows"], task["new_modules"] = result
            self._apply_template_rows(task["new_rows"])
        else:
            self._finish_template("success")

    def _apply_template_rows(self, rows, restoring=False):
        task = self._template_task
        task["applying"] = rows
        task["index"] = 0
        task["restoring"] = restoring
        if not task.get("applied"):
            task["signals_blocked"] = self.command_table.blockSignals(True)
            self.command_table.setUpdatesEnabled(False)
            # 旧回执仍计数/诊断,但不能再给替换或回滚后的命令染色.
            for pending in self.response_validation_manager.pending:
                pending.row_widget_ref = None
        task["applied"] = True
        # 一次调整行结构,已有控件继续复用,空行在后续分块中填充;回滚走同一路径.
        if self.command_table.rowCount() != len(rows):
            self.command_table.setRowCount(len(rows))
        self.command_table.clearSelection()
        self.command_table.comments.clear()
        self.command_table.replacement_rules.clear()
        QTimer.singleShot(0, self._apply_template_chunk)

    def _apply_template_chunk(self):
        task = self._template_task
        if task["cancelled"] and not task["restoring"]:
            self._apply_template_rows(task["rows"], restoring=True)
            return
        try:
            deadline = time.monotonic() + 0.008
            while task["index"] < len(task["applying"]) and time.monotonic() < deadline:
                self.command_table.replace_command_row(task["index"], *task["applying"][task["index"]])
                task["index"] += 1
            self._template_progress(task["index"], len(task["applying"]))
            if task["index"] < len(task["applying"]):
                QTimer.singleShot(0, self._apply_template_chunk)
                return
            if task["cancelled"] and not task["restoring"]:
                self._apply_template_rows(task["rows"], restoring=True)
                return
            self._begin_template_modules()
        except Exception as exc:
            if task["restoring"]:
                self._finish_template("failed", exc)
            else:
                task["error"] = exc
                self._apply_template_rows(task["rows"], restoring=True)

    def _begin_template_modules(self):
        task = self._template_task
        modules, names = ((task["before_modules"], task["before_names"]) if task["restoring"] else task["new_modules"])
        self.modules.clear()
        self.modules.update(modules)
        task["module_names"], task["module_index"] = names, 0
        for combo in (self.jump_module_combo, self.send_module_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("全部")
        QTimer.singleShot(0, self._apply_template_modules_chunk)

    def _apply_template_modules_chunk(self):
        task = self._template_task
        try:
            if task["cancelled"] and not task["restoring"]:
                self._apply_template_rows(task["rows"], restoring=True)
                return
            deadline = time.monotonic() + 0.008
            while task["module_index"] < len(task["module_names"]) and time.monotonic() < deadline:
                name = task["module_names"][task["module_index"]]
                self.jump_module_combo.addItem(name)
                self.send_module_combo.addItem(name)
                task["module_index"] += 1
            if task["module_index"] < len(task["module_names"]):
                QTimer.singleShot(0, self._apply_template_modules_chunk)
                return
            self.send_find_bar.set_scopes(self.modules.keys())
            if task["cancelled"] and not task["restoring"]:
                self._apply_template_rows(task["rows"], restoring=True)
                return
            if task["restoring"]:
                before = task["before_state"]
                self.command_table.load_replacement_rules(before["command_replacement_rules"], notify=False)
                self.jump_module_combo.setCurrentText(before["send_settings"]["jump_module"])
                self.send_module_combo.setCurrentText(before["send_settings"]["send_module"])
                self._finish_template("failed" if task.get("error") else "cancelled", task.get("error"))
                return
            self.jump_module_combo.setCurrentText("全部")
            self.send_module_combo.setCurrentText("全部")
            candidate = copy.deepcopy(task["before_config"])
            candidate["state"] = self.snapshot_state(commands=task["new_rows"])
            manager = copy.copy(self.config_manager)
            manager.config = candidate
            self._start_template_worker("save-config", manager=manager)
        except Exception as exc:
            if task["restoring"]:
                self._finish_template("failed", exc)
            else:
                task["error"] = exc
                self._apply_template_rows(task["rows"], restoring=True)

    def _finish_template(self, outcome, error=None):
        task = self._template_task
        if task.get("applied"):
            self.jump_module_combo.blockSignals(False)
            self.send_module_combo.blockSignals(False)
            self.command_table.blockSignals(task["signals_blocked"])
            self.command_table.setUpdatesEnabled(True)
            self.command_table.viewport().update()
        self._loading_state = task["old_loading"]
        if outcome != "success":
            self._state_dirty = task["old_dirty"]
        task["progress"].close()
        task["progress"].deleteLater()
        self._set_long_task_ui_busy(False)
        self._template_task = None
        count = len(task.get("new_rows", task["rows"])) if outcome == "success" else None
        event = self.publish_file_event(output_rules.file_operation_event(task["operation"], task["path"], outcome, count, error))
        if task["completed"]:
            task["completed"](event)

    def _remember_template_directory(self, filename):
        """仅 GUI 非空选择更新共享目录,失败经既有错误输出提示."""
        try:
            self.config_manager.set_last_used_directory(os.path.dirname(filename))
        except Exception as exc:
            self.output_manager.append_text(f"错误: 保存模板目录失败: {exc}", OutputSource.ERROR)

    def import_template(self):
        """选择文件后调用共用异步模板接口,保留 GUI 明确选择纯文本的入口."""
        filename, _ = QFileDialog.getOpenFileName(self, "导入模板",
            self.config_manager.get_last_used_directory(), "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")
        if not filename:
            return
        self._remember_template_directory(filename)
        def completed(event):
            if event["outcome"] == "failed" and event.get("error") and "格式" in event["error"]:
                choice = self._show_template_format_error_dialog(filename, event["error"])
                if choice == "plain_text":
                    self.start_template_operation("template-import", filename, plain_text=True)
                elif choice == "reselect":
                    self.import_template()
        self.start_template_operation("template-import", filename, completed)

    def _show_template_format_error_dialog(self, filename, error):
        """模板格式不匹配时, 让用户选择重新选文件或按纯文本导入."""
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Warning)
        message_box.setWindowTitle("模板格式不正确")
        message_box.setText(f"文件不是有效的模板格式:\n{os.path.basename(filename)}")
        message_box.setInformativeText(
            f"{error}\n\n正确格式:\n"
            "True,发送字符串,注释\n"
            "False,发送字符串,注释\n\n"
            "是否重新选择文件, 还是依然按纯文本导入？"
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

    def export_template(self):
        """选择目标后调用与 CLI 相同的 GUI 异步原子导出接口."""
        filename, _ = QFileDialog.getSaveFileName(self, "导出模板",
            self.config_manager.get_last_used_directory(), "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)")
        if filename:
            self._remember_template_directory(filename)
            self.start_template_operation("template-export", filename)

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
            self.output_manager.append_text(f"行号超出范围, 当前共有 {total_rows} 行。", OutputSource.ERROR)
            return

        self.command_table.setCurrentCell(target_row, 0)
        self.command_table.scrollTo(self.command_table.model().index(target_row, 0))
        self.command_table.selectRow(target_row)
        self.output_manager.append_text(f"已跳转到第 {target_row + 1} 行。", OutputSource.SYSTEM)

    def schedule_state_save(self, *_args):
        """标记状态已变化,并在用户停止操作后写入配置."""
        if self._loading_state:
            return
        self._state_dirty = True
        self._state_save_timer.start()

    def _flush_pending_state(self):
        """保存待写状态;定时兜底可覆盖长时间连续编辑的场景."""
        if self._state_dirty and not self._loading_state:
            self.save_state()

    def snapshot_state(self, commands=None):
        """读取当前控件,命令表和选择状态,不保存文件,不触发设备操作."""
        return OrderedDict([
            ("basic_settings", {
                "port": self.get_selected_port_id(),
                "port_node": self.get_selected_port_node(),
                "baudrate": self.baud_combo.currentText(),
                "databits": self.data_bits_combo.currentText(),
                "parity": self.parity_combo.currentText(),
                "stopbits": self.stop_bits_combo.currentText()
            }),
            ("remote_settings", {
                "mode": self.remote_mode_combo.currentData(),
                "host": self.remote_host_input.currentText(),
                "port": self.remote_port_spin.value(),
                "token": self.remote_token_input.text()
            }),
            ("receive_settings", {
                "show_timestamp": self.timestamp_check.isChecked(),
                "display_sources": {
                    "send": self._send_display_selected,
                    "receive": self.show_recv_source_check.isChecked(),
                    "system": self.show_sys_source_check.isChecked(),
                    "error": self.show_err_source_check.isChecked()
                },
                "system_levels": {
                    level: level in self.system_level_selection
                    for level in ("normal", "warning", "info", "debug")
                },
                "save_sources": {
                    source.value: source in self.save_source_selection
                    for source in OutputSource
                },
                "save_system_levels": {
                    level: level in self.save_system_level_selection
                    for level in ("normal", "warning", "info", "debug")
                }
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
            ("log_settings", dict(copy.deepcopy(self.log_settings),
                                  save_full_log=self.save_full_log_check.isChecked())),
            ("ui_settings", {
                "h_splitter_sizes": self.h_splitter.sizes()
            }),
            ("tool_settings", {
                "left_group_expanded": {
                    "basic": self.basic_group.isExpanded(),
                    "remote": self.remote_group.isExpanded(),
                    "receive": self.receive_group.isExpanded(),
                    "send": self.send_group.isExpanded(),
                    "template": self.template_group.isExpanded(),
                    "tools": self.tools_group.isExpanded()
                }
            }),
            ("commands", self.command_table.get_all_commands() if commands is None else commands),
            ("command_replacement_rules", self.command_table.get_replacement_rules())
        ])

    def save_state(self):
        """保存当前状态到配置文件"""
        try:
            state = self.snapshot_state()
            self.config_manager.set("state", state)
            self._state_dirty = False
            if not self._loading_state:
                self.gui_cli_bridge.publish_log_settings(state)
            return True
        except Exception as e:
            print(f"保存状态失败: {e}")
            return False

    def load_state(self):
        """加载配置到控件,命令表和模块选择,按现有逻辑处理旧字段.

        加载期间通过 _loading_state 抑制自动保存;模块刷新后恢复仍存在的
        跳转/发送模块选择,不自动连接设备.
        """
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
            mode = remote_settings.get("mode", self.remote_mode_combo.itemData(0))
            mode_index = self.remote_mode_combo.findData(mode)
            if mode_index < 0:
                raise ValueError("远程角色配置无效，请检查 state.remote_settings.mode")
            self.remote_mode_combo.setCurrentIndex(mode_index)
            self.update_remote_host_options()
            self.remote_host_input.setCurrentText(remote_settings.get("host", "127.0.0.1"))
            self.remote_port_spin.setValue(remote_settings.get("port", 8765))
            self.remote_token_input.setText(remote_settings.get("token", ""))

            # 3. 接收设置
            receive_settings = state.get("receive_settings")
            if receive_settings:
                self.timestamp_check.setChecked(receive_settings.get("show_timestamp", False))
                display_sources = receive_settings.get("display_sources")
                if display_sources is None:
                    display_sources = {
                        "send": receive_settings.get("show_send_source", True),
                        "receive": receive_settings.get("show_recv_source", True),
                        "system": receive_settings.get("show_sys_source", True),
                        "error": receive_settings.get("show_err_source", True),
                    }
                self.show_send_source_check.setChecked(display_sources.get("send", True))
                self.show_recv_source_check.setChecked(display_sources.get("receive", True))
                self.show_sys_source_check.setChecked(display_sources.get("system", True))
                self.show_err_source_check.setChecked(display_sources.get("error", True))
                system_levels = receive_settings.get("system_levels") or {}
                self.system_level_selection = {
                    level
                    for level in ("normal", "warning", "info", "debug")
                    if system_levels.get(level, True)
                }
                self._update_system_level_button_text()
                save_sources = receive_settings.get("save_sources") or {}
                self.save_source_selection = {
                    source
                    for source in OutputSource
                    if save_sources.get(source.value, True)
                }
                save_system_levels = receive_settings.get("save_system_levels") or {}
                self.save_system_level_selection = {
                    level
                    for level in ("normal", "warning", "info", "debug")
                    if save_system_levels.get(level, True)
                }
            else:
                # 兼容旧版本 (从 send_settings 中读取)
                old_send_settings = state.get("send_settings", {})
                self.show_send_source_check.setChecked(old_send_settings.get("show_send_source", True))
                self.show_recv_source_check.setChecked(old_send_settings.get("show_recv_source", True))
                self.show_sys_source_check.setChecked(old_send_settings.get("show_sys_source", True))
                self.show_err_source_check.setChecked(old_send_settings.get("show_err_source", True))
                self.system_level_selection = {"normal", "warning", "info", "debug"}
                self._update_system_level_button_text()
                self.save_source_selection = set(OutputSource)
                self.save_system_level_selection = {
                    "normal", "warning", "info", "debug"
                }

            # 4. 发送设置
            send_settings = state.get("send_settings", {})
            self.loop_send_check.setChecked(send_settings.get("loop_send", False))
            self.loop_interval_spin.setValue(send_settings.get("loop_interval", 1000))
            self.interval_spin.setValue(send_settings.get("continuous_interval", 100))
            self.show_send_check.setChecked(send_settings.get("show_send", False))
            self.send_color_combo.setCurrentText(send_settings.get("send_color", "红色"))
            self.ending_combo.setCurrentText(send_settings.get("ending", r"\r\n"))
            self.replace_send_check.setChecked(send_settings.get("replace_send", False))
            if "global_replacement_rule" in send_settings:
                self.global_replacement_rule = normalize_rule(
                    send_settings.get("global_replacement_rule")
                )
            else:
                self.global_replacement_rule = normalize_rule(
                    self.DEFAULT_GLOBAL_REPLACEMENT_RULE
                )
            
            # 5. 日志规则保存在运行态,配置窗口只编辑草稿.
            log_settings = state.get("log_settings", {})
            for key, default in self.log_settings.items():
                self.log_settings[key] = copy.deepcopy(log_settings.get(key, default))
            self.save_full_log_check.setChecked(log_settings.get("save_full_log", False))

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
                "template": True,
                "tools": tool_settings.get("tools_group_expanded", False)
            }
            left_group_expanded = default_left_expanded.copy()
            left_group_expanded.update(tool_settings.get("left_group_expanded", {}))
            self.basic_group.setExpanded(left_group_expanded.get("basic", True))
            self.remote_group.setExpanded(left_group_expanded.get("remote", False))
            self.receive_group.setExpanded(left_group_expanded.get("receive", True))
            self.send_group.setExpanded(left_group_expanded.get("send", True))
            self.template_group.setExpanded(left_group_expanded.get("template", True))
            self.tools_group.setExpanded(left_group_expanded.get("tools", False))

            # 加载命令
            saved_commands = state.get("commands")
            if saved_commands is not None:
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

                # 兼容旧版本: 如果没有新的设置, 尝试使用旧的 selected_module
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
            self.remote_mode_combo.setCurrentIndex(0)
            self.update_remote_host_options()
            self.remote_host_input.setCurrentText("127.0.0.1")
            self.remote_port_spin.setValue(8765)
            self.basic_group.setExpanded(True)
            self.remote_group.setExpanded(False)
            self.receive_group.setExpanded(True)
            self.send_group.setExpanded(True)
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
        if self.serial_operation_busy():
            self.cancel_template_operation()
            self.cancel_firmware_download()
            self._firmware_download_log("INFO", "正在取消任务并释放资源，请结束后再次关闭窗口")
            event.ignore()
            return
        if not self._closing_logs:
            self.close_serial()
            if self.remote_thread:
                self.stop_remote_control()
        self.output_manager.flush_pending_output()
        self.output_manager.archive_enabled = False
        archive = self.output_manager.archive
        if archive:
            archive.flush(force=True)
            if archive.busy:
                self._closing_logs = True
                self.setEnabled(False)
                self.log_status_label.setText("正在保存日志尾部，完成后关闭…")
                self.log_status_label.setVisible(True)
                event.ignore()
                return
            if archive.error and archive.unsaved_entries():
                self._closing_logs = False
                self.setEnabled(True)
                answer = QMessageBox.warning(self, "日志尚未保存", "未确认日志仍在内存。可取消关闭并另存，或舍弃这些内容后退出。",
                                             QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
                if answer != QMessageBox.Discard:
                    event.ignore()
                    return
        if self.gui_cli_bridge:
            self.gui_cli_bridge.close()
        self.save_state()
        event.accept()
