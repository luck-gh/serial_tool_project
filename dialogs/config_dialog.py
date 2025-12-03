from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QDialogButtonBox, QFileDialog, QGroupBox, QCheckBox)
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

        # 进制转化器
        self.add_tool_path_selector(tools_layout, "number_conversion_dialog", "进制转化器:")

        layout.addWidget(tools_group)

        # 对话框按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def add_tool_path_selector(self, layout, tool_name, label_text):
        """添加一个工具路径选择器"""
        h_layout = QHBoxLayout()
        
        # 获取当前工具配置
        tool_config = self.config_manager.get_tool_config(tool_name)
        current_path = tool_config.get("path", "") if tool_config else ""
        is_enabled = tool_config.get("enabled", True) if tool_config else True

        # 创建控件
        enable_check = QCheckBox(label_text)
        enable_check.setChecked(is_enabled)
        
        path_edit = QLineEdit()
        path_edit.setText(current_path)
        
        browse_btn = QPushButton("浏览...")

        # 布局
        h_layout.addWidget(enable_check)
        h_layout.addWidget(path_edit)
        h_layout.addWidget(browse_btn)
        layout.addLayout(h_layout)

        # 连接信号
        browse_btn.clicked.connect(lambda: self.browse_for_tool(tool_name, path_edit))
        
        # 将控件保存为实例属性，以便在accept时访问
        setattr(self, f"{tool_name}_check", enable_check)
        setattr(self, f"{tool_name}_edit", path_edit)

    def browse_for_tool(self, tool_name, path_edit):
        """浏览文件以选择工具路径"""
        filename, _ = QFileDialog.getOpenFileName(
            self, f"选择 {tool_name}", "", "Executable Files (*.exe);;All Files (*)")
        if filename:
            path_edit.setText(filename)

    def accept(self):
        """保存配置, 仅在有更改时更新"""
        tool_name = "number_conversion_dialog"
        
        # 获取当前配置
        current_config = self.config_manager.get_tool_config(tool_name)
        current_path = current_config.get("path", "") if current_config else ""
        current_enabled = current_config.get("enabled", True) if current_config else True

        # 获取新配置
        new_path = self.number_conversion_dialog_edit.text()
        new_enabled = self.number_conversion_dialog_check.isChecked()

        # 仅在配置更改时保存
        if current_path != new_path or current_enabled != new_enabled:
            self.config_manager.set_tool_config(tool_name, new_enabled, new_path)
        
        super().accept()
