# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'run_desktop.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'desktop' / 'assets'), 'desktop/assets'),
                         (str(root / 'docs' / 'desktop-guide.md'), 'docs'),
                         (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
                         (str(root / 'build' / 'licenses'), 'licenses')],
    hiddenimports=['keyring.backends.Windows', 'platformdirs.windows'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['streamlit', 'pytest', 'pytestqt', 'psycopg', 'tkinter', 'matplotlib',
              'numpy', 'pandas', 'pyarrow', 'PIL', 'IPython', 'jupyter', 'jinja2',
              'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtPdf',
              'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.Qt3DCore'],
    noarchive=False,
)
pyz = PYZ(a.pure)
# Qt 6.12 使用 Windows 的无版本后缀 ICU API。开发环境 PATH 中的 ICU 78
# 导出的是带版本号的符号，不能以 icuuc.dll 同名打包后遮盖 Windows 组件。
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt78.dll'}]
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LearningCompass',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(root / 'build' / 'compass.ico'), version=str(root / 'build' / 'version-info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LearningCompass')
