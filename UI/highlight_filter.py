"""Highlight filter chips (Sessions): one pill per kind of moment - Kills 6, Deaths 5, Objectives 3 ...

Click a chip to show / hide that kind in the list and on the timeline; double-click it to show only
that kind. "Everyone" adds other players' events. The choice sticks when you open another recording."""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from Theme.palette import MarkerStyle, Palette
from UI.icons import Icons
from UI.widgets import FilterChip, FlowLayout


class HighlightFilter(QWidget):
    changed = pyqtSignal()
    # (key, chip name, marker types, icon type) - in the order the chips appear
    GROUPS = (
        ("kills", "Kills", {"kill", "multikill", "first_blood", "ace"}, "kill"),
        ("assists", "Assists", {"assist"}, "assist"),
        ("deaths", "Deaths", {"death"}, "death"),
        ("objectives", "Objectives", {"objective"}, "objective"),
        ("structures", "Towers", {"structure"}, "structure"),
        ("bookmarks", "Bookmarks", {"bookmark"}, "bookmark"),
    )
    HIDDEN_TYPES = {"game_start", "game_end"}   # match start / end: always on the timeline, never in the list

    def __init__(self) -> None:
        super().__init__()
        self._flow = FlowLayout(self, spacing=6)
        self._chips: dict[str, FilterChip] = {}
        self.hidden: set[str] = set()      # group keys you switched off
        self.everyone = False              # other players' events

    # ---------- which group a marker is in ----------

    @classmethod
    def group(cls, m: dict) -> str:
        kind = m.get("type", "")
        return next((key for key, _n, types, _i in cls.GROUPS if kind in types), "other")

    def accepts(self, m: dict) -> bool:
        if m.get("type") in self.HIDDEN_TYPES:
            return False
        if not m.get("involves_me") and not self.everyone:
            return False
        return self.group(m) not in self.hidden

    # ---------- chips ----------

    def set_markers(self, markers: list[dict]) -> None:
        """Rebuild the chips for this recording: only kinds it has, each with its count."""
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._chips.clear()
        listed = [m for m in markers if m.get("type") not in self.HIDDEN_TYPES]
        mine = [m for m in listed if m.get("involves_me")]
        groups = [*self.GROUPS, ("other", "Other", set(), "other")]
        for key, name, _types, icon in groups:
            n = sum(1 for m in mine if self.group(m) == key)
            if n:
                self._add(key, f"{name}  {n}", Icons.icon(MarkerStyle.icon(icon), MarkerStyle.color(icon), 14),
                          f"Show / hide {name.lower()} - double-click to show only these",
                          key not in self.hidden)
        others = sum(1 for m in listed if not m.get("involves_me"))
        if others:
            self._add("everyone", f"Everyone  {others}", Icons.icon("marker_other", Palette.TEXT_MUTED, 14),
                      "Also show other players' events", self.everyone)
        self.setVisible(bool(self._chips))
        self.updateGeometry()

    def _add(self, key: str, text: str, icon, tip: str, on: bool) -> None:
        chip = FilterChip(icon, text, tip)
        chip.setChecked(on)
        chip.toggled.connect(lambda checked, k=key: self._toggle(k, checked))
        chip.soloRequested.connect(lambda k=key: self.solo(k))
        self._chips[key] = chip
        self._flow.addWidget(chip)

    def _toggle(self, key: str, on: bool) -> None:
        if key == "everyone":
            self.everyone = on
        elif on:
            self.hidden.discard(key)
        else:
            self.hidden.add(key)
        self.changed.emit()

    def solo(self, key: str) -> None:
        """Show only this kind (double-click); double-click it again to show everything."""
        if key == "everyone":
            return
        groups = [k for k in self._chips if k != "everyone"]
        already = self.hidden == set(groups) - {key}
        self.hidden = set() if already else set(groups) - {key}
        for k, chip in self._chips.items():
            if k != "everyone":
                chip.blockSignals(True)
                chip.setChecked(k not in self.hidden)
                chip.blockSignals(False)
        self.changed.emit()

    # FlowLayout wraps, so the widget's height depends on its width
    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._flow.heightForWidth(width)
