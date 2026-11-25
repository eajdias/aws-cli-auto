# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
src = root / "src"

a = Analysis(
    [str(root / "scripts" / "exe_entry.py")],
    pathex=[str(src)],
    binaries=[],
    datas=[
        (
            str(src / "s3_glacier_restore" / "ui" / "index.html"),
            "s3_glacier_restore/ui",
        )
    ],
    hiddenimports=["s3_glacier_restore.ui._build_config"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=-1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="s3-download-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
