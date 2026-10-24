# PyInstaller spec - one-folder, windowed build
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("app") + [
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPrintSupport",
    "cryptography", "openpyxl",
] + ["app.ui." + m for m in ("dashboard", "students", "fees", "attendance", "reports", "catalog", "fields", "users", "settings_page", "backup_page", "forms", "preview", "login", "widgets", "theme")]
a = Analysis(["main.py"], pathex=["."], datas=[("assets", "assets")], hiddenimports=hidden,
             excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DigitAcademy", console=False, icon="assets/app.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="DigitAcademy")
