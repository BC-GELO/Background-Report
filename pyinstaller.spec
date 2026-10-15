# PyInstaller spec for Building a standalone Windows executable
# Build: pyinstaller pyinstaller.spec

# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# Module analysis
a = Analysis(
    ['hardware_health_checker.py'],
    pathex=['.'],
    binaries=[
        # Vulkan DLL (required for GPU compute load on any modern Windows GPU)
        # On Windows, vulkan.dll ships with all modern GPU drivers
        ('vulkan.dll', None) if os.path.exists(os.path.join(sys.exec_prefix, 'DLLs', 'vulkan.dll')) else None,
    ],
    hiddenimports=[
        'gpu_load',
        'ctypes',
        'winreg',
        'struct',
    ],
    excludes=[],
    cache_path='./build',
    hookspath=[],
    runtime_hooks=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    datas=[
        # Include gpu_load.py for the backend implementations
        ('gpu_load.py', '.'),
    ],
)

# Filter out None from binaries
a.binaries = [b for b in a.binaries if b is not None]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='hardware_health_checker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name='hardware_health_checker.app',
    icon=None,
    bundle_identifier=None,
)