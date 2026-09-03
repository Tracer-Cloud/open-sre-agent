# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import os
import runpy
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


ROOT = Path(SPECPATH).resolve()
MODE = os.environ.get("OPENSRE_PYINSTALLER_MODE", "onedir")
if MODE not in {"onedir", "onefile"}:
    raise ValueError(f"Unsupported OPENSRE_PYINSTALLER_MODE: {MODE!r}")

manifest = runpy.run_path(str(ROOT / "infrastructure/deployment/packaging/release_manifest.py"))
runtime_hidden_imports = manifest["runtime_hidden_imports"]
skill_data_entries = manifest["skill_data_entries"]
infrastructure_data_entries = manifest["infrastructure_data_entries"]

datas = list(infrastructure_data_entries(ROOT))
datas += collect_data_files(
    "core.agent_harness.prompts",
    includes=["opensre_system_prompt.md"],
)
datas += collect_data_files("surfaces.cli")
datas += collect_data_files("config")
datas += collect_data_files("litellm")
datas += copy_metadata("opensre")
datas += list(skill_data_entries(ROOT))

hiddenimports = [
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    *runtime_hidden_imports(ROOT),
]

# The frozen binary IS ``opensre``, so it starts where the console script does:
# the entrypoint that composes the CLI, the interactive shell and the gateway.
# Entering through the CLI alone would leave the binary without a shell to
# launch and without a foreground gateway runner.
a = Analysis(
    [str(ROOT / "surfaces/entrypoint.py")],
    pathex=[str(ROOT)],
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

if MODE == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="opensre",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="opensre",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="opensre",
    )
