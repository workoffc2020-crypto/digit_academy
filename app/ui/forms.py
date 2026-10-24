"""Dynamic form for administrator-defined custom fields."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from app.services import customfields
from app.ui.widgets import OptDate, btn


class CustomFieldsForm(QWidget):
    def __init__(self, app, module: str, parent=None):
        super().__init__(parent)
        self.app, self.module = app, module
        self.fields = customfields.fields(app.db, module, app.actor, for_edit=True)
        self.w = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        sections = {}
        for f in self.fields:
            sections.setdefault(f["section"], []).append(f)
        for sec, fl in sections.items():
            box = QGroupBox(sec)
            form = QFormLayout(box)
            for f in fl:
                w = self._make(f)
                lab = f["label"] + (" *" if f["required"] else "")
                form.addRow(lab, w)
                self.w[f["field_key"]] = (f, w)
                if f.get("readonly"):
                    w.setEnabled(False)
            v.addWidget(box)
        v.addStretch(1)
        self.empty = not self.fields

    def _make(self, f):
        t = f["field_type"]
        if t == "longtext":
            w = QPlainTextEdit()
            w.setFixedHeight(70)
        elif t in ("text", "email", "phone", "url", "int", "decimal"):
            w = QLineEdit()
            if f.get("max_length"):
                w.setMaxLength(int(f["max_length"]))
        elif t == "date":
            w = OptDate()
        elif t == "dropdown":
            w = QComboBox()
            w.addItem("", "")
            for c in f["choices"]:
                w.addItem(c, c)
        elif t == "multiselect":
            w = QListWidget()
            w.setFixedHeight(min(26 * len(f["choices"]) + 8, 130))
            for c in f["choices"]:
                it = QListWidgetItem(c)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked)
                w.addItem(it)
        elif t in ("checkbox", "yesno"):
            w = QCheckBox("Yes")
        elif t == "file":
            w = _FilePick()
        else:
            w = QLineEdit()
        if f["default_value"] and t not in ("file",):
            self._set(w, t, f["default_value"])
        return w

    @staticmethod
    def _set(w, t, v):
        if t == "longtext":
            w.setPlainText(v)
        elif isinstance(w, QLineEdit):
            w.setText(v)
        elif isinstance(w, OptDate):
            w.set_iso(v)
        elif isinstance(w, QComboBox):
            i = w.findData(v)
            w.setCurrentIndex(max(i, 0))
        elif isinstance(w, QListWidget):
            try:
                chosen = json.loads(v) if v else []
            except Exception:
                chosen = []
            for i in range(w.count()):
                w.item(i).setCheckState(Qt.CheckState.Checked if w.item(i).text() in chosen else Qt.CheckState.Unchecked)
        elif isinstance(w, QCheckBox):
            w.setChecked(v in ("1", "true", "Yes"))
        elif isinstance(w, _FilePick):
            w.path = v
            w.refresh()

    def set_values(self, values: dict):
        for key, (f, w) in self.w.items():
            if key in values and values[key] != "":
                self._set(w, f["field_type"], values[key])

    def values(self) -> dict:
        out = {}
        for key, (f, w) in self.w.items():
            if f.get("readonly"):
                continue
            t = f["field_type"]
            if t == "longtext":
                out[key] = w.toPlainText()
            elif isinstance(w, QLineEdit):
                out[key] = w.text()
            elif isinstance(w, OptDate):
                out[key] = w.iso() or ""
            elif isinstance(w, QComboBox):
                out[key] = w.currentData() or ""
            elif isinstance(w, QListWidget):
                out[key] = [w.item(i).text() for i in range(w.count()) if w.item(i).checkState() == Qt.CheckState.Checked]
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            elif isinstance(w, _FilePick):
                p = w.path
                if p and not p.startswith(self.app.paths.attachments):
                    p = customfields.attach_file(self.app.paths.attachments, p)      # validated copy
                    w.path = p
                out[key] = p
        return out


class _FilePick(QWidget):
    def __init__(self):
        super().__init__()
        self.path = ""
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.lab = QLabel("No file")
        b = btn("Choose file…")
        b.clicked.connect(self.pick)
        c = btn("Clear")
        c.clicked.connect(self.clear)
        h.addWidget(self.lab, 1)
        h.addWidget(b)
        h.addWidget(c)

    def refresh(self):
        import os
        self.lab.setText(os.path.basename(self.path) if self.path else "No file")

    def pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose file (PDF, image, Word, Excel; max 5 MB)", "", "Documents (*.pdf *.jpg *.jpeg *.png *.doc *.docx *.xls *.xlsx *.txt)")
        if p:
            self.path = p
            self.refresh()

    def clear(self):
        self.path = ""
        self.refresh()
