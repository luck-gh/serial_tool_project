# -*- mode: python ; coding: utf-8 -*-
# pyinstaller main.spec
import os

# 获取当前工作目录
spec_dir = os.getcwd()

# 检查是否需要打包位计算器
# 优先级: 环境变量 BUNDLE_CALC > 自动检测同级目录
calc_dir = os.path.abspath(os.path.join('..', 'number_converter_project'))
calc_exists = os.path.exists(calc_dir)

bundle_calc_env = os.environ.get('BUNDLE_CALC', '').lower().strip()
if bundle_calc_env == 'true':
    bundle_calc = True
elif bundle_calc_env == 'false':
    bundle_calc = False
else:
    # 默认逻辑: 如果同级目录存在则打包
    bundle_calc = calc_exists

pathex = []
hiddenimports = []
datas = [(os.path.join(spec_dir, 'resources'), 'resources')]

if bundle_calc:
    if calc_exists:
        # 将父目录加入路径，这样可以作为包导入 number_converter_project
        # 把主工程的父目录加入 pathex，确保子工程包可被找到
        pathex.append(os.path.dirname(spec_dir))
        # 明确列出隐藏导入，避免打包时被遗漏
        hiddenimports.append('number_converter_project.number_conversion_dialog')
        # 如果需要，也可以显式添加子工程资源目录到 datas, 示例已包含对整个子工程的文件收集
        
        # 收集需要的文件，排除 dist, build, __pycache__, .git 等
        excluded_dirs = {'dist', 'build', '__pycache__', '.git', '.idea', '.vscode'}
        for root, dirs, files in os.walk(calc_dir):
            # 过滤目录
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            
            for file in files:
                # 排除 spec 文件和 python 编译文件
                if file.endswith('.spec') or file.endswith('.pyc'):
                    continue
                    
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, calc_dir)
                dest_path = os.path.join('number_converter_project', os.path.dirname(rel_path))
                datas.append((full_path, dest_path))
        
        print(f"### Bundling Bit Calculator (filtered) from: {calc_dir}")
    else:
        print(f"### Warning: BUNDLE_CALC is true but directory not found at {calc_dir}")

print(f"### bundle_calc {bundle_calc}")
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(spec_dir, 'resources', 'HOWE_LOGO.ico'),
)
