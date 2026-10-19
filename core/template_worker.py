"""模板文件工作线程:只读写纯数据快照,不访问窗口或活动配置字典."""
from PyQt5.QtCore import QThread, pyqtSignal

from core.command_templates import load_template, export_template, CommandRowsTextParser
from core.command_executor import build_module_index


class TemplateWorker(QThread):
    """为 GUI 模板流程执行读取,导出或原子配置保存,结束后才消费 result.

    Args:
        operation: read,export 或 save-config.
        path: 绝对模板文件路径.
        rows: 导出用独立命令行快照,不包含 QWidget.
        manager: save-config 使用的 ConfigManager 副本,config 为独立候选数据.
        plain_text: GUI 用户明确选择按纯文本导入时才启用.
        parent: 存活至 QThread.finished 的 Qt 所有者.
    """
    progress = pyqtSignal(int)

    def __init__(self, operation, path, rows=None, manager=None, plain_text=False, parent=None):
        super().__init__(parent)
        self.operation, self.path = operation, path
        self.rows, self.manager, self.plain_text = rows, manager, plain_text
        self.result = None
        self.error = None

    def run(self):
        """线程内完成纯数据 IO,取消不提交导出临时文件,不操作串口."""
        try:
            if self.isInterruptionRequested():
                raise InterruptedError("模板操作已取消")
            if self.operation == "read":
                if self.plain_text:
                    with open(self.path, encoding="utf-8-sig", newline="") as stream:
                        self.result = CommandRowsTextParser.parse_plain_text_rows(stream.read())
                else:
                    self.result = load_template(self.path)
                self.result = (self.result, build_module_index(self.result))
            elif self.operation == "export":
                def advance(index):
                    if index % 100 == 0 or index == len(self.rows):
                        self.progress.emit(index)
                    return not self.isInterruptionRequested()
                export_template(self.path, self.rows, progress=advance)
                self.result = len(self.rows)
            elif self.operation == "save-config":
                # 仅副本持有候选字典;真正的活动配置管理器 在 GUI 提交阶段才更新.
                self.manager.save_config()
                self.result = self.manager.config
            else:
                raise ValueError("未知模板工作阶段")
        except Exception as exc:
            self.error = exc
