# -*- mode: python ; coding: utf-8 -*-
"""
# pyinstaller main.spec
"""
"""
串口工具 PyInstaller 打包配置
支持独立打包和集成打包
"""
import os

# 获取当前工作目录
spec_dir = os.getcwd()
workspace_dir = os.path.dirname(spec_dir)

pathex = []
hiddenimports = []
datas = [(os.path.join(spec_dir, 'resources'), 'resources')]

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

    hiddenimports.extend(hidden_imports)

    excluded_dirs = {'dist', 'build', '__pycache__', '.git', '.idea', '.vscode', '.pytest_cache'}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]

        for file in files:
            if file.endswith(('.spec', '.pyc', '.pyo')):
                continue

            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, project_dir)
            dest_path = os.path.join(project_name, os.path.dirname(rel_path))
            datas.append((full_path, dest_path))

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
    ['firmware_downloader_project.firmware_downloader_dialog'],
    'BUNDLE_FIRMWARE_DOWNLOADER',
)

a = Analysis(
    ['main.py'],
    pathex=pathex,
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
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
