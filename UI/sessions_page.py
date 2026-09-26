"""Sessions page: match cards, highlight title bar, player + timeline, highlight list and export."""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
                             QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from Capture.ffmpeg import FFmpeg
from Core.clips import ClipExporter, Segment
from Core.discord import DiscordFitter
from Core.formatting import Format
from Core.library import RecordingEntry, RecordingLibrary
from Theme.palette import Palette
from UI.cards import CardStrip
from UI.icons import Icons
from UI.player import PlayerPanel
from UI.shell import Shell
from UI.widgets import IconButton, IconTextButton

if TYPE_CHECKING:
    from UI.main_window import MainWindow

log = logging.getLogger("gamecapture.ui")


class _ExportSignals(QObject):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(bool, str, object)  # ok, message, (kind, paths)


class SessionsPage(QWidget):
    DEFAULT_TYPES = Segment.HIGHLIGHT_TYPES  # pre-ticked for export

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self.sig = _ExportSignals()
        self.all_entries: list[RecordingEntry] = []
        self.current: RecordingEntry | None = None
        self.current_marker: dict | None = None
        self._check_state: dict[tuple, bool] = {}
        self._exporting = False
        self._build()
        self._wire()
        self._show_entry_header()

    # ================================================================ UI

    def _build(self) -> None:
        self.crumb = QLabel()
        self.crumb.setObjectName("Crumb")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search game, character, result, mode...")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.search.addAction(Icons.icon("search", Palette.TEXT_MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        title = QLabel("Sessions")
        title.setObjectName("PageTitle")
        top = QHBoxLayout()
        top.addWidget(title)
        top.addWidget(self.crumb)
        top.addStretch()
        top.addWidget(self.search)

        self.strip = CardStrip(self.win.thumbs)

        self.hl_title = QLabel()
        self.hl_title.setObjectName("HighlightTitle")
        self.hl_title.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.hl_sub = QLabel()
        self.hl_sub.setObjectName("Muted")
        self.star_btn = IconButton("star", "Favourite this highlight", checkable=True,
                                   checked_icon="star_filled", checked_color=Palette.GOLD)
        self.folder_btn = IconButton("folder", "Show in folder")
        self.trash_btn = IconButton("trash", "Delete this recording")
        self.share_btn = IconTextButton("share", "Share clip")
        self.share_btn.setToolTip("Export this highlight, shrink it to your Discord limit if needed, "
                                  "and copy the file - paste it into Discord")
        self.clips_btn = IconTextButton("scissors", "Export clips")
        self.clips_btn.setToolTip("One file per ticked highlight - instant, no re-encode")
        self.edit_btn = IconTextButton("nav_editor", "Edit")
        self.edit_btn.setToolTip("Send the ticked highlights to the video editor to cut, trim and join them")
        self.reel_btn = IconTextButton("reel", "Highlight reel", primary=True)
        self.reel_btn.setToolTip("All ticked highlights in one video (GPU encode)")
        bar = QHBoxLayout()
        bar.addWidget(self.hl_title)
        bar.addSpacing(6)
        for b in (self.star_btn, self.folder_btn, self.trash_btn):
            bar.addWidget(b)
        bar.addSpacing(8)
        bar.addWidget(self.hl_sub)
        bar.addStretch()
        for b in (self.share_btn, self.edit_btn, self.clips_btn, self.reel_btn):
            bar.addWidget(b)

        self.player = PlayerPanel()
        self.player.lead_seconds = self.cfg.clips.pre_seconds
        self.player.allow_preload = self.win.preload_allowed

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.player)
        split.addWidget(self._build_side_panel())
        split.setStretchFactor(0, 1)
        split.setSizes([900, 290])

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        root.addLayout(top)
        root.addWidget(self.strip)
        root.addLayout(bar)
        root.addWidget(split, 1)

    def _build_side_panel(self) -> QWidget:
        self.hl_list = QListWidget()
        self.hl_list.setTextElideMode(Qt.TextElideMode.ElideRight)  # long names end in "..." (full text on hover)
        self.hl_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.show_all_cb = QCheckBox("Show everyone's events")
        sel_all, sel_none = QPushButton("All"), QPushButton("None")
        sel_all.clicked.connect(lambda: self._set_all_checked(True))
        sel_none.clicked.connect(lambda: self._set_all_checked(False))
        head = QHBoxLayout()
        label = QLabel("Highlights")
        label.setStyleSheet("font-weight: 600;")
        head.addWidget(label)
        head.addStretch()
        head.addWidget(sel_all)
        head.addWidget(sel_none)

        self.pre_spin, self.post_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, value in ((self.pre_spin, self.cfg.clips.pre_seconds), (self.post_spin, self.cfg.clips.post_seconds)):
            spin.setRange(0, 60)
            spin.setDecimals(0)
            spin.setSuffix(" s")
            spin.setValue(value)
        form = QFormLayout()
        form.addRow("Before", self.pre_spin)
        form.addRow("After", self.post_spin)

        self.seg_label = QLabel()
        self.seg_label.setObjectName("Muted")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.export_label = QLabel()
        self.export_label.setWordWrap(True)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.addLayout(head)
        side.addWidget(self.show_all_cb)
        side.addWidget(self.hl_list, 1)
        side.addLayout(form)
        for w in (self.seg_label, self.progress, self.export_label):
            side.addWidget(w)
        panel = QWidget()
        panel.setLayout(side)
        panel.setMinimumWidth(250)
        return panel

    def _wire(self) -> None:
        self.search.textChanged.connect(lambda _: self._apply_filter())
        self.strip.cardClicked.connect(self.open_entry)
        self.player.timeline.markerClicked.connect(lambda m: self.select_marker(m, play=True))
        self.player.player.durationChanged.connect(lambda _: self._update_segments())
        self.hl_list.itemChanged.connect(self._on_item_checked)
        self.hl_list.currentItemChanged.connect(self._on_current_item)
        self.hl_list.itemDoubleClicked.connect(
            lambda item: self.select_marker(item.data(Qt.ItemDataRole.UserRole), play=True))
        self.show_all_cb.toggled.connect(lambda _: self._fill_highlights())
        self.pre_spin.valueChanged.connect(self._on_padding)
        self.post_spin.valueChanged.connect(self._on_padding)
        self.star_btn.toggled.connect(self._on_star)
        self.folder_btn.clicked.connect(lambda: self.current and Shell.reveal(self.current.video))
        self.trash_btn.clicked.connect(lambda: self.current and self.win.delete_entry(self.current))
        self.share_btn.clicked.connect(self._share)
        self.clips_btn.clicked.connect(lambda: self._export("clips"))
        self.reel_btn.clicked.connect(lambda: self._export("reel"))
        self.edit_btn.clicked.connect(lambda: self.current and self.win.open_in_editor(self.current, self._segments()))
        self.sig.progress.connect(self._on_progress)
        self.sig.done.connect(self._on_done)

    # ================================================================ entries

    def set_entries(self, entries: list[RecordingEntry]) -> None:
        self.all_entries = entries
        if self.current is not None and not any(e is self.current for e in entries):
            self.clear()
        self._apply_filter()
        if self.current is None and entries:
            self.open_entry(entries[0])

    def _apply_filter(self) -> None:
        words = self.search.text().strip().lower().split()
        shown = [e for e in self.all_entries if all(w in e.search_text() for w in words)]
        self.strip.set_entries(shown, self.current)

    def open_entry(self, entry: RecordingEntry) -> None:
        if entry is self.current:
            return
        self.current = entry
        self.current_marker = None
        self._check_state.clear()
        self.strip.mark_selected(entry)
        self.player.load(entry.video, entry.markers)
        self._fill_highlights()
        self._show_entry_header()
        self.export_label.setText("")

    def open_at(self, video_path: str, video_time: float) -> None:
        """From the Favorites page: open a recording at a specific highlight."""
        entry = next((e for e in self.all_entries if str(e.video) == video_path), None)
        if entry is None:
            return
        self.open_entry(entry)
        marker = next((m for m in entry.markers if abs(m["video_time"] - video_time) < 0.01), None)
        if marker is not None:
            self.select_marker(marker, play=True)

    def clear(self) -> None:
        self.current = None
        self.current_marker = None
        self.player.unload()
        self._fill_highlights()
        self._show_entry_header()

    def _show_entry_header(self) -> None:
        e = self.current
        for b in (self.folder_btn, self.trash_btn):
            b.setEnabled(e is not None)
        self.star_btn.blockSignals(True)
        self.star_btn.setChecked(False)
        self.star_btn.blockSignals(False)
        self.star_btn.setEnabled(False)
        if e is None:
            self.crumb.setText("")
            self.hl_title.setText("No recording selected")
            self.hl_sub.setText("")
        else:
            self.crumb.setText(f"›  {e.game_name}  ·  {e.when:%d %b %Y, %H:%M}")
            self.hl_title.setText(e.title)
            self.hl_sub.setText("  ·  ".join(x for x in (e.result, e.kda, e.mode_name) if x))
        self._update_buttons()

    # ================================================================ highlights

    @staticmethod
    def _key(m: dict) -> tuple:
        return (m.get("video_time"), m.get("label"))

    def _fill_highlights(self) -> None:
        self.hl_list.blockSignals(True)
        self.hl_list.clear()
        markers = self.current.markers if self.current else []
        show_all = self.show_all_cb.isChecked()
        for m in sorted(markers, key=lambda m: m["video_time"]):
            if not show_all and not m.get("involves_me"):
                continue
            item = QListWidgetItem(Icons.marker_icon(m.get("type", ""), bool(m.get("favorite"))),
                                   f"{Format.duration(m['video_time'])}    {m['label']}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setToolTip(m["label"])
            default = m.get("involves_me", False) and m.get("type") in self.DEFAULT_TYPES
            checked = self._check_state.get(self._key(m), default)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, m)
            self.hl_list.addItem(item)
            if m is self.current_marker:
                self.hl_list.setCurrentItem(item)
        if self.hl_list.count() == 0:
            text = ("Select a recording above" if self.current is None else
                    "No highlights - this was a manual recording" if not markers else
                    "No highlights involving you (tick 'Show everyone's events')")
            placeholder = QListWidgetItem(text)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.hl_list.addItem(placeholder)
        self.hl_list.blockSignals(False)
        self._update_segments()

    def select_marker(self, m: dict | None, play: bool = False) -> None:
        if not m or self.current is None:
            return
        self.current_marker = m
        e = self.current
        self.hl_title.setText(m["label"])
        self.hl_sub.setText("  ·  ".join(x for x in (Format.duration(m["video_time"]), e.title, e.result) if x))
        self.star_btn.setEnabled(e.editable)
        self.star_btn.blockSignals(True)
        self.star_btn.setChecked(bool(m.get("favorite")))
        self.star_btn.blockSignals(False)
        self.player.timeline.set_selected(m)
        for i in range(self.hl_list.count()):
            item = self.hl_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is m:
                self.hl_list.blockSignals(True)
                self.hl_list.setCurrentItem(item)
                self.hl_list.blockSignals(False)
                break
        if play:
            self.player.play_from(m["video_time"] - self.pre_spin.value())
        self._update_buttons()

    def _on_current_item(self, item, _previous) -> None:
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self.select_marker(item.data(Qt.ItemDataRole.UserRole))

    def _on_item_checked(self, item: QListWidgetItem) -> None:
        m = item.data(Qt.ItemDataRole.UserRole)
        if m:
            self._check_state[self._key(m)] = item.checkState() == Qt.CheckState.Checked
            self._update_segments()

    def _set_all_checked(self, checked: bool) -> None:
        self.hl_list.blockSignals(True)
        for i in range(self.hl_list.count()):
            item = self.hl_list.item(i)
            m = item.data(Qt.ItemDataRole.UserRole)
            if m:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                self._check_state[self._key(m)] = checked
        self.hl_list.blockSignals(False)
        self._update_segments()

    def _checked_markers(self) -> list[dict]:
        out = []
        for i in range(self.hl_list.count()):
            item = self.hl_list.item(i)
            m = item.data(Qt.ItemDataRole.UserRole)
            if m and item.checkState() == Qt.CheckState.Checked:
                out.append(m)
        return out

    def _on_star(self, on: bool) -> None:
        m, e = self.current_marker, self.current
        if m is None or e is None or not e.editable:
            return
        if on:
            m["favorite"] = True
        else:
            m.pop("favorite", None)
        try:
            RecordingLibrary.save(e)
        except (OSError, PermissionError) as exc:
            log.error("Could not save favourite: %s", exc)
        self._fill_highlights()
        self.win.favorites_changed()

    def apply_clip_defaults(self) -> None:
        """Settings changed the default padding: reflect it here."""
        self.pre_spin.setValue(self.cfg.clips.pre_seconds)
        self.post_spin.setValue(self.cfg.clips.post_seconds)

    def _on_padding(self) -> None:
        self.player.lead_seconds = self.pre_spin.value()
        self.cfg.clips.pre_seconds = self.pre_spin.value()
        self.cfg.clips.post_seconds = self.post_spin.value()
        self._update_segments()

    # ================================================================ export

    def _segments(self, markers: list[dict] | None = None) -> list[Segment]:
        return Segment.from_markers(self._checked_markers() if markers is None else markers,
                                    self.pre_spin.value(), self.post_spin.value(), self.player.duration or None)

    def _update_segments(self) -> None:
        segs = self._segments()
        self.player.timeline.set_segments([(s.start, s.end) for s in segs])
        total = sum(s.duration for s in segs)
        self.seg_label.setText(f"{len(segs)} clip{'s' if len(segs) != 1 else ''} ticked · {Format.duration(total)}"
                               if segs else "Tick highlights to export")
        self._update_buttons()

    def _update_buttons(self) -> None:
        idle = self.current is not None and not self._exporting
        has_segs = bool(self._segments()) if self.current else False
        self.clips_btn.setEnabled(idle and has_segs)
        self.edit_btn.setEnabled(idle and has_segs)
        self.reel_btn.setEnabled(idle and has_segs)
        self.share_btn.setEnabled(idle and self.current_marker is not None)

    def _ffmpeg(self) -> FFmpeg | None:
        ffmpeg = self.win.engine.ffmpeg or FFmpeg.locate(self.cfg.ffmpeg_path)
        if ffmpeg is None:
            QMessageBox.warning(self, "GameCapture", "ffmpeg not found - run Tools\\get_ffmpeg.py")
        return ffmpeg

    def _share(self) -> None:
        if self.current_marker is not None:
            self._export("share", [self.current_marker])

    def _export(self, kind: str, markers: list[dict] | None = None) -> None:
        entry, segs = self.current, self._segments(markers)
        if entry is None or not segs:
            return
        ffmpeg = self._ffmpeg()
        if ffmpeg is None:
            return
        exporter = ClipExporter(ffmpeg)
        encoder = self.win.engine.encoder or "x264"
        out_dir = self.win.clips_dir()
        self._exporting = True
        self._update_buttons()
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.export_label.setText("Starting...")
        report = lambda frac, text: self.sig.progress.emit(int(frac * 100), text)  # noqa: E731

        def work():
            try:
                if kind == "reel":
                    path = exporter.export_reel(entry.video, segs, out_dir / f"{entry.video.stem}_highlights.mp4",
                                                encoder, self.cfg.capture.quality, report)
                    self.sig.done.emit(True, f"Saved {path.name}", (kind, [path]))
                else:
                    paths = exporter.export_clips(entry.video, segs, out_dir, report)
                    if kind == "share":  # make sure it goes through on Discord
                        limit = self.cfg.clips.discord_limit_mb
                        paths = [DiscordFitter(ffmpeg, encoder).fit(paths[0], limit, progress=report)]
                    self.sig.done.emit(True, f"Saved {len(paths)} clip(s)", (kind, paths))
            except Exception as exc:
                log.error("Export failed: %s", exc)
                self.sig.done.emit(False, str(exc), (kind, []))
        threading.Thread(target=work, name="export", daemon=True).start()

    def _on_progress(self, percent: int, text: str) -> None:
        self.progress.setValue(percent)
        self.export_label.setText(text)

    def _on_done(self, ok: bool, text: str, payload) -> None:
        kind, paths = payload
        self._exporting = False
        self.progress.setVisible(False)
        if ok and kind == "share" and paths:
            Shell.copy_file_to_clipboard(paths[0])
            text = (f"Clip copied ({paths[0].stat().st_size / 1e6:.1f} MB, fits Discord's "
                    f"{self.cfg.clips.discord_limit_mb} MB) - paste it with Ctrl+V")
        self.export_label.setText(("Done: " if ok else "Failed: ") + text)
        self.export_label.setStyleSheet(f"color: {Palette.WIN if ok else Palette.LOSE};")
        self._update_buttons()
        self.win.clips_changed()
