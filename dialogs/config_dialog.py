#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
配置对话框模块, 负责外部工具和工具相关参数的配置界面。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QDialogButtonBox, QFileDialog, QGroupBox, QCheckBox, QComboBox, QSpinBox)
from managers.config_manager import ConfigManager

class ConfigDialog(QDialog):
    """配置对话框"""
    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.init_ui()

    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("配置")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        # 版本信息
        version_group = QGroupBox("版本信息")
        version_layout = QVBoxLayout(version_group)
        version_layout.addWidget(QLabel(f"工具版本: {self.config_manager.tool_version}"))
        version_layout.addWidget(QLabel(f"工具更新时间: {self.config_manager.tool_version_date}"))
        version_layout.addWidget(QLabel(f"配置文件版本: {self.config_manager.get('config_version', 'N/A')}"))
        version_layout.addWidget(QLabel(f"配置最后更新时间: {self.config_manager.get('config_last_updated', 'N/A')}"))
        layout.addWidget(version_group)

        # 工具路径配置
        tools_group = QGroupBox("工具路径")
        tools_layout = QVBoxLayout(tools_group)

        # 动态添加所有已注册的工具
        for tool_name in self.config_manager.get_all_tool_names():
            display_name = self.config_manager.get_tool_display_name(tool_name)
            self.add_tool_config_row(tools_layout, tool_name, display_name)

        layout.addWidget(tools_group)

        # 对话框按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def add_tool_config_row(self, layout, tool_name, label_text):
        """
        添加通用的工具配置行（五列布局：启用、置顶、参数、路径、浏览）

        Args:
            layout: 父布局
            tool_name: 工具名称（用于标识）
            label_text: 显示的标签文本
        """
        h_layout = QHBoxLayout()

        # 获取当前工具配置
        tool_config = self.config_manager.get_tool_config(tool_name)
        current_path = tool_config.get("path", "") if tool_config else ""
        is_enabled = tool_config.get("enabled", False) if tool_config else False
        is_always_on_top = tool_config.get("always_on_top", False) if tool_config else False

        # 第一列：启用复选框
        enable_check = QCheckBox(label_text)
        enable_check.setChecked(is_enabled)
        enable_check.setMinimumWidth(150)
        h_layout.addWidget(enable_check)

        # 第二列：置顶复选框
        always_on_top_check = QCheckBox("置顶")
        always_on_top_check.setChecked(is_always_on_top)
        always_on_top_check.setMinimumWidth(60)
        h_layout.addWidget(always_on_top_check)

        # 第三列：参数配置按钮（如果工具有额外参数）
        tool_info = self.config_manager.get_tool_info(tool_name)
        has_params = self._tool_has_extra_params(tool_info)

        if has_params:
            config_btn = QPushButton("参数")
            config_btn.setMaximumWidth(60)
            config_btn.clicked.connect(lambda: self._open_tool_params_dialog(tool_name))
            h_layout.addWidget(config_btn)
        else:
            # 占位，保持对齐
            h_layout.addSpacing(60)

        # 第四列：路径输入框
        path_edit = QLineEdit()
        path_edit.setText(current_path)
        path_edit.setPlaceholderText("留空使用内置集成")
        h_layout.addWidget(path_edit, 1)  # 拉伸填充

        # 第五列：浏览按钮
        browse_btn = QPushButton("浏览")
        browse_btn.setMinimumWidth(80)  # 设置最小宽度确保文字完整显示
        browse_btn.clicked.connect(lambda: self._browse_for_tool(tool_name, path_edit))
        h_layout.addWidget(browse_btn)

        layout.addLayout(h_layout)

        # 将控件保存为实例属性，以便在 accept 时访问
        setattr(self, f"{tool_name}_check", enable_check)
        setattr(self, f"{tool_name}_always_on_top_check", always_on_top_check)
        setattr(self, f"{tool_name}_edit", path_edit)

    def _tool_has_extra_params(self, tool_info):
        """检查工具是否有额外参数（除了 path 和 enabled）"""
        if not tool_info:
            return False

        default_config = tool_info.get("default_config", {})
        # 检查是否有除 path 和 enabled 之外的参数
        extra_keys = set(default_config.keys()) - {"path", "enabled"}
        return len(extra_keys) > 0

    def _get_tool_param(self, tool_name, param_name, fallback=None):
        """
        获取工具参数值，优先使用配置文件中的值，如果不存在则使用 REGISTERED_TOOLS 中的默认值

        Args:
            tool_name: 工具名称
            param_name: 参数名称
            fallback: 如果 REGISTERED_TOOLS 中也没有该参数时的备用值

        Returns:
            参数值
        """
        tool_config = self.config_manager.get_tool_config(tool_name)
        tool_info = self.config_manager.get_tool_info(tool_name)
        default_config = tool_info.get("default_config", {}) if tool_info else {}

        if tool_config and param_name in tool_config:
            return tool_config[param_name]
        elif param_name in default_config:
            return default_config[param_name]
        else:
            return fallback

    def _open_tool_params_dialog(self, tool_name):
        """打开工具参数配置对话框"""
        if tool_name == "bin_hex_converter":
            self._open_bin_hex_params_dialog()
        elif tool_name == "number_conversion_dialog":
            self._open_number_conversion_params_dialog()
        elif tool_name == "firmware_downloader":
            self._open_firmware_downloader_params_dialog()
        # 未来可以添加其他工具的参数对话框
        # elif tool_name == "other_tool":
        #     self._open_other_tool_params_dialog()

    def _open_bin_hex_params_dialog(self):
        """打开 Bin to Hex 转换器参数对话框"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout

        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("Bin to Hex 转换器 - 默认参数")
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)
        form_layout = QFormLayout()

        # 数据宽度
        data_width_combo = QComboBox()
        data_width_combo.addItems(["1字节", "2字节", "4字节", "8字节"])
        data_width_map = {1: 0, 2: 1, 4: 2, 8: 3}
        current_width = self._get_tool_param("bin_hex_converter", "data_width", 1)
        data_width_combo.setCurrentIndex(data_width_map.get(current_width, 0))
        form_layout.addRow("数据宽度:", data_width_combo)

        # 每行字节数
        bytes_per_row_spin = QSpinBox()
        bytes_per_row_spin.setRange(1, 128)
        bytes_per_row_spin.setValue(self._get_tool_param("bin_hex_converter", "bytes_per_row", 16))
        bytes_per_row_spin.setSuffix(" Bytes")
        form_layout.addRow("每行字节数:", bytes_per_row_spin)

        # 字节序
        byteorder_combo = QComboBox()
        byteorder_combo.addItems(["小端 (Little-Endian)", "大端 (Big-Endian)"])
        current_byteorder = self._get_tool_param("bin_hex_converter", "byteorder", "little")
        byteorder_combo.setCurrentIndex(0 if current_byteorder == "little" else 1)
        form_layout.addRow("字节序:", byteorder_combo)

        # 大小写
        uppercase_check = QCheckBox("大写")
        uppercase_check.setChecked(self._get_tool_param("bin_hex_converter", "uppercase", True))
        form_layout.addRow("字母大小写:", uppercase_check)

        layout.addLayout(form_layout)

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            # 保存配置到临时属性
            if not hasattr(self, 'bin_hex_converter_params'):
                self.bin_hex_converter_params = {}

            width_map = {0: 1, 1: 2, 2: 4, 3: 8}
            self.bin_hex_converter_params = {
                'data_width': width_map[data_width_combo.currentIndex()],
                'bytes_per_row': bytes_per_row_spin.value(),
                'byteorder': 'little' if byteorder_combo.currentIndex() == 0 else 'big',
                'uppercase': uppercase_check.isChecked()
            }

    def _open_number_conversion_params_dialog(self):
        """打开进制转换器参数对话框"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout

        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("进制转换器 - 默认参数")
        dialog.setMinimumWidth(350)

        layout = QVBoxLayout(dialog)
        form_layout = QFormLayout()

        # 数据宽度
        data_width_combo = QComboBox()
        data_width_combo.addItems(["BYTE (8位)", "WORD (16位)", "DWORD (32位)", "QWORD (64位)"])
        width_map = {"BYTE": 0, "WORD": 1, "DWORD": 2, "QWORD": 3}
        current_width = self._get_tool_param("number_conversion_dialog", "data_width", "DWORD")
        data_width_combo.setCurrentIndex(width_map.get(current_width, 2))
        form_layout.addRow("数据宽度:", data_width_combo)

        layout.addLayout(form_layout)

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            # 保存配置到临时属性
            if not hasattr(self, 'number_conversion_dialog_params'):
                self.number_conversion_dialog_params = {}

            width_reverse_map = {0: "BYTE", 1: "WORD", 2: "DWORD", 3: "QWORD"}
            self.number_conversion_dialog_params = {
                'data_width': width_reverse_map[data_width_combo.currentIndex()]
            }

    def _open_firmware_downloader_params_dialog(self):
        """打开固件下载工具参数对话框"""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QTabWidget, QWidget,
                                      QGroupBox, QRadioButton, QButtonGroup)

        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("固件下载工具 - 默认参数")
        dialog.setMinimumWidth(700)

        layout = QVBoxLayout(dialog)

        # 创建标签页
        tab_widget = QTabWidget()

        # === 基本参数标签页 ===
        basic_tab = QWidget()
        basic_layout = QFormLayout(basic_tab)

        # 初始文件路径
        initial_file_layout = QHBoxLayout()
        initial_file_edit = QLineEdit()
        initial_file_edit.setText(self._get_tool_param("firmware_downloader", "initial_file", ""))
        initial_file_edit.setPlaceholderText("留空则每次手动选择")
        initial_file_layout.addWidget(initial_file_edit, 1)

        initial_file_browse_btn = QPushButton("浏览")
        initial_file_browse_btn.clicked.connect(lambda: self._browse_for_initial_file(initial_file_edit))
        initial_file_layout.addWidget(initial_file_browse_btn)
        basic_layout.addRow("初始固件文件:", initial_file_layout)

        # 包大小
        packet_size_spin = QSpinBox()
        packet_size_spin.setRange(16, 8192)
        packet_size_spin.setValue(self._get_tool_param("firmware_downloader", "packet_size", 256))
        packet_size_spin.setSuffix(" Bytes")
        basic_layout.addRow("包大小:", packet_size_spin)

        # 开始命令
        start_command_edit = QLineEdit()
        start_command_edit.setText(self._get_tool_param("firmware_downloader", "start_command", "download 0\\n"))
        basic_layout.addRow("开始命令:", start_command_edit)

        # 添加包 CRC
        add_packet_crc_check = QCheckBox("启用")
        add_packet_crc_check.setChecked(self._get_tool_param("firmware_downloader", "add_packet_crc", False))
        basic_layout.addRow("添加包CRC:", add_packet_crc_check)

        # 包 CRC 类型
        packet_crc_type_combo = QComboBox()
        packet_crc_type_combo.addItems(["CRC16-MODBUS", "CRC16-CCITT", "CRC32"])
        current_crc_type = self._get_tool_param("firmware_downloader", "packet_crc_type", "CRC16-MODBUS")
        packet_crc_type_combo.setCurrentText(current_crc_type)
        basic_layout.addRow("包CRC类型:", packet_crc_type_combo)

        tab_widget.addTab(basic_tab, "基本参数")

        # === 开始命令 ACK 标签页 ===
        start_ack_tab = QWidget()
        start_ack_layout = QFormLayout(start_ack_tab)

        # 等待开始 ACK
        wait_start_ack_check = QCheckBox("启用")
        wait_start_ack_check.setChecked(self._get_tool_param("firmware_downloader", "wait_start_ack", False))
        start_ack_layout.addRow("等待开始ACK:", wait_start_ack_check)

        # 检查模式
        start_ack_check_mode_combo = QComboBox()
        start_ack_check_mode_combo.addItems(["AND", "OR"])
        start_ack_check_mode_combo.setCurrentText(self._get_tool_param("firmware_downloader", "start_ack_check_mode", "AND"))
        start_ack_layout.addRow("检查模式:", start_ack_check_mode_combo)

        # 超时时间
        start_ack_timeout_spin = QSpinBox()
        start_ack_timeout_spin.setRange(100, 60000)
        start_ack_timeout_spin.setValue(self._get_tool_param("firmware_downloader", "start_ack_timeout", 1000))
        start_ack_timeout_spin.setSuffix(" ms")
        start_ack_layout.addRow("超时时间:", start_ack_timeout_spin)

        # 检查长度
        start_ack_check_length_check = QCheckBox("启用")
        start_ack_check_length_check.setChecked(self._get_tool_param("firmware_downloader", "start_ack_check_length", True))
        start_ack_layout.addRow("检查长度:", start_ack_check_length_check)

        # 期望长度
        start_ack_expected_length_spin = QSpinBox()
        start_ack_expected_length_spin.setRange(1, 1024)
        start_ack_expected_length_spin.setValue(self._get_tool_param("firmware_downloader", "start_ack_expected_length", 1))
        start_ack_layout.addRow("期望长度:", start_ack_expected_length_spin)

        # 检查数据
        start_ack_check_data_check = QCheckBox("启用")
        start_ack_check_data_check.setChecked(self._get_tool_param("firmware_downloader", "start_ack_check_data", False))
        start_ack_layout.addRow("检查数据:", start_ack_check_data_check)

        # 期望数据
        start_ack_expected_data_edit = QLineEdit()
        start_ack_expected_data_edit.setText(self._get_tool_param("firmware_downloader", "start_ack_expected_data", "0x06"))
        start_ack_layout.addRow("期望数据:", start_ack_expected_data_edit)

        # 数据格式
        start_ack_data_format_combo = QComboBox()
        start_ack_data_format_combo.addItems(["HEX", "ASCII"])
        start_ack_data_format_combo.setCurrentText(self._get_tool_param("firmware_downloader", "start_ack_data_format", "HEX"))
        start_ack_layout.addRow("数据格式:", start_ack_data_format_combo)

        tab_widget.addTab(start_ack_tab, "开始命令ACK")

        # === 数据包 ACK 标签页 ===
        packet_ack_tab = QWidget()
        packet_ack_layout = QFormLayout(packet_ack_tab)

        # 等待数据包 ACK
        wait_packet_ack_check = QCheckBox("启用")
        wait_packet_ack_check.setChecked(self._get_tool_param("firmware_downloader", "wait_packet_ack", False))
        packet_ack_layout.addRow("等待数据包ACK:", wait_packet_ack_check)

        # 检查模式
        packet_ack_check_mode_combo = QComboBox()
        packet_ack_check_mode_combo.addItems(["AND", "OR"])
        packet_ack_check_mode_combo.setCurrentText(self._get_tool_param("firmware_downloader", "packet_ack_check_mode", "AND"))
        packet_ack_layout.addRow("检查模式:", packet_ack_check_mode_combo)

        # 超时时间
        packet_ack_timeout_spin = QSpinBox()
        packet_ack_timeout_spin.setRange(100, 60000)
        packet_ack_timeout_spin.setValue(self._get_tool_param("firmware_downloader", "packet_ack_timeout", 1000))
        packet_ack_timeout_spin.setSuffix(" ms")
        packet_ack_layout.addRow("超时时间:", packet_ack_timeout_spin)

        # 检查长度
        packet_ack_check_length_check = QCheckBox("启用")
        packet_ack_check_length_check.setChecked(self._get_tool_param("firmware_downloader", "packet_ack_check_length", True))
        packet_ack_layout.addRow("检查长度:", packet_ack_check_length_check)

        # 期望长度
        packet_ack_expected_length_spin = QSpinBox()
        packet_ack_expected_length_spin.setRange(1, 1024)
        packet_ack_expected_length_spin.setValue(self._get_tool_param("firmware_downloader", "packet_ack_expected_length", 1))
        packet_ack_layout.addRow("期望长度:", packet_ack_expected_length_spin)

        # 检查数据
        packet_ack_check_data_check = QCheckBox("启用")
        packet_ack_check_data_check.setChecked(self._get_tool_param("firmware_downloader", "packet_ack_check_data", False))
        packet_ack_layout.addRow("检查数据:", packet_ack_check_data_check)

        # 期望数据
        packet_ack_expected_data_edit = QLineEdit()
        packet_ack_expected_data_edit.setText(self._get_tool_param("firmware_downloader", "packet_ack_expected_data", "0x06"))
        packet_ack_layout.addRow("期望数据:", packet_ack_expected_data_edit)

        # 数据格式
        packet_ack_data_format_combo = QComboBox()
        packet_ack_data_format_combo.addItems(["HEX", "ASCII"])
        packet_ack_data_format_combo.setCurrentText(self._get_tool_param("firmware_downloader", "packet_ack_data_format", "HEX"))
        packet_ack_layout.addRow("数据格式:", packet_ack_data_format_combo)

        # 检查 CRC
        packet_ack_check_crc_check = QCheckBox("启用")
        packet_ack_check_crc_check.setChecked(self._get_tool_param("firmware_downloader", "packet_ack_check_crc", False))
        packet_ack_layout.addRow("检查CRC:", packet_ack_check_crc_check)

        # CRC 类型
        packet_ack_crc_type_combo = QComboBox()
        packet_ack_crc_type_combo.addItems(["CRC16-MODBUS", "CRC16-CCITT", "CRC32"])
        packet_ack_crc_type_combo.setCurrentText(self._get_tool_param("firmware_downloader", "packet_ack_crc_type", "CRC16-MODBUS"))
        packet_ack_layout.addRow("CRC类型:", packet_ack_crc_type_combo)

        tab_widget.addTab(packet_ack_tab, "数据包ACK")

        # === 末尾数据包 ACK 标签页 ===
        last_packet_ack_tab = QWidget()
        last_packet_ack_layout = QFormLayout(last_packet_ack_tab)

        # 等待末尾数据包 ACK
        wait_last_packet_ack_check = QCheckBox("启用")
        wait_last_packet_ack_check.setChecked(self._get_tool_param("firmware_downloader", "wait_last_packet_ack", False))
        last_packet_ack_layout.addRow("等待末尾包ACK:", wait_last_packet_ack_check)

        # 检查模式
        last_packet_ack_check_mode_combo = QComboBox()
        last_packet_ack_check_mode_combo.addItems(["AND", "OR"])
        last_packet_ack_check_mode_combo.setCurrentText(self._get_tool_param("firmware_downloader", "last_packet_ack_check_mode", "AND"))
        last_packet_ack_layout.addRow("检查模式:", last_packet_ack_check_mode_combo)

        # 超时时间
        last_packet_ack_timeout_spin = QSpinBox()
        last_packet_ack_timeout_spin.setRange(100, 60000)
        last_packet_ack_timeout_spin.setValue(self._get_tool_param("firmware_downloader", "last_packet_ack_timeout", 5000))
        last_packet_ack_timeout_spin.setSuffix(" ms")
        last_packet_ack_layout.addRow("超时时间:", last_packet_ack_timeout_spin)

        # 检查长度
        last_packet_ack_check_length_check = QCheckBox("启用")
        last_packet_ack_check_length_check.setChecked(self._get_tool_param("firmware_downloader", "last_packet_ack_check_length", False))
        last_packet_ack_layout.addRow("检查长度:", last_packet_ack_check_length_check)

        # 期望长度
        last_packet_ack_expected_length_spin = QSpinBox()
        last_packet_ack_expected_length_spin.setRange(1, 1024)
        last_packet_ack_expected_length_spin.setValue(self._get_tool_param("firmware_downloader", "last_packet_ack_expected_length", 1))
        last_packet_ack_layout.addRow("期望长度:", last_packet_ack_expected_length_spin)

        # 检查数据
        last_packet_ack_check_data_check = QCheckBox("启用")
        last_packet_ack_check_data_check.setChecked(self._get_tool_param("firmware_downloader", "last_packet_ack_check_data", False))
        last_packet_ack_layout.addRow("检查数据:", last_packet_ack_check_data_check)

        # 期望数据
        last_packet_ack_expected_data_edit = QLineEdit()
        last_packet_ack_expected_data_edit.setText(self._get_tool_param("firmware_downloader", "last_packet_ack_expected_data", "END\\r\\n"))
        last_packet_ack_layout.addRow("期望数据:", last_packet_ack_expected_data_edit)

        # 数据格式
        last_packet_ack_data_format_combo = QComboBox()
        last_packet_ack_data_format_combo.addItems(["HEX", "ASCII"])
        last_packet_ack_data_format_combo.setCurrentText(self._get_tool_param("firmware_downloader", "last_packet_ack_data_format", "ASCII"))
        last_packet_ack_layout.addRow("数据格式:", last_packet_ack_data_format_combo)

        # 检查 CRC
        last_packet_ack_check_crc_check = QCheckBox("启用")
        last_packet_ack_check_crc_check.setChecked(self._get_tool_param("firmware_downloader", "last_packet_ack_check_crc", False))
        last_packet_ack_layout.addRow("检查CRC:", last_packet_ack_check_crc_check)

        # CRC 类型
        last_packet_ack_crc_type_combo = QComboBox()
        last_packet_ack_crc_type_combo.addItems(["CRC16-MODBUS", "CRC16-CCITT", "CRC32"])
        last_packet_ack_crc_type_combo.setCurrentText(self._get_tool_param("firmware_downloader", "last_packet_ack_crc_type", "CRC16-MODBUS"))
        last_packet_ack_layout.addRow("CRC类型:", last_packet_ack_crc_type_combo)

        tab_widget.addTab(last_packet_ack_tab, "末尾包ACK")

        # === 结尾字符串标签页 ===
        end_string_tab = QWidget()
        end_string_layout = QFormLayout(end_string_tab)

        # 发送结尾字符串
        send_end_string_check = QCheckBox("启用")
        send_end_string_check.setChecked(self._get_tool_param("firmware_downloader", "send_end_string", False))
        end_string_layout.addRow("发送结尾字符串:", send_end_string_check)

        # 结尾字符串
        end_string_edit = QLineEdit()
        end_string_edit.setText(self._get_tool_param("firmware_downloader", "end_string", "?\\r\\n"))
        end_string_layout.addRow("结尾字符串:", end_string_edit)

        tab_widget.addTab(end_string_tab, "结尾字符串")

        layout.addWidget(tab_widget)

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            # 保存配置到临时属性
            self.firmware_downloader_params = {
                # 初始文件
                'initial_file': initial_file_edit.text().strip(),
                # 基本参数
                'packet_size': packet_size_spin.value(),
                'start_command': start_command_edit.text().strip(),
                'add_packet_crc': add_packet_crc_check.isChecked(),
                'packet_crc_type': packet_crc_type_combo.currentText(),
                # 开始命令 ACK
                'wait_start_ack': wait_start_ack_check.isChecked(),
                'start_ack_timeout': start_ack_timeout_spin.value(),
                'start_ack_check_length': start_ack_check_length_check.isChecked(),
                'start_ack_expected_length': start_ack_expected_length_spin.value(),
                'start_ack_check_data': start_ack_check_data_check.isChecked(),
                'start_ack_expected_data': start_ack_expected_data_edit.text().strip(),
                'start_ack_data_format': start_ack_data_format_combo.currentText(),
                'start_ack_check_mode': start_ack_check_mode_combo.currentText(),
                # 数据包 ACK
                'wait_packet_ack': wait_packet_ack_check.isChecked(),
                'packet_ack_timeout': packet_ack_timeout_spin.value(),
                'packet_ack_check_length': packet_ack_check_length_check.isChecked(),
                'packet_ack_expected_length': packet_ack_expected_length_spin.value(),
                'packet_ack_check_data': packet_ack_check_data_check.isChecked(),
                'packet_ack_expected_data': packet_ack_expected_data_edit.text().strip(),
                'packet_ack_data_format': packet_ack_data_format_combo.currentText(),
                'packet_ack_check_crc': packet_ack_check_crc_check.isChecked(),
                'packet_ack_crc_type': packet_ack_crc_type_combo.currentText(),
                'packet_ack_check_mode': packet_ack_check_mode_combo.currentText(),
                # 末尾数据包 ACK
                'wait_last_packet_ack': wait_last_packet_ack_check.isChecked(),
                'last_packet_ack_timeout': last_packet_ack_timeout_spin.value(),
                'last_packet_ack_check_length': last_packet_ack_check_length_check.isChecked(),
                'last_packet_ack_expected_length': last_packet_ack_expected_length_spin.value(),
                'last_packet_ack_check_data': last_packet_ack_check_data_check.isChecked(),
                'last_packet_ack_expected_data': last_packet_ack_expected_data_edit.text().strip(),
                'last_packet_ack_data_format': last_packet_ack_data_format_combo.currentText(),
                'last_packet_ack_check_crc': last_packet_ack_check_crc_check.isChecked(),
                'last_packet_ack_crc_type': last_packet_ack_crc_type_combo.currentText(),
                'last_packet_ack_check_mode': last_packet_ack_check_mode_combo.currentText(),
                # 结尾字符串配置
                'send_end_string': send_end_string_check.isChecked(),
                'end_string': end_string_edit.text().strip()
            }


    def _browse_for_tool(self, tool_name, path_edit):
        """浏览文件以选择工具路径"""
        filename, _ = QFileDialog.getOpenFileName(
            self, f"选择 {tool_name}", "", "可执行文件 (*.exe);;Python文件 (*.py);;所有文件 (*)")
        if filename:
            path_edit.setText(filename)

    def _browse_for_initial_file(self, path_edit):
        """浏览文件以选择初始固件文件"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择固件文件", "", "固件文件 (*.bin *.hex *.fw);;所有文件 (*)")
        if filename:
            path_edit.setText(filename)

    def accept(self):
        """保存配置, 仅在有更改时更新（通用化）"""
        # 遍历所有已注册的工具，保存配置
        for tool_name in self.config_manager.get_all_tool_names():
            # 获取控件
            check_widget = getattr(self, f"{tool_name}_check", None)
            always_on_top_widget = getattr(self, f"{tool_name}_always_on_top_check", None)
            edit_widget = getattr(self, f"{tool_name}_edit", None)

            if not check_widget or not edit_widget:
                continue

            # 获取当前配置
            current_config = self.config_manager.get_tool_config(tool_name)
            current_path = current_config.get("path", "") if current_config else ""
            current_enabled = current_config.get("enabled", False) if current_config else False
            current_always_on_top = current_config.get("always_on_top", False) if current_config else False

            # 获取新配置
            new_path = edit_widget.text().strip()
            new_enabled = check_widget.isChecked()
            new_always_on_top = always_on_top_widget.isChecked() if always_on_top_widget else False

            # 准备额外参数
            extra_params = {}

            # 检查是否有工具特定的参数（从参数对话框）
            if tool_name == "bin_hex_converter" and hasattr(self, 'bin_hex_converter_params'):
                extra_params = self.bin_hex_converter_params
            elif tool_name == "number_conversion_dialog" and hasattr(self, 'number_conversion_dialog_params'):
                extra_params = self.number_conversion_dialog_params
            elif tool_name == "firmware_downloader" and hasattr(self, 'firmware_downloader_params'):
                extra_params = self.firmware_downloader_params
            else:
                # 从当前配置中保留额外参数
                if current_config:
                    for key in current_config.keys():
                        if key not in ['path', 'enabled', 'always_on_top']:
                            extra_params[key] = current_config[key]

            # 添加always_on_top到额外参数
            extra_params['always_on_top'] = new_always_on_top

            # 检查是否有变化
            has_changes = (current_path != new_path or current_enabled != new_enabled or current_always_on_top != new_always_on_top)

            # 检查额外参数是否有变化
            if current_config and extra_params:
                for key, value in extra_params.items():
                    if current_config.get(key) != value:
                        has_changes = True
                        break

            # 如果有变化则保存
            if has_changes:
                self.config_manager.set_tool_config(
                    tool_name,
                    new_enabled,
                    new_path,
                    **extra_params
                )

        super().accept()
