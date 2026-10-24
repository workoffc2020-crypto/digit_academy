"""Field Designer: add / edit / reorder / disable custom fields without touching code."""
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QListWidget, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget

from app.security.permissions import ROLES
from app.services import customfields as cf
from app.ui.fees import SimpleMoneyDialog as FormDialog
from app.ui.widgets import DataTable, btn, confirm, fill_combo, label, page_header, safe, warn


class FieldsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Custom Fields", "Add your own fields to student forms, reports and printed forms - no programming"))
        h = QHBoxLayout()
        h.addWidget(label("Show fields for:"))
        self.module = QComboBox()
        fill_combo(self.module, [(n, k) for k, n in cf.MODULES.items()])
        self.module.currentIndexChanged.connect(lambda _=0: self.refresh())
        h.addWidget(self.module)
        h.addStretch(1)
        v.addLayout(h)
        self.table = DataTable([("Order", "sort_order", "int"), ("Section", "section", "text"), ("Label", "label", "text"), ("Type", lambda r: cf.FIELD_TYPES[r["field_type"]], "text"),
                                ("Required", lambda r: "Yes" if r["required"] else "No", "text"), ("On printed forms", lambda r: "Yes" if r["print_on_forms"] else "No", "text"),
                                ("Status", lambda r: "Active" if r["active"] else "Disabled", "status")], page_size=200, empty="No custom fields yet. Click “Add field”.")
        v.addWidget(self.table, 1)
        b = QHBoxLayout()
        b.addWidget(btn("➕  Add field", "primary", slot=lambda: self.edit(None)))
        b.addWidget(btn("✏  Edit", slot=lambda: self.edit(self.table.current())))
        b.addWidget(btn("▲ Up", slot=lambda: self.move(-1)))
        b.addWidget(btn("▼ Down", slot=lambda: self.move(1)))
        b.addWidget(btn("Disable / enable", "warn", slot=self.toggle))
        b.addStretch(1)
        v.addLayout(b)
        v.addWidget(label("Disabling a field hides it but keeps every saved value; enabling it brings the data back. Fields that already hold data cannot change type.", "muted"))

    def refresh(self):
        self.rows = cf.fields(self.app.db, self.module.currentData(), include_inactive=True)
        self.table.set_rows(self.rows)

    def toggle(self):
        r = self.table.current()
        if r:
            ok, _ = safe(self, cf.set_active, self.app.db, self.app.actor, r["id"], not r["active"])
            self.refresh()

    def move(self, d):
        r = self.table.current()
        if not r:
            return
        ids = [x["id"] for x in self.rows]
        i = ids.index(r["id"])
        j = i + d
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            safe(self, cf.set_order, self.app.db, self.app.actor, ids)
            self.refresh()

    def edit(self, row):
        if row is None and False:
            return
        label_e = QLineEdit(row["label"] if row else "")
        mod = self.module.currentData()
        typ = QComboBox(); fill_combo(typ, [(n, k) for k, n in cf.FIELD_TYPES.items()], current=row["field_type"] if row else "text")
        sec = QLineEdit(row["section"] if row else "Additional information")
        req = QCheckBox("Required"); req.setChecked(bool(row["required"]) if row else False)
        prn = QCheckBox("Show on printed forms / profile"); prn.setChecked(bool(row["print_on_forms"]) if row else False)
        dflt = QLineEdit(row["default_value"] if row else "")
        mx = QSpinBox(); mx.setRange(0, 1000); mx.setSpecialValueText("No limit"); mx.setValue(row["max_length"] or 0 if row else 0)
        choices = QPlainTextEdit("\n".join(row["choices"]) if row else ""); choices.setFixedHeight(80)
        choices.setPlaceholderText("One choice per line (for dropdown / multi-select)")
        vis = QListWidget(); vis.setFixedHeight(90)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem
        have = set((row["visible_roles"] or "").split(",")) if row else set()
        for k, n in ROLES.items():
            it = QListWidgetItem(n); it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if k in have else Qt.CheckState.Unchecked); it.setData(Qt.ItemDataRole.UserRole, k); vis.addItem(it)
        d = FormDialog(self, "Custom field", [("l", "Label *", label_e), ("t", "Field type", typ), ("s", "Section / group", sec), ("r", "", req), ("p", "", prn), ("d", "Default value", dflt),
                                              ("m", "Maximum length", mx), ("c", "Choices", choices), ("v", "Visible only to (none ticked = everyone)", vis)],
                       "Fields appear on the student form (tab “Additional info”), in the profile and custom-field report.")
        if d.exec():
            roles = [vis.item(i).data(Qt.ItemDataRole.UserRole) for i in range(vis.count()) if vis.item(i).checkState() == Qt.CheckState.Checked]
            ok, _ = safe(self, cf.save_field, self.app.db, self.app.actor, id=row["id"] if row else None, module=mod, label=label_e.text(), field_type=typ.currentData(),
                         required=req.isChecked(), default_value=dflt.text(), max_length=mx.value() or None, choices=choices.toPlainText().splitlines(), section=sec.text(),
                         visible_roles=roles, print_on_forms=prn.isChecked(), active=bool(row["active"]) if row else True)
            self.refresh()
