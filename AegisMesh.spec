# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['vpn_obfuscator_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('USER_GUIDE.md', '.'),
        ('XHS_PROJECT_NOTE.md', '.'),
    ],
    hiddenimports=['yaml'],
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
    [],
    exclude_binaries=True,
    name='AegisMesh',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/AegisMesh.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AegisMesh',
)
app = BUNDLE(
    coll,
    name='AegisMesh.app',
    icon='assets/AegisMesh.icns',
    bundle_identifier=None,
)
