#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""接收区和发送编辑区共用的查找栏。"""

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QCheckBox, QLabel, QComboBox,
    QDialog, QVBoxLayout, QGroupBox, QRadioButton, QScrollArea,
    QDialogButtonBox
)

from core.text_search import MatchOptions


class SearchScopeDialog(QDialog):
    """发送编辑区的搜索区域和多模块范围选择。"""

    def __init__(self, modules, selected_modules, search_area, parent=None):
        super().__init__(parent)
        self.setWindowTitle("搜索范围")
        self.setMinimumWidth(340)
        self._updating_modules = False
        modules = tuple(modules)
        selected_modules = set(selected_modules) & set(modules)

        layout = QVBoxLayout(self)

        area_group = QGroupBox("搜索区域")
        area_layout = QHBoxLayout(area_group)
        self.area_buttons = {}
        for area in ("字符串", "备注", "一起搜索"):
            button = QRadioButton(area)
            button.setChecked(area == search_area)
            self.area_buttons[area] = button
            area_layout.addWidget(button)
        layout.addWidget(area_group)

        module_group = QGroupBox("模块范围（可多选）")
        module_layout = QVBoxLayout(module_group)
        self.module_scroll = QScrollArea()
        self.module_scroll.setWidgetResizable(True)
        self.module_scroll.setFixedHeight(190)
        module_list = QWidget()
        module_list_layout = QVBoxLayout(module_list)
        module_list_layout.setContentsMargins(6, 6, 6, 6)

        self.all_modules_check = QCheckBox("全部模块")
        self.all_modules_check.setChecked(not selected_modules)
        module_list_layout.addWidget(self.all_modules_check)

        self.module_checks = {}
        for module in modules:
            checkbox = QCheckBox(module)
            checkbox.setChecked(module in selected_modules)
            checkbox.toggled.connect(self._on_module_toggled)
            self.module_checks[module] = checkbox
            module_list_layout.addWidget(checkbox)
        module_list_layout.addStretch()

        self.all_modules_check.toggled.connect(self._on_all_modules_toggled)
        self.module_scroll.setWidget(module_list)
        module_layout.addWidget(self.module_scroll)
        layout.addWidget(module_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_all_modules_toggled(self, checked):
        if self._updating_modules or not checked:
            return
        self._updating_modules = True
        for checkbox in self.module_checks.values():
            checkbox.setChecked(False)
        self._updating_modules = False

    def _on_module_toggled(self, checked):
        if self._updating_modules:
            return
        self._updating_modules = True
        if checked:
            self.all_modules_check.setChecked(False)
        elif not any(checkbox.isChecked() for checkbox in self.module_checks.values()):
            self.all_modules_check.setChecked(True)
        self._updating_modules = False

    def selected_modules(self):
        if self.all_modules_check.isChecked():
            return ()
        selected = tuple(
            module for module, checkbox in self.module_checks.items()
            if checkbox.isChecked()
        )
        return selected or ()

    def search_area(self):
        for area, button in self.area_buttons.items():
            if button.isChecked():
                return area
        return "字符串"


class FindBar(QWidget):
    SEARCH_AREA_COMMAND = "字符串"
    SEARCH_AREA_COMMENT = "备注"
    SEARCH_AREA_BOTH = "一起搜索"

    searchChanged = pyqtSignal(str, object, object)
    navigateRequested = pyqtSignal(int)
    closed = pyqtSignal()

    def __init__(self, show_scope=False, parent=None):
        super().__init__(parent)
        self.show_scope = show_scope
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(5)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("查找")
        self.search_input.setClearButtonEnabled(True)
        layout.addWidget(self.search_input, 1)

        self.scope_combo = QComboBox(self)
        self.scope_combo.addItem("全部")
        self.scope_combo.hide()
        self._selected_scopes = set()
        if show_scope:
            self.scope_button = QPushButton("范围")
            self.scope_button.setFixedWidth(54)
            self.scope_button.clicked.connect(self._show_scope_dialog)
            layout.addWidget(self.scope_button)
        else:
            self.scope_button = None
        self._search_area = self.SEARCH_AREA_COMMAND

        self.case_check = QCheckBox("Aa")
        self.case_check.setToolTip("区分大小写")
        self.regex_check = QCheckBox(".*")
        self.regex_check.setToolTip("使用正则表达式")
        self.whole_check = QCheckBox("全词")
        self.whole_check.setToolTip("全字符匹配（完整单词）")
        layout.addWidget(self.case_check)
        layout.addWidget(self.regex_check)
        layout.addWidget(self.whole_check)

        self.result_label = QLabel("0/0")
        self.result_label.setMinimumWidth(54)
        self.result_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.result_label)

        self.previous_btn = QPushButton("↑")
        self.previous_btn.setFixedWidth(30)
        self.previous_btn.setToolTip("上一个 (Shift+Enter)")
        self.next_btn = QPushButton("↓")
        self.next_btn.setFixedWidth(30)
        self.next_btn.setToolTip("下一个 (Enter)")
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedWidth(30)
        self.close_btn.setToolTip("关闭 (Esc)")
        layout.addWidget(self.previous_btn)
        layout.addWidget(self.next_btn)
        layout.addWidget(self.close_btn)

        self.search_input.textChanged.connect(self._emit_search)
        self.case_check.toggled.connect(self._emit_search)
        self.regex_check.toggled.connect(self._emit_search)
        self.whole_check.toggled.connect(self._emit_search)
        self.search_input.returnPressed.connect(lambda: self.navigateRequested.emit(1))
        self.previous_btn.clicked.connect(lambda: self.navigateRequested.emit(-1))
        self.next_btn.clicked.connect(lambda: self.navigateRequested.emit(1))
        self.close_btn.clicked.connect(self.close_bar)
        self._update_scope_tooltip()

    def options(self):
        return MatchOptions(
            case_sensitive=self.case_check.isChecked(),
            use_regex=self.regex_check.isChecked(),
            whole_word=self.whole_check.isChecked(),
        )

    def _emit_search(self, *_args):
        self.searchChanged.emit(
            self.search_input.text(),
            self.options(),
            self.selected_scopes() if self.show_scope else "全部",
        )

    def search_area(self):
        return self._search_area

    def set_search_area(self, area):
        if area not in (
            self.SEARCH_AREA_COMMAND,
            self.SEARCH_AREA_COMMENT,
            self.SEARCH_AREA_BOTH,
        ):
            return
        if self._search_area == area:
            return
        self._search_area = area
        self._update_scope_tooltip()
        self._emit_search()

    def set_scope(self, scope):
        if scope == "全部":
            self.set_selected_scopes(())
        elif self.scope_combo.findText(scope) >= 0:
            self.set_selected_scopes((scope,))

    def selected_scopes(self):
        return tuple(
            self.scope_combo.itemText(index)
            for index in range(1, self.scope_combo.count())
            if self.scope_combo.itemText(index) in self._selected_scopes
        )

    def set_selected_scopes(self, scopes):
        valid_scopes = {
            self.scope_combo.itemText(index)
            for index in range(1, self.scope_combo.count())
        }
        selected = {scope for scope in scopes if scope in valid_scopes}
        if selected == self._selected_scopes:
            return
        self._selected_scopes = selected
        self._update_scope_tooltip()
        self._emit_search()

    def _update_scope_tooltip(self):
        if not self.scope_button:
            return
        selected_scopes = self.selected_scopes()
        if not selected_scopes:
            module_text = "全部模块"
        elif len(selected_scopes) <= 3:
            module_text = "、".join(selected_scopes)
        else:
            module_text = f"已选 {len(selected_scopes)} 个模块"
        self.scope_button.setToolTip(
            f"模块范围: {module_text}\n"
            f"搜索区域: {self._search_area}"
        )

    def _show_scope_dialog(self):
        modules = [
            self.scope_combo.itemText(index)
            for index in range(1, self.scope_combo.count())
        ]
        dialog = SearchScopeDialog(
            modules,
            self.selected_scopes(),
            self._search_area,
            self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return

        selected_scopes = set(dialog.selected_modules())
        search_area = dialog.search_area()
        if selected_scopes == self._selected_scopes and search_area == self._search_area:
            return
        self._selected_scopes = selected_scopes
        self._search_area = search_area
        self._update_scope_tooltip()
        self._emit_search()

    def set_scopes(self, scopes):
        if not self.show_scope:
            return
        current_scopes = set(self.selected_scopes())
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("全部")
        self.scope_combo.addItems([scope for scope in scopes if scope != "全部"])
        self.scope_combo.blockSignals(False)
        available_scopes = {
            self.scope_combo.itemText(index)
            for index in range(1, self.scope_combo.count())
        }
        self._selected_scopes = current_scopes & available_scopes
        self._update_scope_tooltip()
        self._emit_search()

    def show_and_focus(self, selected_text=""):
        self.show()
        if selected_text:
            self.search_input.setText(selected_text)
        self.search_input.setFocus()
        self.search_input.selectAll()
        self._emit_search()

    def set_result(self, current, total, error=""):
        if error:
            self.result_label.setText("错误")
            self.result_label.setToolTip(error)
            self.search_input.setStyleSheet("border: 1px solid #f44336;")
            return
        self.search_input.setStyleSheet("")
        self.result_label.setToolTip("")
        self.result_label.setText(f"{current}/{total}" if total else "0/0")

    def close_bar(self):
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_bar()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ShiftModifier:
            self.navigateRequested.emit(-1)
            return
        super().keyPressEvent(event)
