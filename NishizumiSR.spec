# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Nishizumi SR overlay.

Build with:  pyinstaller NishizumiSR.spec --noconfirm

Produces a single windowed executable. Read-only data (the official corner
table) is bundled; everything the app writes - settings, learned tracks,
session history, logs - goes to %LOCALAPPDATA%\\NishizumiSR instead, via
app_paths.py. Writing into the bundle would be lost on exit.
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # read-only resources -> unpacked next to the code inside the bundle
        ('tracks/official_corners.json', 'tracks'),
    ],
    hiddenimports=[
        'irsdk',
        'keyboard',
        'yaml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim the heavy Qt pieces the overlay never touches. PySide6 otherwise
    # drags in WebEngine, 3D, multimedia and friends - hundreds of megabytes.
    excludes=[
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQml', 'PySide6.Qt3DCore',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtCharts',
        'PySide6.QtDataVisualization', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtBluetooth', 'PySide6.QtNetworkAuth', 'PySide6.QtPositioning',
        'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.QtHelp',
        'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtSerialPort',
        'PySide6.QtSensors', 'PySide6.QtSpatialAudio', 'PySide6.QtSvgWidgets',
        'PySide6.QtTextToSpeech', 'PySide6.QtUiTools', 'PySide6.QtXml',
        'matplotlib', 'scipy', 'pandas', 'tkinter', 'PIL', 'pytest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NishizumiSR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # overlay app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
