# 命令行使用指南 (Command Line Usage)

本文档介绍如何通过命令行启动和使用 GHowe 串口调试助手。

---

## 目录

- [基础使用](#基础使用)
- [启动方式](#启动方式)
- [命令行参数](#命令行参数)
- [配置文件](#配置文件)
- [打包与分发](#打包与分发)
- [开发环境](#开发环境)

---

## 基础使用

### 使用 Python 运行 (开发环境)

#### 方式一: 模块方式运行 (推荐)

```bash
# 在项目根目录的上级目录运行
python -m serial_tool_project.main
```

**优点**:
- 正确处理模块导入路径
- 支持相对导入
- 推荐用于开发和测试

#### 方式二: 直接运行脚本

```bash
# 进入项目目录
cd serial_tool_project

# 运行主脚本
python main.py
```

**优点**:
- 简单直接
- 适合快速启动

### 使用可执行文件运行 (生产环境)

#### Windows

```bash
# 直接双击 .exe 文件
GHowe串口助手.exe

# 或在命令行运行
.\GHowe串口助手.exe
```

#### Linux

```bash
# 添加执行权限
chmod +x GHowe串口助手

# 运行
./GHowe串口助手
```

#### macOS

```bash
# 添加执行权限
chmod +x GHowe串口助手

# 运行
./GHowe串口助手
```

---

## 启动方式

### 快速启动

```bash
# 使用默认配置启动
python -m serial_tool_project.main
```

### 指定配置文件启动

配置文件名称由可执行文件名自动决定:

```python
# 示例: main.py -> main_config.json
# 示例: GHowe串口助手.exe -> GHowe串口助手_config.json
```

**配置文件查找规则**:
1. 首先在当前工作目录查找
2. 如果不存在，使用默认配置

### 自定义可执行文件名

通过修改可执行文件名来改变配置文件名:

```bash
# Windows
ren GHowe串口助手.exe MySerialTool.exe
# 配置文件将使用: MySerialTool_config.json

# Linux/macOS
mv GHowe串口助手 MySerialTool
# 配置文件将使用: MySerialTool_config.json
```

---

## 命令行参数

当前版本不支持命令行参数，所有配置通过配置文件或 GUI 界面管理。

### 未来计划支持的参数

```bash
# 指定配置文件 (计划中)
python -m serial_tool_project.main --config custom_config.json

# 指定串口 (计划中)
python -m serial_tool_project.main --port COM3 --baud 115200

# 自动执行命令文件 (计划中)
python -m serial_tool_project.main --run commands.csv

# 静默模式 (计划中)
python -m serial_tool_project.main --silent
```

---

## 配置文件

### 配置文件位置

配置文件位于可执行文件所在目录:

```
当前工作目录/
├── GHowe串口助手.exe (或 main.py)
└── GHowe串口助手_config.json (或 main_config.json)
```

### 配置文件结构

```json
{
  "tool_version": "1.1.1",
  "tool_update_time": "2025-12-02 00:00:00",
  "config_last_updated": "2026-01-22 10:00:00",
  "tools": {
    "number_conversion_dialog": {
      "enabled": true,
      "path": "",
      "data_width": "DWORD",
      "always_on_top": false
    }
  },
  "last_used_directory": "D:/Projects/",
  "last_state": {
    "basic_settings": {
      "port": "COM3",
      "baudrate": 115200,
      "databits": 8,
      "parity": "None",
      "stopbits": 1
    },
    "receive_settings": {
      "auto_scroll": true,
      "show_timestamp": true,
      "pause_display": false
    },
    "send_settings": {
      "ending": "\\r\\n",
      "continuous_interval": 100,
      "loop_interval": 1000,
      "loop_send": false
    },
    "ui_settings": {
      "window_geometry": [100, 100, 1400, 800],
      "splitter_sizes": [400, 1000]
    },
    "commands": [
      {
        "checked": true,
        "command": "AT+CMGF=1",
        "comment": "设置文本模式"
      }
    ]
  }
}
```

### 手动编辑配置文件

#### 注意事项
- 关闭应用后再编辑配置文件
- 使用 UTF-8 编码保存
- 确保 JSON 格式正确 (可用在线工具验证)
- 编辑前建议备份

#### 常见配置修改

**修改默认串口**:
```json
"basic_settings": {
  "port": "COM5",  // 修改为目标端口
  "baudrate": 9600  // 修改波特率
}
```

**修改工具路径**:
```json
"tools": {
  "number_conversion_dialog": {
    "path": "D:/Tools/Calculator.exe",
    "enabled": true
  }
}
```

**清空命令历史**:
```json
"commands": []  // 设置为空数组
```

### 配置文件迁移

#### 导出配置
```bash
# 复制配置文件
cp main_config.json backup_config.json

# 或压缩备份
zip config_backup.zip main_config.json
```

#### 导入配置
```bash
# 恢复配置文件
cp backup_config.json main_config.json

# 或从压缩包恢复
unzip config_backup.zip
```

### 重置配置

#### 方式一: 删除配置文件
```bash
# Windows
del main_config.json

# Linux/macOS
rm main_config.json
```

#### 方式二: 重命名配置文件
```bash
# Windows
ren main_config.json main_config.json.bak

# Linux/macOS
mv main_config.json main_config.json.bak
```

重启应用后将使用默认配置。

---

## 打包与分发

### 使用 PyInstaller 打包

#### 基础打包命令

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包成单个可执行文件
pyinstaller --onefile --windowed --icon=resources/HOWE_LOGO.ico main.py
```

#### 使用 spec 文件打包 (推荐)

```bash
# 首次生成 spec 文件
pyi-makespec --onefile --windowed --icon=resources/HOWE_LOGO.ico main.py

# 编辑 main.spec 文件 (参考项目中的 main.spec)

# 使用 spec 文件打包
pyinstaller main.spec
```

### 打包选项说明

| 选项 | 说明 | 推荐 |
|------|------|------|
| `--onefile` | 打包成单个可执行文件 | ✅ 推荐 |
| `--windowed` | 隐藏控制台窗口 (GUI 应用) | ✅ 推荐 |
| `--icon` | 设置应用图标 | ✅ 推荐 |
| `--add-data` | 添加资源文件 | 按需使用 |
| `--name` | 指定可执行文件名 | 按需使用 |
| `--upx` | 使用 UPX 压缩 | 可选 |
| `--clean` | 清理临时文件 | 推荐 |

### spec 文件配置示例

```python
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('resources/HOWE_LOGO.ico', 'resources')],  # 资源文件
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GHowe串口助手',  # 可执行文件名
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # 启用 UPX 压缩
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 隐藏控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/HOWE_LOGO.ico'  # 应用图标
)
```

### 打包后目录结构

```
dist/
├── GHowe串口助手.exe  (可执行文件)
└── (运行时自动生成配置文件)

build/  (构建临时文件，可删除)
```

### 分发应用

#### Windows

```bash
# 方式一: 直接分发 .exe 文件
# 用户双击运行即可

# 方式二: 制作安装包 (使用 NSIS 或 Inno Setup)
# 可包含快捷方式、卸载程序等
```

#### Linux

```bash
# 打包成 .deb 包 (Debian/Ubuntu)
dpkg-deb --build package_directory

# 打包成 .rpm 包 (Red Hat/Fedora)
rpmbuild -ba package.spec

# 或直接分发可执行文件
tar -czf GHowe串口助手.tar.gz GHowe串口助手
```

#### macOS

```bash
# 创建 .app 包
# 或打包成 .dmg 镜像文件
hdiutil create -volname "GHowe串口助手" -srcfolder dist -ov -format UDZO GHowe串口助手.dmg
```

### 使用 GitHub Actions 自动发布

项目支持通过 Git tag 自动打包并发布 Windows exe。

#### 触发条件

推送形如 `v1.1.5` 的版本标签。

#### 自动执行内容

- 校验 `README.md` 版本号
- 校验 `main.py` 中的 `TOOL_VERSION`
- 使用 `main.spec` 执行 PyInstaller 打包
- 创建 GitHub Release
- 上传 exe 到 Release

#### 发布命令示例

```bash
git add .
git commit -m "release: v1.1.5"
git tag v1.1.5
git push origin master
git push origin v1.1.5
```

#### 常见失败原因

- tag 不是 `vX.Y.Z` 格式
- README 版本与 tag 不一致
- `TOOL_VERSION` 与 tag 不一致
- PyInstaller 打包失败
- exe 未生成

---

## 开发环境

### 环境要求

- **Python**: 3.6+
- **操作系统**: Windows / Linux / macOS

### 依赖安装

```bash
# 安装核心依赖
pip install PyQt5 pyserial

# 或使用 requirements.txt (如果提供)
pip install -r requirements.txt
```

### requirements.txt 示例

```
PyQt5>=5.15.0
pyserial>=3.5
```

### 开发模式运行

```bash
# 克隆项目
git clone <repository_url>
cd serial_tool_project

# 安装依赖
pip install -r requirements.txt

# 运行应用
python -m serial_tool_project.main
```

### 开发环境变量

#### BUNDLE_CALC (计划中)

控制是否打包进制转换器:

```bash
# Windows
set BUNDLE_CALC=1
pyinstaller main.spec

# Linux/macOS
export BUNDLE_CALC=1
pyinstaller main.spec
```

---

## 故障排查

### 导入错误

**错误**: `ModuleNotFoundError: No module named 'PyQt5'`

**解决**:
```bash
pip install PyQt5
```

### 串口权限错误 (Linux/macOS)

**错误**: `PermissionError: [Errno 13] Permission denied: '/dev/ttyUSB0'`

**解决**:
```bash
# 方式一: 添加到 dialout 组
sudo usermod -a -G dialout $USER
# 注销后重新登录

# 方式二: 临时授权
sudo chmod 666 /dev/ttyUSB0
```

### 配置文件损坏

**错误**: `json.decoder.JSONDecodeError`

**解决**:
```bash
# 删除配置文件，使用默认配置
rm main_config.json
```

### 打包失败

**错误**: `FileNotFoundError: resources/HOWE_LOGO.ico`

**解决**:
```bash
# 确保资源文件存在
ls resources/HOWE_LOGO.ico

# 或修改 spec 文件，移除图标配置
```

---

## 高级用法

### 多实例运行

支持同时运行多个实例:

```bash
# 实例 1
python -m serial_tool_project.main

# 实例 2 (使用不同端口)
python -m serial_tool_project.main
```

注意: 不同实例不能同时占用同一个串口。

### 自定义配置文件 (计划中)

```bash
# 使用自定义配置文件名
python -m serial_tool_project.main --config my_config.json
```

### 批量命令执行 (计划中)

```bash
# 启动后自动加载并执行命令
python -m serial_tool_project.main --run commands.csv --auto-start
```

---

## 脚本示例

### Windows 批处理脚本

**启动脚本 (start.bat)**:
```batch
@echo off
echo Starting GHowe Serial Tool...
python -m serial_tool_project.main
pause
```

**快速启动 (quick_start.bat)**:
```batch
@echo off
cd /d %~dp0
python main.py
```

### Linux/macOS Shell 脚本

**启动脚本 (start.sh)**:
```bash
#!/bin/bash
echo "Starting GHowe Serial Tool..."
python3 -m serial_tool_project.main
```

**添加执行权限**:
```bash
chmod +x start.sh
./start.sh
```

---

## 参考资料

- [Python 官方文档](https://docs.python.org/3/)
- [PyQt5 官方文档](https://www.riverbankcomputing.com/static/Docs/PyQt5/)
- [PySerial 官方文档](https://pyserial.readthedocs.io/)
- [PyInstaller 官方文档](https://pyinstaller.org/)

---

<p align="center">
  <i>更多使用技巧请参考主 README 文档</i>
</p>
