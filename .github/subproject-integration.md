
# 子工程集成指南

## 概述

本项目包含两个主要部分：
- **主工程 (serial_tool_project)**: 串口工具主应用，支持集成子工程模块。
- **模块工程 (number_converter_project)**: 进制转换器模块，可独立运行、打包，或作为子工程被主工程集成。

模块工程支持独立使用和打包，主工程可以选择包含或不包含模块工程。集成基于 PyInstaller 的打包配置，通过环境变量和目录检测实现动态集成。

## 模块工程设计约定 (number_converter_project)

### 关键文件
- **入口/运行**: [number_conversion_dialog.py](number_conversion_dialog.py)
- **打包配置**: [number_conversion_dialog.spec](number_conversion_dialog.spec)
- **自定义控件**: [widgets/widgets.py](widgets/widgets.py)
- **资源定位工具**: [widgets/utils.py](widgets/utils.py)
- **项目说明**: [README.md](README.md)

### 重要规则与约定

- **导入兼容**: `number_conversion_dialog.py` 支持作为包内模块导入和独立运行。保持多重 import-fallback 逻辑（相对导入、包内导入、绝对导入），确保两种模式兼容。

- **资源加载**: 使用 `widgets/utils.py::resource_path()` 获取资源路径，支持开发和打包环境差异。

- **自定义控件兼容**: `ExpandingTextEdit` 提供 `.setText()` 和 `.text()` 方法兼容 `QLineEdit`。修改 API 时维护兼容性。

- **防循环更新**: 使用 `self.updating` 标志包裹批量更新逻辑，避免递归更新。

- **位映射与顺序**: 位键盘使用 64 位数组，高位在前。位到整数转换遵循 `1 << (63 - i)` 权重。

- **数据宽度与有符号处理**: 支持 BYTE/WORD/DWORD/QWORD，有符号显示由 `self.is_signed` 控制。

### 打包与运行
- 开发运行: `python number_conversion_dialog.py [初始值] [HEX|DEC]`
- 打包: `pyinstaller --onefile --windowed --icon="resources/HOWE_LOGO.ico" --add-data="resources;resources" number_conversion_dialog.py`

## 主工程集成机制 (serial_tool_project)

### 目录结构要求
- 子工程位于主工程同级目录，如 `../number_converter_project/`
- 目录名与包名一致

### 自动检测逻辑
- 优先级: 环境变量 > 目录存在检测
- 环境变量值: `true` 强制打包, `false` 强制不打包, 未设置则自动检测

### 文件收集规则
- **包含**: Python 文件、资源文件、配置文件
- **排除**: `dist/`, `build/`, `__pycache__/`, `.git/`, `.idea/`, `.vscode/`, `*.spec`, `*.pyc`

### 添加新子工程步骤

1. **准备子工程**: 确保目录位于 `../your_subproject/`, 提供主入口模块
2. **修改 main.spec**: 添加检测和文件收集逻辑
3. **主应用集成**: 使用动态导入添加入口点
4. **更新配置**: 在 ConfigManager 中添加配置项

### 示例 spec 配置
```python
subproject_dir = os.path.abspath(os.path.join('..', 'your_subproject'))
bundle_env = os.environ.get('BUNDLE_SUBPROJECT', '').lower().strip()
bundle = bundle_env == 'true' if bundle_env else os.path.exists(subproject_dir)

if bundle:
    pathex.append(os.path.dirname(spec_dir))
    hiddenimports.append('your_subproject.main_module')
    # 文件收集逻辑...
```

### 主应用集成示例
```python
try:
    from your_subproject.main_dialog import YourDialog
    # 显示对话框
except ImportError:
    QMessageBox.warning(self, "警告", "子工程未集成")
```

## 环境变量

| 变量名 | 描述 | 默认值 |
|--------|------|--------|
| `BUNDLE_CALC` | 控制进制转换器打包 | 目录存在时为 true |
| `BUNDLE_SUBPROJECT` | 控制其他子工程打包 | 目录存在时为 true |

## 注意事项

### 依赖管理
- 子工程依赖需与主工程兼容
- 避免版本冲突，使用虚拟环境

### 资源路径
- 使用相对路径和 `resource_path()` 函数
- 正确映射资源文件到 `datas`

### 命名空间
- 确保包名唯一，避免冲突

