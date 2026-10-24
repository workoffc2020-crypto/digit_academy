"""Navy / white / teal theme. One stylesheet function; light and dark palettes."""
import os

from app.paths import resource_path

LIGHT = dict(bg="#f1f5fa", card="#ffffff", text="#1f2937", muted="#64748b", border="#d9e2ee", navy="#0b2545", navy2="#13315c", teal="#13a89e",
             teal_dark="#0e8a81", input="#ffffff", alt="#f6f9fd", sel="#d7f3f0", sel_text="#0b2545", danger="#dc2626", warn="#d97706", ok="#16a34a", head="#eaf0f8")
DARK = dict(bg="#0f172a", card="#1a2540", text="#e5ebf5", muted="#94a3b8", border="#2c3a5a", navy="#07142b", navy2="#0d2146", teal="#22c3b6", teal_dark="#17a094",
            input="#111c33", alt="#16213a", sel="#14463f", sel_text="#ffffff", danger="#f87171", warn="#fbbf24", ok="#4ade80", head="#22304f")


def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


def stylesheet(font_pt: int = 10, dark: bool = False) -> str:
    c = palette(dark)
    tick = resource_path(os.path.join("assets", "check.png")).replace("\\", "/")
    return f"""
* {{ font-family: 'Segoe UI', 'Noto Sans', 'FreeSans', Arial; font-size: {font_pt}pt; color: {c['text']}; }}
QMainWindow, QDialog, QWidget#page {{ background: {c['bg']}; }}
QWidget {{ background: transparent; }}
QDialog QWidget, QMainWindow > QWidget {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: {font_pt + 7}pt; font-weight: 700; color: {c['navy'] if not dark else c['text']}; }}
QLabel#h2 {{ font-size: {font_pt + 2}pt; font-weight: 600; }}
QLabel#muted {{ color: {c['muted']}; }}
QFrame#card, QGroupBox {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QGroupBox {{ margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {c['teal_dark']}; background: transparent; }}
QFrame#kpi {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QLabel#kpiv {{ font-size: {font_pt + 8}pt; font-weight: 700; }}
QLabel#kpit {{ color: {c['muted']}; font-size: {max(font_pt - 1, 8)}pt; font-weight: 600; }}
QFrame#header {{ background: {c['navy']}; border: none; }}
QFrame#header QLabel {{ color: #ffffff; }}
QLabel#htitle {{ font-size: {font_pt + 5}pt; font-weight: 700; color: #ffffff; }}
QLabel#hsub {{ color: #5eead4; font-size: {max(font_pt - 1, 8)}pt; }}
QFrame#sidebar {{ background: {c['navy2']}; border: none; }}
QPushButton#nav {{ background: transparent; color: #cbd5e1; text-align: left; padding: 11px 16px; border: none; border-left: 4px solid transparent; font-weight: 600; }}
QPushButton#nav:hover {{ background: rgba(255,255,255,0.08); color: #ffffff; }}
QPushButton#nav:checked {{ background: rgba(19,168,158,0.22); color: #ffffff; border-left: 4px solid {c['teal']}; }}
QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 7px 14px; font-weight: 600; }}
QPushButton:hover {{ border-color: {c['teal']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['alt']}; }}
QPushButton[variant="primary"] {{ background: {c['teal']}; color: #ffffff; border: 1px solid {c['teal_dark']}; }}
QPushButton[variant="primary"]:hover {{ background: {c['teal_dark']}; }}
QPushButton[variant="navy"] {{ background: {c['navy']}; color: #ffffff; border: 1px solid {c['navy']}; }}
QPushButton[variant="danger"] {{ background: {c['danger']}; color: #ffffff; border: 1px solid {c['danger']}; }}
QPushButton[variant="warn"] {{ background: {c['warn']}; color: #ffffff; border: 1px solid {c['warn']}; }}
QPushButton[variant="primary"]:disabled, QPushButton[variant="navy"]:disabled, QPushButton[variant="danger"]:disabled, QPushButton[variant="warn"]:disabled {{ background: {c['border']}; color: {c['muted']}; border-color: {c['border']}; }}
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 5px 8px; selection-background-color: {c['teal']}; selection-color: #ffffff; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c['teal']}; }}
QLineEdit:read-only {{ background: {c['alt']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; selection-background-color: {c['sel']}; selection-color: {c['sel_text']}; border: 1px solid {c['border']}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {c['border']}; border-radius: 5px; background: {c['input']}; }}
QCheckBox::indicator:checked {{ background: {c['teal']}; border-color: {c['teal_dark']}; image: url({tick}); }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {c['border']}; background: {c['input']}; }}
QRadioButton::indicator:checked {{ background: {c['teal']}; border-color: {c['teal_dark']}; }}
QTableWidget, QTableView, QTreeWidget, QListWidget {{ background: {c['card']}; alternate-background-color: {c['alt']}; border: 1px solid {c['border']}; border-radius: 8px; gridline-color: {c['border']}; selection-background-color: {c['sel']}; selection-color: {c['sel_text']}; }}
QHeaderView::section {{ background: {c['head']}; color: {c['text']}; padding: 7px 8px; border: none; border-right: 1px solid {c['border']}; border-bottom: 1px solid {c['border']}; font-weight: 700; }}
QTableWidget::item {{ padding: 4px; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['card']}; top: -1px; }}
QTabBar::tab {{ background: {c['head']}; padding: 8px 16px; border: 1px solid {c['border']}; border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 3px; font-weight: 600; }}
QTabBar::tab:selected {{ background: {c['card']}; color: {c['teal_dark']}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 6px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 6px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollArea {{ border: none; background: transparent; }}
QToolTip {{ background: {c['navy']}; color: #ffffff; border: none; padding: 5px; }}
QProgressBar {{ border: 1px solid {c['border']}; border-radius: 6px; background: {c['alt']}; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {c['teal']}; border-radius: 5px; }}
QMenu {{ background: {c['card']}; border: 1px solid {c['border']}; }} QMenu::item {{ padding: 6px 22px; }} QMenu::item:selected {{ background: {c['sel']}; color: {c['sel_text']}; }}
QMessageBox {{ background: {c['card']}; }}
"""
