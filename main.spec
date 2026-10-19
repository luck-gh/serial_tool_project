# -*- mode: python ; coding: utf-8 -*-
"""
# pyinstaller main.spec
"""
"""
串口工具 PyInstaller 打包配置
同时打包兼容版, GUI 和 CLI 三个可执行文件
"""
import os
import sys
from fnmatch import fnmatch
from importlib.util import find_spec

# 获取当前工作目录
spec_dir = os.getcwd()

# 缺依赖时终止,避免生成表面成功,运行控制台才缺模块的 EXE.
missing = [name for name in ('PyQt5', 'serial', 'jsonschema', 'prompt_toolkit')
           if find_spec(name) is None]
if missing:
    # PowerShell 单引号内的单引号须成对转义,路径含空格时仍可直接复制执行.
    python_path = sys.executable.replace("'", "''")
    requirements_path = os.path.join(spec_dir, 'requirements.txt').replace("'", "''")
    raise SystemExit('打包环境缺少依赖: ' + ', '.join(missing)
                     + f'\n当前 Python: {sys.executable}'
                     + '\n请在 PowerShell 运行以下命令安装项目依赖，然后重试打包：'
                     + f"\n& '{python_path}' -m pip install -r '{requirements_path}'"
                     + f"\n& '{python_path}' -m PyInstaller main.spec")

workspace_dir = os.path.dirname(spec_dir)

pathex = []
gui_hiddenimports = []
documentation_datas = [
    (os.path.join(spec_dir, 'docs', 'REMOTE_PROTOCOL.md'), 'docs'),
    (os.path.join(spec_dir, 'docs', 'OPERATION_MANUAL_FOR_GUI.md'), 'docs'),
    (os.path.join(spec_dir, 'docs', 'OPERATION_MANUAL_FOR_CLI.md'), 'docs'),
]
gui_datas = [(os.path.join(spec_dir, 'resources'), 'resources')] + documentation_datas
cli_hiddenimports = [
    'firmware_downloader_project.core.downloader',
    'firmware_downloader_project.core.protocol',
]
cli_datas = list(documentation_datas)

# 控制台使用普通 PromptSession,不使用 Pygments 语法着色/图片导出;其可选
# formatter 钩子会递归收集全局环境中的 Pillow 和 NumPy.
# 配置校验使用本地生成的 JSON Schema,不使用 jsonschema 旧 RefResolver 的
# requests HTTP 扩展,避免带入 urllib3/pyOpenSSL/cryptography.
unused_optional_imports = ['pygments', 'requests']

def env_enabled(env_name, default):
    value = os.environ.get(env_name, '').lower().strip()
    if value in ('1', 'true', 'yes', 'on'):
        return True
    if value in ('0', 'false', 'no', 'off'):
        return False
    return default


def add_subproject(project_name, hidden_imports, env_name):
    project_dir = os.path.abspath(os.path.join(workspace_dir, project_name))
    project_exists = os.path.isdir(project_dir)
    should_bundle = env_enabled(env_name, project_exists)

    print(f"### {project_name}: exists={project_exists}, bundle={should_bundle}")

    if not should_bundle:
        return
    if not project_exists:
        print(f"### Warning: {env_name} is enabled but directory not found: {project_dir}")
        return

    if workspace_dir not in pathex:
        pathex.append(workspace_dir)

    gui_hiddenimports.extend(hidden_imports)

    excluded_dirs = {'dist', 'build', '__pycache__', '.git', '.idea', '.vscode', '.pytest_cache'}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]

        for file in files:
            if file.endswith(('.spec', '.pyc', '.pyo')):
                continue

            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, project_dir)
            dest_path = os.path.join(project_name, os.path.dirname(rel_path))
            gui_datas.append((full_path, dest_path))

    print(f"### Bundled subproject from: {project_dir}")


add_subproject(
    'number_converter_project',
    ['number_converter_project.number_conversion_dialog'],
    'BUNDLE_NUMBER_CONVERTER',
)
add_subproject(
    'bin_hex_converter_project',
    ['bin_hex_converter_project.bin_hex_converter_dialog'],
    'BUNDLE_BIN_HEX_CONVERTER',
)
add_subproject(
    'firmware_downloader_project',
    [
        'firmware_downloader_project.firmware_downloader_dialog',
        'firmware_downloader_project.core.downloader',
    ],
    'BUNDLE_FIRMWARE_DOWNLOADER',
)

compat_analysis = Analysis(
    ['main.py'],
    pathex=pathex,
    binaries=[],
    datas=gui_datas,
    hiddenimports=gui_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=unused_optional_imports,
    noarchive=False,
    optimize=0,
)
compat_pyz = PYZ(compat_analysis.pure)

compat_exe = EXE(
    compat_pyz,
    compat_analysis.scripts,
    compat_analysis.binaries,
    compat_analysis.datas,
    [],
    name='GHowe_串口调试助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(spec_dir, 'resources', 'HOWE_LOGO.ico'),
)

gui_analysis = Analysis(
    ['gui_main.py'],
    pathex=pathex,
    binaries=[],
    datas=gui_datas,
    hiddenimports=gui_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=unused_optional_imports,
    noarchive=False,
    optimize=0,
)
gui_pyz = PYZ(gui_analysis.pure)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    gui_analysis.binaries,
    gui_analysis.datas,
    [],
    name='GHowe_串口调试助手_GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(spec_dir, 'resources', 'HOWE_LOGO.ico'),
)

cli_analysis = Analysis(
    ['cli_main.py'],
    pathex=pathex,
    binaries=[],
    datas=cli_datas,
    hiddenimports=cli_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # CLI 只使用 QtCore/QtNetwork 本机 IPC,不能依赖窗口,绘图或 QML.
    excludes=unused_optional_imports + ['PyQt5.QtWidgets', 'PyQt5.QtGui', 'PyQt5.QtQuick',
                                      'PyQt5.QtQml', 'PyQt5.QtOpenGL'],
    noarchive=False,
    optimize=0,
)
# PyInstaller 的 PyQt5 钩子额外收集 ANGLE/OpenGL,即使只导入 QtCore.
# CLI 不使用 QtGui;QtCore/QtNetwork 也不链接这些图形运行库.
cli_graphics_dlls = ('libegl.dll', 'libglesv2.dll', 'opengl32sw.dll', 'd3dcompiler_??.dll')
cli_analysis.binaries = [entry for entry in cli_analysis.binaries
                        if not any(fnmatch(os.path.basename(entry[0]).lower(), pattern)
                                   for pattern in cli_graphics_dlls)]
cli_pyz = PYZ(cli_analysis.pure)

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    cli_analysis.binaries,
    cli_analysis.datas,
    [],
    name='GHowe_串口调试助手_CLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(spec_dir, 'resources', 'HOWE_LOGO.ico'),
)