### 调试与测试
- 单独测试子工程
- 验证打包后集成
- 检查导入和路径问题

### 版本控制
- 子工程独立版本管理
- 记录集成版本，考虑 Git 子模块

## 给 AI/自动化工具的指引

- 保持 import-fallback 逻辑，重构时提供兼容层
- 修改控件 API 时更新兼容方法
- 使用 `self.updating` 标志进行批量更新
- 修改位映射或宽度逻辑时更新相关函数

## 贡献者检查清单

- 是否改变导入/运行模式？更新兼容策略和 README
- 是否改动资源位置？确认 `resource_path()` 使用
- 是否引入新控件 API？更新兼容方法
- 是否更改位权或宽度？提供测试用例

## 故障排除

### 常见问题
- **导入错误**: 检查 `hiddenimports`
- **资源缺失**: 验证 `datas` 路径
- **路径问题**: 使用 `resource_path()`
- **版本冲突**: 检查依赖兼容性

### 调试步骤
1. 单独运行子工程验证功能
2. 检查 PyInstaller 日志输出
3. 验证打包后的文件结构
4. 测试动态导入机制

## 附：模块引用与兼容性检查（简要）

为方便维护与自动化工具参考，下面是对 `serial_tool_project` 与 `number_converter_project` 之间实际引用与兼容实现的总结、风险点与建议。

### 发现的关键实现
- `serial_tool_project/widgets/base_widgets.py` 中的 `open_bit_calculator()` 实现了三阶段启动策略：
  1. 使用 `ConfigManager` 中用户配置的外部路径启动（支持 `.py`）；
  2. 若失败，检测打包环境（`sys.frozen` / `sys._MEIPASS`），或在开发环境中向上查找 `number_converter_project` 并将其父目录加入 `sys.path`，然后动态导入：优先 `from number_converter_project.number_conversion_dialog import NumberConversionDialog`，失败后退回 `from number_conversion_dialog import NumberConversionDialog`；
  3. 导入失败时写入调试信息到 `output_manager`。

- 资源加载函数 `resource_path()` 在两个工程均有实现（`serial_tool_project/utils/ui_utils.py` 与 `number_converter_project/widgets/utils.py`），均兼容 PyInstaller 的 `sys._MEIPASS`。

- `number_conversion_dialog.py` 自身包含多层 import-fallback，保证作为包或独立脚本运行时均可找到 `ExpandingTextEdit` 与 `resource_path()`。

### 主要风险点

1. 同名控件/模块：`ExpandingTextEdit` 在两个工程中重复定义，可能在不同导入路径下导致不一致实现。
2. 重复的 `resource_path()` 实现增加维护成本；若修改逻辑则需同步两处。
3. 动态修改 `sys.path` 可能引起命名空间遮蔽（当 import_root 包含与主工程冲突的包名时）。
4. 打包时必须在 `spec` 中显式添加 `pathex`/`datas`/`hiddenimports`，否则运行时仍会出现 ImportError 或资源缺失。

### 推荐的保留/改进实现（要点）

- 动态导入模块时保持三步优先级：`ConfigManager.path` -> 环境变量/打包判断 -> 同级目录自动发现。示例模板：

```python
import os, sys

def import_number_converter(import_hint_dir=None):
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', None)
        if base and base not in sys.path:
            sys.path.insert(0, base)
    else:
        if import_hint_dir and os.path.isdir(import_hint_dir) and import_hint_dir not in sys.path:
            sys.path.insert(0, import_hint_dir)

    try:
        from number_converter_project.number_conversion_dialog import NumberConversionDialog
        return NumberConversionDialog
    except ImportError:
        from number_conversion_dialog import NumberConversionDialog
        return NumberConversionDialog
```

- 将 `resource_path()` 抽取为共享模块并在两个工程引用同一实现以减少维护成本。
- 保留 `ExpandingTextEdit` 与 `setText()`/`text()` 的兼容方法，或将其抽为共享控件。
- 在 `main.spec` 中显式添加子工程到 `pathex` 与 `datas`，并列出 `hiddenimports`。

详细分析已记录在仓库根目录的 `module-reference-report.md`（如需完整报告可打开该文件）。

## 结束语

此文档总结了主工程与模块工程的集成机制。开发者在修改时请遵循上述约定，确保独立运行、打包和集成兼容性。如有疑问，可参考具体工程的 README 或联系维护者。
