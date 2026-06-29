from __future__ import annotations

import ctypes
import sys
from datetime import datetime
from functools import partial
from pathlib import Path
import winreg

from PySide6.QtCore import QDateTime, QSettings, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QTextCharFormat, QTextListFormat
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QMenu,
    QSystemTrayIcon,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from sticky_notes.models import Note
from sticky_notes.storage import DEFAULT_COLORS, NoteStore, html_to_text


CARD_WIDTH = 220
CARD_HEIGHT = 160
REMINDER_OPTIONS = list(range(5, 181, 5)) + [24 * 60]
MAX_QT_TIMER_MS = 2_000_000_000
STARTUP_REGISTRY_NAME = "StickyNotes"
STARTUP_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
SETTINGS_ORGANIZATION = "Steve Leach"
SETTINGS_APPLICATION = "Sticky Notes"
DEFAULT_WINDOW_SIZE = QSize(1190, 760)
SINGLE_INSTANCE_SERVER_NAME = "SteveLeach.StickyNotes.SingleInstance"


def reminder_label(minutes: int) -> str:
    if minutes == 24 * 60:
        return "1 Day"
    if minutes < 60:
        return f"{minutes} min"
    if minutes % 60 == 0:
        return f"{minutes // 60} hr"
    return f"{minutes} min"


def default_reminder_start() -> QDateTime:
    now = QDateTime.currentDateTime()
    start = QDateTime(now.date(), QTime(8, 0))
    if start.toSecsSinceEpoch() <= now.toSecsSinceEpoch():
        start = start.addDays(1)
    return start


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def startup_command() -> str:
    return f'"{Path(sys.executable).resolve()}"'


def is_windows_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, STARTUP_REGISTRY_NAME)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return value == startup_command()


def set_windows_startup_enabled(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_REGISTRY_NAME, 0, winreg.REG_SZ, startup_command())
            return
        try:
            winreg.DeleteValue(key, STARTUP_REGISTRY_NAME)
        except FileNotFoundError:
            pass


def notify_existing_instance() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_SERVER_NAME)
    if not socket.waitForConnected(200):
        socket.abort()
        return False
    socket.write(b"show")
    socket.flush()
    socket.waitForBytesWritten(200)
    socket.disconnectFromServer()
    return True


def create_single_instance_server() -> QLocalServer | None:
    QLocalServer.removeServer(SINGLE_INSTANCE_SERVER_NAME)
    server = QLocalServer()
    if server.listen(SINGLE_INSTANCE_SERVER_NAME):
        return server
    return None


class ReminderStartDateEdit(QDateEdit):
    def focus_calendar_on_selected_date(self) -> None:
        calendar = self.calendarWidget()
        if calendar is not None:
            calendar.setSelectedDate(self.date())
            calendar.showSelectedDate()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.focus_calendar_on_selected_date()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        self.focus_calendar_on_selected_date()
        super().keyPressEvent(event)


class ReminderCalendarWidget(QCalendarWidget):
    def paintCell(self, painter, rect, date) -> None:  # type: ignore[override]
        if date != self.selectedDate():
            super().paintCell(painter, rect, date)
            return

        painter.save()
        painter.fillRect(rect.adjusted(2, 2, -2, -2), QColor("#2f80ed"))
        painter.setPen(Qt.white)
        painter.drawText(rect, Qt.AlignCenter, str(date.day()))
        painter.restore()


class OptionsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.startup_checkbox = QCheckBox("Start when Windows starts")
        self.startup_checkbox.setChecked(is_windows_startup_enabled())
        self.startup_checkbox.toggled.connect(self.update_startup)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)

        layout.addWidget(self.startup_checkbox)
        layout.addStretch(1)
        layout.addLayout(button_row)

    def update_startup(self, enabled: bool) -> None:
        try:
            set_windows_startup_enabled(enabled)
        except OSError as error:
            self.startup_checkbox.blockSignals(True)
            self.startup_checkbox.setChecked(is_windows_startup_enabled())
            self.startup_checkbox.blockSignals(False)
            QMessageBox.warning(self, "Startup option", f"Could not update Windows startup setting.\n\n{error}")


class AboutDialog(QDialog):
    def __init__(self, app_icon: QIcon, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Sticky Notes")
        self.setMinimumWidth(380)
        self.setWindowIcon(app_icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(12)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        pixmap = app_icon.pixmap(96, 96)
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap)

        title_label = QLabel("Sticky Notes")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setObjectName("aboutTitle")

        description = QLabel("Classic sticky note cards with priority reminders and system tray support.")
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignCenter)

        author = QLabel("Author: Steve Leach")
        copyright_label = QLabel("Copyright 2026 Steve Leach")
        storage = QLabel("Notes are stored locally on this computer.")
        for label in (author, copyright_label, storage):
            label.setAlignment(Qt.AlignCenter)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        button_row.addStretch(1)

        layout.addWidget(icon_label)
        layout.addWidget(title_label)
        layout.addWidget(description)
        layout.addSpacing(6)
        layout.addWidget(author)
        layout.addWidget(copyright_label)
        layout.addWidget(storage)
        layout.addSpacing(8)
        layout.addLayout(button_row)

        self.setStyleSheet(
            """
            QLabel#aboutTitle {
                font-size: 20px;
                font-weight: 700;
            }
            QPushButton {
                background: #5f6f52;
                color: white;
                border: 0;
                border-radius: 6px;
                padding: 7px 18px;
            }
            QPushButton:hover {
                background: #4d5e42;
            }
            """
        )


class TrashDialog(QDialog):
    def __init__(self, store: NoteStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Trash")
        self.setMinimumSize(460, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.note_list = QListWidget()
        self.note_list.currentItemChanged.connect(self.update_buttons)

        button_row = QHBoxLayout()
        self.restore_button = QPushButton("Restore")
        self.restore_button.clicked.connect(self.restore_selected_note)
        self.delete_button = QPushButton("Delete permanently")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self.delete_selected_note_permanently)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        button_row.addWidget(self.restore_button)
        button_row.addWidget(self.delete_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)

        layout.addWidget(self.note_list, 1)
        layout.addLayout(button_row)
        self.refresh()
        self.update_buttons()

    def refresh(self) -> None:
        self.note_list.clear()
        for note in self.store.list_trashed_notes():
            title = note.title or "Untitled note"
            deleted = note.deleted_at[:10] if note.deleted_at else "Unknown date"
            item = QListWidgetItem(f"{title}    Deleted {deleted}")
            item.setData(Qt.UserRole, note.id)
            self.note_list.addItem(item)
        if self.note_list.count() == 0:
            item = QListWidgetItem("Trash is empty.")
            item.setFlags(Qt.NoItemFlags)
            self.note_list.addItem(item)

    def selected_note_id(self) -> int | None:
        item = self.note_list.currentItem()
        if item is None:
            return None
        note_id = item.data(Qt.UserRole)
        return int(note_id) if note_id is not None else None

    def update_buttons(self) -> None:
        has_note = self.selected_note_id() is not None
        self.restore_button.setEnabled(has_note)
        self.delete_button.setEnabled(has_note)

    def restore_selected_note(self) -> None:
        note_id = self.selected_note_id()
        if note_id is None:
            return
        self.store.restore_note(note_id)
        self.refresh()
        self.update_buttons()

    def delete_selected_note_permanently(self) -> None:
        note_id = self.selected_note_id()
        if note_id is None:
            return
        try:
            note = self.store.get_note(note_id)
            title = note.title or "Untitled note"
        except KeyError:
            return
        answer = QMessageBox.question(
            self,
            "Delete permanently",
            f"Permanently delete \"{title}\"?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.store.permanently_delete_note(note_id)
        self.refresh()
        self.update_buttons()


class ReminderWindow(QWidget):
    delete_requested = Signal(int, QWidget)
    dismissed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.note_id: int | None = None
        self.setWindowTitle("Priority Note")
        self.setMinimumSize(420, 320)
        self.resize(520, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.title_label = QLabel()
        self.title_label.setObjectName("reminderTitle")
        self.title_label.setWordWrap(True)

        self.body_view = QTextEdit()
        self.body_view.setReadOnly(True)

        interval_row = QHBoxLayout()
        interval_label = QLabel("Remind every")
        self.interval_combo = QComboBox()
        self.interval_combo.setToolTip("Reminder interval")
        for minutes in REMINDER_OPTIONS:
            self.interval_combo.addItem(reminder_label(minutes), minutes)
        interval_row.addStretch(1)
        interval_row.addWidget(interval_label)
        interval_row.addWidget(self.interval_combo)
        interval_row.addStretch(1)

        button_row = QHBoxLayout()
        dismiss_button = QPushButton("Dismiss")
        dismiss_button.clicked.connect(self.dismiss)
        delete_button = QPushButton("Delete")
        delete_button.setObjectName("dangerButton")
        delete_button.clicked.connect(self.request_delete)
        button_row.addStretch(1)
        button_row.addWidget(dismiss_button)
        button_row.addWidget(delete_button)
        button_row.addStretch(1)

        layout.addWidget(self.title_label)
        layout.addWidget(self.body_view, 1)
        layout.addLayout(interval_row)
        layout.addLayout(button_row)

        self.setStyleSheet(
            """
            QWidget {
                background: #fff4a3;
                color: #2c271d;
                font-family: Segoe UI;
                font-size: 10pt;
            }
            QLabel#reminderTitle {
                font-size: 16px;
                font-weight: 700;
            }
            QTextEdit {
                background: #fffbea;
                border: 1px solid #d2c39d;
                border-radius: 6px;
                padding: 8px;
            }
            QPushButton {
                background: #5f6f52;
                color: white;
                border: 0;
                border-radius: 6px;
                padding: 8px 18px;
            }
            QPushButton:hover {
                background: #4d5e42;
            }
            QPushButton#dangerButton {
                background: #9d3f35;
            }
            QPushButton#dangerButton:hover {
                background: #84352d;
            }
            """
        )

    def show_note(self, note: Note) -> None:
        self.note_id = note.id
        self.title_label.setText(note.title or "Priority note")
        self.body_view.setHtml(note.body_html)
        interval_index = self.interval_combo.findData(max(REMINDER_OPTIONS[0], note.reminder_minutes))
        self.interval_combo.setCurrentIndex(max(0, interval_index))
        self._center_on_screen()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._force_topmost()

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())

    def _force_topmost(self) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.AllowSetForegroundWindow(-1)
            user32.ShowWindow(hwnd, 9)
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def request_delete(self) -> None:
        if self.note_id is not None:
            self.delete_requested.emit(self.note_id, self)

    def dismiss(self) -> None:
        if self.note_id is not None:
            self.dismissed.emit(self.note_id, int(self.interval_combo.currentData() or REMINDER_OPTIONS[0]))
        self.hide()


class NoteCard(QFrame):
    opened = Signal(int)
    delete_requested = Signal(int)

    def __init__(self, note: Note, selected: bool = False, reminder_remaining: str = "") -> None:
        super().__init__()
        self.note = note
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("noteCard")
        if selected:
            border = "3px solid #4eeb00"
        elif note.is_priority:
            border = "2px solid #9d3f35"
        else:
            border = "1px solid rgba(92, 74, 31, 0.25)"
        self.setStyleSheet(
            f"""
            QFrame#noteCard {{
                background: {note.color};
                border: {border};
                border-radius: 7px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_prefix = "* " if note.is_pinned else ""
        title = QLabel(f"{title_prefix}{note.title or 'Untitled note'}")
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        title.setFixedHeight(36)
        title_row.addWidget(title, 1)
        if note.is_priority:
            badge = QLabel("Priority")
            badge.setObjectName("priorityBadge")
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedSize(58, 22)
            title_row.addWidget(badge)

        preview = QLabel(html_to_text(note.body_html) or "Click to select...")
        preview.setObjectName("cardPreview")
        preview.setTextFormat(Qt.PlainText)
        preview.setWordWrap(True)
        preview.setAlignment(Qt.AlignTop)
        preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(4)
        footer = QLabel(note.created_at[:10])
        footer.setObjectName("cardFooter")
        footer.setFixedHeight(20)
        self.footer_remaining = QLabel()
        self.footer_remaining.setObjectName("cardFooter")
        self.footer_remaining.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.footer_remaining.setFixedHeight(20)
        self.set_reminder_remaining(reminder_remaining)
        footer_row.addWidget(footer, 1)
        footer_row.addWidget(self.footer_remaining, 0)

        layout.addLayout(title_row)
        layout.addWidget(preview, 1)
        layout.addLayout(footer_row)

    def set_reminder_remaining(self, reminder_remaining: str) -> None:
        self.footer_remaining.setText(reminder_remaining)
        self.footer_remaining.setVisible(bool(reminder_remaining))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self.note.id is not None:
            self.opened.emit(self.note.id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        if self.note.id is None:
            return
        menu = QMenu(self)
        delete_action = menu.addAction("Delete")
        chosen_action = menu.exec(event.globalPos())
        if chosen_action == delete_action:
            self.delete_requested.emit(self.note.id)


class MainWindow(QMainWindow):
    def __init__(self, single_instance_server: QLocalServer | None = None) -> None:
        super().__init__()
        self.settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        self.store = NoteStore()
        self.single_instance_server = single_instance_server
        if self.single_instance_server is not None:
            self.single_instance_server.newConnection.connect(self.handle_single_instance_connection)
        self.current_note: Note | None = None
        self.loading_note = False
        self.editing_note = False
        self.reminder_window: ReminderWindow | None = None
        self.active_reminder_note_id: int | None = None
        self.pending_reminder_note_ids: list[int] = []
        self.reminder_timers: dict[int, QTimer] = {}
        self.reminder_intervals: dict[int, int] = {}
        self.reminder_starts: dict[int, str] = {}
        self.card_widgets: dict[int, NoteCard] = {}
        self.card_order: list[int] = []
        self.empty_grid_label: QLabel | None = None
        self.is_quitting = False
        self.tray_notice_shown = False

        self.setWindowTitle("Sticky Notes")
        icon_path = resource_path("assets/StickyNotes.ico")
        self.app_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        if icon_path.exists():
            self.setWindowIcon(self.app_icon)

        self.resize_save_timer = QTimer(self)
        self.resize_save_timer.setSingleShot(True)
        self.resize_save_timer.setInterval(500)
        self.resize_save_timer.timeout.connect(self.save_window_size)
        self.resize(self.saved_window_size())

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(650)
        self.autosave_timer.timeout.connect(self.save_current_note)

        self.selection_idle_timer = QTimer(self)
        self.selection_idle_timer.setSingleShot(True)
        self.selection_idle_timer.setInterval(3 * 60 * 1000)
        self.selection_idle_timer.timeout.connect(self.deselect_current_note)

        self.countdown_refresh_timer = QTimer(self)
        self.countdown_refresh_timer.setInterval(30 * 1000)
        self.countdown_refresh_timer.timeout.connect(self.update_visible_countdowns)
        self.countdown_refresh_timer.start()

        self._build_ui()
        self._apply_styles()
        self._setup_tray_icon()
        if self.tray_icon is None:
            self.refresh_grid()
        self.initialize_priority_reminders()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        header = QHBoxLayout()
        app_title = QLabel("Sticky Notes")
        app_title.setObjectName("appTitle")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search notes")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(220)
        self.search_input.textChanged.connect(self.refresh_grid)

        new_button = QPushButton("New Note")
        new_button.setToolTip("New note")
        new_button.setObjectName("iconButton")
        new_button.clicked.connect(self.create_note)

        header.addWidget(app_title)
        header.addStretch(1)
        header.addWidget(self.search_input, 0)
        header.addWidget(new_button)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_grid_area())
        splitter.addWidget(self._build_editor())
        splitter.setSizes([770, 380])

        root_layout.addLayout(header)
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

    def _setup_tray_icon(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        tray_menu = QMenu(self)
        open_action = QAction("Open Sticky Notes", self)
        open_action.triggered.connect(self.show_main_window)
        new_action = QAction("New Note", self)
        new_action.triggered.connect(self.create_note_from_tray)
        trash_action = QAction("Trash", self)
        trash_action.triggered.connect(self.show_trash_dialog)
        options_action = QAction("Options", self)
        options_action.triggered.connect(self.show_options_dialog)
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.exit_application)

        tray_menu.addAction(open_action)
        tray_menu.addAction(new_action)
        tray_menu.addAction(trash_action)
        tray_menu.addAction(options_action)
        tray_menu.addAction(about_action)
        tray_menu.addSeparator()
        tray_menu.addAction(exit_action)

        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("Sticky Notes")
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.handle_tray_activated)
        self.tray_icon.show()

    def _build_grid_area(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.grid_host = QWidget()
        self.grid_layout = QGridLayout(self.grid_host)
        self.grid_layout.setContentsMargins(0, 0, 8, 8)
        self.grid_layout.setHorizontalSpacing(14)
        self.grid_layout.setVerticalSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll_area.setWidget(self.grid_host)
        layout.addWidget(self.scroll_area)
        return container

    def _build_editor(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("editorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        panel_header = QHBoxLayout()
        editor_title = QLabel("Selected Note")
        editor_title.setObjectName("panelTitle")
        self.saved_label = QLabel("No note selected")
        self.saved_label.setObjectName("savedLabel")
        self.close_note_button = QPushButton("Close")
        self.close_note_button.setObjectName("secondaryButton")
        self.close_note_button.setToolTip("Close selected note")
        self.close_note_button.clicked.connect(self.deselect_current_note)
        self.edit_note_button = QPushButton("Edit")
        self.edit_note_button.setObjectName("secondaryButton")
        self.edit_note_button.setToolTip("Edit selected note")
        self.edit_note_button.clicked.connect(self.begin_editing_current_note)
        panel_header.addWidget(editor_title)
        panel_header.addStretch(1)
        panel_header.addWidget(self.saved_label)
        panel_header.addWidget(self.edit_note_button)
        panel_header.addWidget(self.close_note_button)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Title")
        self.title_input.textChanged.connect(self.queue_autosave)

        self.body_editor = QTextEdit()
        self.body_editor.setPlaceholderText("Write your note...")
        self.body_editor.textChanged.connect(self.queue_autosave)

        self.editor_toolbar = QToolBar()
        self.editor_toolbar.setIconSize(self.editor_toolbar.iconSize())
        self._add_text_actions(self.editor_toolbar)

        tags_label = QLabel("Tags")
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("work, ideas, reminders")
        self.tags_input.textChanged.connect(self.queue_autosave)

        priority_row = QHBoxLayout()
        self.priority_checkbox = QCheckBox("Priority")
        self.priority_checkbox.toggled.connect(self.set_priority_controls_enabled)
        self.reminder_combo = QComboBox()
        self.reminder_combo.setToolTip("Reminder interval")
        self.reminder_combo.setFixedWidth(94)
        for minutes in REMINDER_OPTIONS:
            self.reminder_combo.addItem(reminder_label(minutes), minutes)
        self.reminder_combo.currentIndexChanged.connect(self.queue_autosave)

        start_date_row = QHBoxLayout()
        self.reminder_start_checkbox = QCheckBox("Start at")
        self.reminder_start_checkbox.toggled.connect(self.set_reminder_start_enabled)
        self.reminder_start_date = ReminderStartDateEdit()
        reminder_calendar = ReminderCalendarWidget()
        reminder_calendar.setDateEditEnabled(True)
        reminder_calendar.setSelectedDate(self.reminder_start_date.date())
        self.reminder_start_date.setCalendarPopup(True)
        self.reminder_start_date.setCalendarWidget(reminder_calendar)
        self.reminder_start_date.setDisplayFormat("MM-dd-yyyy")
        self.reminder_start_date.setFixedWidth(118)
        self.reminder_start_date.dateChanged.connect(self.queue_autosave)

        start_time_row = QHBoxLayout()
        self.reminder_start_hour = QComboBox()
        self.reminder_start_hour.setToolTip("Reminder hour")
        self.reminder_start_hour.setFixedWidth(48)
        for hour in range(1, 13):
            self.reminder_start_hour.addItem(str(hour), hour)
        self.reminder_start_hour.currentIndexChanged.connect(self.queue_autosave)
        self.reminder_start_minute = QComboBox()
        self.reminder_start_minute.setToolTip("Reminder minute")
        self.reminder_start_minute.setFixedWidth(52)
        for minute in range(0, 60, 5):
            self.reminder_start_minute.addItem(f"{minute:02d}", minute)
        self.reminder_start_minute.currentIndexChanged.connect(self.queue_autosave)
        self.reminder_start_ampm = QComboBox()
        self.reminder_start_ampm.setToolTip("AM or PM")
        self.reminder_start_ampm.setFixedWidth(58)
        self.reminder_start_ampm.addItems(["AM", "PM"])
        self.reminder_start_ampm.currentIndexChanged.connect(self.queue_autosave)
        self.set_reminder_start_controls(default_reminder_start())
        priority_row.addWidget(self.priority_checkbox)
        priority_row.addWidget(self.reminder_combo)
        priority_row.addStretch(1)
        start_date_row.addWidget(self.reminder_start_checkbox)
        start_date_row.addWidget(self.reminder_start_date)
        start_date_row.addStretch(1)
        start_time_row.addSpacing(84)
        start_time_row.addWidget(self.reminder_start_hour)
        start_time_row.addWidget(QLabel(":"))
        start_time_row.addWidget(self.reminder_start_minute)
        start_time_row.addWidget(self.reminder_start_ampm)
        start_time_row.addStretch(1)

        meta_row = QHBoxLayout()
        self.pin_checkbox = QCheckBox("Pinned")
        self.pin_checkbox.toggled.connect(self.queue_autosave)
        self.color_buttons: list[QPushButton] = []
        for color in DEFAULT_COLORS:
            button = QPushButton()
            button.setFixedSize(24, 24)
            button.setToolTip(color)
            button.setStyleSheet(f"background: {color}; border: 1px solid #8f8058; border-radius: 12px;")
            button.clicked.connect(partial(self.set_note_color, color))
            self.color_buttons.append(button)

        self.custom_color_button = QPushButton("...")
        self.custom_color_button.setFixedSize(34, 24)
        self.custom_color_button.setToolTip("Choose custom note color")
        self.custom_color_button.clicked.connect(self.choose_custom_color)

        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self.delete_current_note)

        meta_row.addWidget(self.pin_checkbox)
        meta_row.addSpacing(8)
        for button in self.color_buttons:
            meta_row.addWidget(button)
        meta_row.addWidget(self.custom_color_button)
        meta_row.addStretch(1)
        meta_row.addWidget(self.delete_button)

        self.created_label = QLabel("")
        self.created_label.setObjectName("metaLabel")

        layout.addLayout(panel_header)
        layout.addWidget(self.title_input)
        layout.addWidget(self.editor_toolbar)
        layout.addWidget(self.body_editor, 1)
        layout.addWidget(tags_label)
        layout.addWidget(self.tags_input)
        layout.addLayout(priority_row)
        layout.addLayout(start_date_row)
        layout.addLayout(start_time_row)
        layout.addLayout(meta_row)
        layout.addWidget(self.created_label)

        self._set_editor_enabled(False)
        return panel

    def _add_text_actions(self, toolbar: QToolBar) -> None:
        bold = QAction("B", self)
        bold.setCheckable(True)
        bold.setToolTip("Bold")
        bold.triggered.connect(lambda checked: self.body_editor.setFontWeight(700 if checked else 400))

        italic = QAction("I", self)
        italic.setCheckable(True)
        italic.setToolTip("Italic")
        italic.triggered.connect(self.body_editor.setFontItalic)

        underline = QAction("U", self)
        underline.setCheckable(True)
        underline.setToolTip("Underline")
        underline.triggered.connect(self.body_editor.setFontUnderline)

        bullet = QAction("Bullets", self)
        bullet.setToolTip("Bulleted list")
        bullet.triggered.connect(self.toggle_bullets)

        color = QAction("Text color", self)
        color.setToolTip("Text color")
        color.triggered.connect(self.choose_text_color)

        toolbar.addAction(bold)
        toolbar.addAction(italic)
        toolbar.addAction(underline)
        toolbar.addSeparator()
        toolbar.addAction(bullet)
        toolbar.addAction(color)

    def _set_editor_enabled(self, enabled: bool) -> None:
        if not enabled:
            self.editing_note = False
        for widget in (self.title_input, self.body_editor, self.tags_input, self.close_note_button):
            widget.setEnabled(enabled)
        self.edit_note_button.setEnabled(enabled and not self.editing_note)
        self._set_editor_editable(enabled and self.editing_note)

    def _set_editor_editable(self, editable: bool) -> None:
        self.editing_note = editable and self.current_note is not None
        selected = self.current_note is not None

        self.title_input.setReadOnly(not self.editing_note)
        self.body_editor.setReadOnly(not self.editing_note)
        self.tags_input.setReadOnly(not self.editing_note)
        self.editor_toolbar.setEnabled(self.editing_note)

        for widget in (self.pin_checkbox, self.priority_checkbox, self.delete_button):
            widget.setEnabled(self.editing_note)
        for button in self.color_buttons:
            button.setEnabled(self.editing_note)
        self.custom_color_button.setEnabled(self.editing_note)

        self.edit_note_button.setEnabled(selected and not self.editing_note)
        self.close_note_button.setEnabled(selected)
        priority_enabled = self.editing_note and self.priority_checkbox.isChecked()
        self.reminder_combo.setEnabled(priority_enabled)
        self.reminder_start_checkbox.setEnabled(priority_enabled)
        self.set_reminder_start_inputs_enabled(priority_enabled and self.reminder_start_checkbox.isChecked())

    def set_reminder_start_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.reminder_start_date,
            self.reminder_start_hour,
            self.reminder_start_minute,
            self.reminder_start_ampm,
        ):
            widget.setEnabled(enabled)

    def set_reminder_start_controls(self, value: QDateTime) -> None:
        self.reminder_start_date.setDate(value.date())
        self.reminder_start_date.focus_calendar_on_selected_date()
        hour_24 = value.time().hour()
        minute = round(value.time().minute() / 5) * 5
        if minute == 60:
            minute = 0
            hour_24 = (hour_24 + 1) % 24
        ampm = "AM" if hour_24 < 12 else "PM"
        hour_12 = hour_24 % 12 or 12
        self.reminder_start_hour.setCurrentIndex(max(0, self.reminder_start_hour.findData(hour_12)))
        self.reminder_start_minute.setCurrentIndex(max(0, self.reminder_start_minute.findData(minute)))
        self.reminder_start_ampm.setCurrentText(ampm)

    def reminder_start_iso(self) -> str:
        selected_date = self.reminder_start_date.date()
        hour = int(self.reminder_start_hour.currentData() or 12)
        minute = int(self.reminder_start_minute.currentData() or 0)
        if self.reminder_start_ampm.currentText() == "AM":
            hour_24 = 0 if hour == 12 else hour
        else:
            hour_24 = 12 if hour == 12 else hour + 12
        value = datetime(selected_date.year(), selected_date.month(), selected_date.day(), hour_24, minute)
        return value.isoformat()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f7f3e7;
                color: #2c271d;
                font-family: Segoe UI;
                font-size: 10pt;
            }
            QLabel#appTitle {
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#panelTitle {
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#savedLabel, QLabel#metaLabel, QLabel#cardFooter {
                color: #6f6656;
                font-size: 9pt;
            }
            QLabel#cardTitle {
                font-weight: 700;
                font-size: 11pt;
            }
            QLabel#cardPreview {
                color: #403827;
            }
            QLabel#priorityBadge {
                background: #9d3f35;
                color: white;
                border-radius: 4px;
                font-size: 8pt;
                font-weight: 700;
            }
            QFrame#editorPanel {
                background: #fffaf0;
                border: 1px solid #d8caa8;
                border-radius: 8px;
            }
            QLineEdit, QTextEdit {
                background: #fffefa;
                border: 1px solid #d2c39d;
                border-radius: 6px;
                padding: 7px;
            }
            QPushButton {
                background: #5f6f52;
                color: white;
                border: 0;
                border-radius: 6px;
                padding: 7px 10px;
            }
            QPushButton:hover {
                background: #4d5e42;
            }
            QPushButton#iconButton {
                min-width: 86px;
                font-weight: 700;
            }
            QPushButton#dangerButton {
                background: #9d3f35;
            }
            QPushButton#dangerButton:hover {
                background: #84352d;
            }
            QPushButton#secondaryButton {
                background: #ece2c8;
                color: #2c271d;
                border: 1px solid #c7b88f;
            }
            QPushButton#secondaryButton:hover {
                background: #e3d5b5;
            }
            QToolBar {
                background: transparent;
                border: 0;
                spacing: 4px;
            }
            QToolButton {
                background: #ece2c8;
                border: 1px solid #c7b88f;
                border-radius: 5px;
                padding: 5px;
            }
            QToolButton:hover {
                background: #e3d5b5;
            }
            """
        )

    def refresh_grid(self) -> None:
        if not hasattr(self, "grid_layout"):
            return
        self.clear_grid_layout(delete_widgets=True)
        self.card_widgets = {}
        self.card_order = []
        self.empty_grid_label = None

        notes = self.store.list_notes(self.search_input.text())
        if not notes:
            self.empty_grid_label = QLabel("No notes yet. Press New Note to create one.")
            self.empty_grid_label.setObjectName("metaLabel")
            self.grid_layout.addWidget(self.empty_grid_label, 0, 0)
            return

        selected_id = self.current_note.id if self.current_note is not None else None
        for note in notes:
            card = NoteCard(note, selected=note.id == selected_id, reminder_remaining=self.reminder_remaining_text(note))
            card.opened.connect(self.open_note)
            card.delete_requested.connect(self.confirm_delete_note_by_id)
            if note.id is not None:
                self.card_widgets[note.id] = card
                self.card_order.append(note.id)
        self.layout_grid_cards()

    def clear_grid_layout(self, delete_widgets: bool = False) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and delete_widgets:
                widget.hide()
                widget.deleteLater()

    def layout_grid_cards(self) -> None:
        if not hasattr(self, "grid_layout"):
            return
        if self.empty_grid_label is not None:
            return
        self.clear_grid_layout(delete_widgets=False)
        available_width = max(self.scroll_area.viewport().width() - 28, CARD_WIDTH)
        columns = max(1, available_width // (CARD_WIDTH + 14))
        for index, note_id in enumerate(self.card_order):
            card = self.card_widgets.get(note_id)
            if card is not None:
                self.grid_layout.addWidget(card, index // columns, index % columns)

    def update_visible_countdowns(self) -> None:
        if not self.isVisible() or self.isMinimized():
            return
        for note_id, card in list(self.card_widgets.items()):
            if card is None:
                continue
            try:
                note = self.store.get_note(note_id)
            except KeyError:
                continue
            card.set_reminder_remaining(self.reminder_remaining_text(note))

    def reminder_remaining_text(self, note: Note) -> str:
        if note.id is None or not note.is_priority:
            return ""
        start_at = self.parse_reminder_start(note.reminder_start_at)
        now = datetime.now()
        if start_at is not None and start_at > now:
            remaining_seconds = (start_at - now).total_seconds()
            days = max(1, int((remaining_seconds + 86_399) // 86_400))
            return "1 day" if days == 1 else f"{days} days"
        timer = self.reminder_timers.get(note.id)
        if timer is None or not timer.isActive():
            return ""
        remaining_ms = timer.remainingTime()
        if remaining_ms <= 0:
            return "Due"
        minutes = max(1, (remaining_ms + 59_999) // 60_000)
        if minutes < 60:
            return f"{minutes} min"
        hours, extra_minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours} hr" if extra_minutes == 0 else f"{hours}h {extra_minutes}m"
        days, extra_hours = divmod(hours, 24)
        return f"{days} day" if extra_hours == 0 else f"{days}d {extra_hours}h"

    def saved_window_size(self) -> QSize:
        size = self.settings.value("main_window/size", DEFAULT_WINDOW_SIZE)
        if isinstance(size, QSize) and size.isValid():
            return size
        return DEFAULT_WINDOW_SIZE

    def save_window_size(self) -> None:
        if not self.isMinimized():
            self.settings.setValue("main_window/size", self.size())

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "resize_save_timer") and self.isVisible() and not self.isMinimized():
            self.resize_save_timer.start()
            self.layout_grid_cards()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self.refresh_grid)

    def handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_main_window()

    def show_main_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh_grid()

    def handle_single_instance_connection(self) -> None:
        if self.single_instance_server is None:
            return
        while self.single_instance_server.hasPendingConnections():
            socket = self.single_instance_server.nextPendingConnection()
            if socket is None:
                continue
            socket.waitForReadyRead(100)
            socket.readAll()
            socket.disconnectFromServer()
        QTimer.singleShot(0, self.show_main_window)

    def create_note_from_tray(self) -> None:
        self.show_main_window()
        self.create_note()

    def show_options_dialog(self) -> None:
        dialog = OptionsDialog(self)
        dialog.setWindowIcon(self.app_icon)
        dialog.exec()

    def show_about_dialog(self) -> None:
        dialog = AboutDialog(self.app_icon, self)
        dialog.exec()

    def show_trash_dialog(self) -> None:
        dialog = TrashDialog(self.store, self)
        dialog.setWindowIcon(self.app_icon)
        dialog.exec()
        self.refresh_grid()
        self.initialize_priority_reminders()

    def create_note(self) -> None:
        note = self.store.create_note()
        self.refresh_grid()
        if note.id is not None:
            self.open_note(note.id)
            self.begin_editing_current_note()

    def open_note(self, note_id: int) -> None:
        if self.current_note is not None and self.current_note.id == note_id:
            self.reset_selection_idle_timer()
            self.refresh_grid()
            return
        if self.autosave_timer.isActive():
            self.autosave_timer.stop()
            self.save_current_note()
        self.loading_note = True
        self.editing_note = False
        self.current_note = self.store.get_note(note_id)

        self.title_input.setText(self.current_note.title)
        self.body_editor.setHtml(self.current_note.body_html)
        self.tags_input.setText(self.current_note.tags)
        self.pin_checkbox.setChecked(self.current_note.is_pinned)
        self.priority_checkbox.setChecked(self.current_note.is_priority)
        reminder_index = self.reminder_combo.findData(self.current_note.reminder_minutes)
        self.reminder_combo.setCurrentIndex(max(0, reminder_index))
        if self.current_note.reminder_start_at:
            start_at = QDateTime.fromString(self.current_note.reminder_start_at, Qt.ISODate)
            if not start_at.isValid():
                start_at = default_reminder_start()
            self.set_reminder_start_controls(start_at)
            self.reminder_start_checkbox.setChecked(True)
        else:
            self.set_reminder_start_controls(default_reminder_start())
            self.reminder_start_checkbox.setChecked(False)
        self.set_reminder_start_enabled(self.reminder_start_checkbox.isChecked())
        self.created_label.setText(
            f"Created {self.current_note.created_at[:10]} | Updated {self.current_note.updated_at[:10]}"
        )
        self.saved_label.setText("View only")
        self.loading_note = False
        self._set_editor_enabled(True)
        self.reset_selection_idle_timer()
        self.refresh_grid()

    def begin_editing_current_note(self) -> None:
        if self.current_note is None:
            return
        self._set_editor_editable(True)
        self.saved_label.setText("Editing")
        self.reset_selection_idle_timer()

    def queue_autosave(self) -> None:
        if self.loading_note or self.current_note is None or not self.editing_note:
            return
        self.reset_selection_idle_timer()
        self.saved_label.setText("Saving...")
        self.autosave_timer.start()

    def save_current_note(self) -> None:
        if self.current_note is None:
            return
        self.current_note.title = self.title_input.text()
        self.current_note.body_html = self.body_editor.toHtml()
        self.current_note.tags = self.tags_input.text()
        self.current_note.is_pinned = self.pin_checkbox.isChecked()
        self.current_note.is_priority = self.priority_checkbox.isChecked()
        self.current_note.reminder_minutes = int(self.reminder_combo.currentData() or REMINDER_OPTIONS[0])
        self.current_note.reminder_start_at = (
            self.reminder_start_iso()
            if self.reminder_start_checkbox.isChecked()
            else ""
        )
        self.current_note = self.store.save_note(self.current_note)
        self.sync_priority_reminder(self.current_note)
        self.saved_label.setText("Saved" if self.editing_note else "View only")
        self.created_label.setText(
            f"Created {self.current_note.created_at[:10]} | Updated {self.current_note.updated_at[:10]}"
        )
        self.refresh_grid()

    def reset_selection_idle_timer(self) -> None:
        if self.current_note is None:
            self.selection_idle_timer.stop()
            return
        self.selection_idle_timer.start()

    def deselect_current_note(self) -> None:
        if self.current_note is None:
            return
        if self.autosave_timer.isActive():
            self.autosave_timer.stop()
            self.save_current_note()

        self.loading_note = True
        self.editing_note = False
        self.current_note = None
        self.title_input.clear()
        self.body_editor.clear()
        self.tags_input.clear()
        self.priority_checkbox.setChecked(False)
        self.reminder_combo.setCurrentIndex(0)
        self.reminder_start_checkbox.setChecked(False)
        self.set_reminder_start_controls(default_reminder_start())
        self.set_reminder_start_enabled(False)
        self.pin_checkbox.setChecked(False)
        self.created_label.clear()
        self.saved_label.setText("No note selected")
        self._set_editor_enabled(False)
        self.loading_note = False
        self.refresh_grid()

    def set_note_color(self, color: str) -> None:
        if self.current_note is None or not self.editing_note:
            return
        self.current_note.color = color
        self.queue_autosave()

    def set_reminder_start_enabled(self, enabled: bool) -> None:
        self.set_reminder_start_inputs_enabled(
            enabled
            and self.editing_note
            and self.reminder_start_checkbox.isEnabled()
            and self.priority_checkbox.isChecked()
        )
        if not self.loading_note and self.editing_note:
            self.queue_autosave()

    def set_priority_controls_enabled(self, enabled: bool) -> None:
        self.reminder_combo.setEnabled(enabled and self.editing_note and self.priority_checkbox.isEnabled())
        self.reminder_start_checkbox.setEnabled(enabled and self.editing_note and self.priority_checkbox.isEnabled())
        if not enabled:
            self.set_reminder_start_inputs_enabled(False)
        else:
            self.set_reminder_start_inputs_enabled(self.editing_note and self.reminder_start_checkbox.isChecked())
        if not self.loading_note and self.editing_note:
            self.queue_autosave()

    def choose_custom_color(self) -> None:
        if self.current_note is None or not self.editing_note:
            return
        chosen = QColorDialog.getColor(QColor(self.current_note.color), self, "Choose note color")
        if chosen.isValid():
            self.set_note_color(chosen.name())

    def choose_text_color(self) -> None:
        if not self.editing_note:
            return
        chosen = QColorDialog.getColor(parent=self, title="Choose text color")
        if not chosen.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(chosen)
        self.body_editor.textCursor().mergeCharFormat(fmt)

    def toggle_bullets(self) -> None:
        if not self.editing_note:
            return
        cursor = self.body_editor.textCursor()
        cursor.beginEditBlock()
        block_format = cursor.blockFormat()
        list_format = QTextListFormat()
        list_format.setStyle(QTextListFormat.ListDisc)
        cursor.setBlockFormat(block_format)
        cursor.createList(list_format)
        cursor.endEditBlock()

    def delete_current_note(self) -> None:
        if self.current_note is None or self.current_note.id is None or not self.editing_note:
            return
        self.confirm_delete_note_by_id(self.current_note.id)

    def confirm_delete_note_by_id(self, note_id: int) -> None:
        try:
            note = self.store.get_note(note_id)
        except KeyError:
            return
        answer = QMessageBox.question(
            self,
            "Delete note",
            f"Delete \"{note.title or 'Untitled note'}\"?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.delete_note_by_id(note_id)

    def delete_note_by_id(self, note_id: int) -> None:
        timer = self.reminder_timers.pop(note_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self.reminder_intervals.pop(note_id, None)
        self.reminder_starts.pop(note_id, None)
        self.pending_reminder_note_ids = [pending_id for pending_id in self.pending_reminder_note_ids if pending_id != note_id]
        deleted_active_reminder = self.active_reminder_note_id == note_id
        if deleted_active_reminder:
            self.active_reminder_note_id = None
            if self.reminder_window is not None:
                self.reminder_window.hide()

        self.store.delete_note(note_id)
        if self.current_note is not None and self.current_note.id == note_id:
            self.editing_note = False
            self.current_note = None
            self.selection_idle_timer.stop()
            self.title_input.clear()
            self.body_editor.clear()
            self.tags_input.clear()
            self.priority_checkbox.setChecked(False)
            self.reminder_combo.setCurrentIndex(0)
            self.reminder_start_checkbox.setChecked(False)
            self.set_reminder_start_controls(default_reminder_start())
            self.set_reminder_start_enabled(False)
            self.created_label.clear()
            self.saved_label.setText("No note selected")
            self._set_editor_enabled(False)
        self.refresh_grid()
        if deleted_active_reminder:
            QTimer.singleShot(0, self.show_next_queued_reminder)

    def initialize_priority_reminders(self) -> None:
        for note in self.store.list_notes():
            self.sync_priority_reminder(note)
            if self.is_missed_start_reminder(note):
                QTimer.singleShot(0, partial(self.show_missed_start_reminder, note.id))

    def is_missed_start_reminder(self, note: Note) -> bool:
        start_at = self.parse_reminder_start(note.reminder_start_at)
        return note.id is not None and note.is_priority and start_at is not None and start_at <= datetime.now()

    def show_missed_start_reminder(self, note_id: int) -> None:
        try:
            note = self.store.get_note(note_id)
        except KeyError:
            return
        if not self.is_missed_start_reminder(note):
            return
        self.show_priority_reminder(note)

    def sync_priority_reminder(self, note: Note) -> None:
        if note.id is None:
            return
        if not note.is_priority:
            timer = self.reminder_timers.pop(note.id, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            self.reminder_intervals.pop(note.id, None)
            self.reminder_starts.pop(note.id, None)
            self.pending_reminder_note_ids = [
                pending_id for pending_id in self.pending_reminder_note_ids if pending_id != note.id
            ]
            if self.active_reminder_note_id == note.id:
                self.active_reminder_note_id = None
                if self.reminder_window is not None:
                    self.reminder_window.hide()
                QTimer.singleShot(0, self.show_next_queued_reminder)
            return

        interval = max(REMINDER_OPTIONS[0], note.reminder_minutes)
        previous_interval = self.reminder_intervals.get(note.id)
        previous_start = self.reminder_starts.get(note.id)
        timer = self.reminder_timers.get(note.id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(partial(self.handle_priority_timeout, note.id))
            self.reminder_timers[note.id] = timer
        if note.id == self.active_reminder_note_id or note.id in self.pending_reminder_note_ids:
            timer.stop()
            self.reminder_intervals[note.id] = interval
            self.reminder_starts[note.id] = note.reminder_start_at
            return
        if not timer.isActive() or previous_interval != interval or previous_start != note.reminder_start_at:
            timer.start(self.next_reminder_delay_ms(note))
        self.reminder_intervals[note.id] = interval
        self.reminder_starts[note.id] = note.reminder_start_at

    def next_reminder_delay_ms(self, note: Note) -> int:
        start_at = self.parse_reminder_start(note.reminder_start_at)
        if start_at is not None:
            seconds_until_start = (start_at - datetime.now()).total_seconds()
            if seconds_until_start > 0:
                return min(MAX_QT_TIMER_MS, max(1000, int(seconds_until_start * 1000)))
        return max(1000, max(REMINDER_OPTIONS[0], note.reminder_minutes) * 60 * 1000)

    @staticmethod
    def parse_reminder_start(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def handle_priority_timeout(self, note_id: int) -> None:
        try:
            note = self.store.get_note(note_id)
        except KeyError:
            timer = self.reminder_timers.pop(note_id, None)
            if timer is not None:
                timer.deleteLater()
            self.reminder_intervals.pop(note_id, None)
            self.reminder_starts.pop(note_id, None)
            return
        if not note.is_priority:
            self.sync_priority_reminder(note)
            return
        start_at = self.parse_reminder_start(note.reminder_start_at)
        if start_at is not None and start_at > datetime.now():
            timer = self.reminder_timers.get(note_id)
            if timer is not None:
                timer.start(self.next_reminder_delay_ms(note))
            return
        self.show_priority_reminder(note)

    def show_priority_reminder(self, note: Note) -> None:
        if note.id is None:
            return
        if self.active_reminder_note_id is not None and self.active_reminder_note_id != note.id:
            if note.id not in self.pending_reminder_note_ids:
                self.pending_reminder_note_ids.append(note.id)
            self.stop_reminder_timer(note.id)
            return
        self.show_reminder_now(note)

    def show_reminder_now(self, note: Note) -> None:
        if note.id is None:
            return
        if self.reminder_window is None:
            self.reminder_window = ReminderWindow()
            self.reminder_window.delete_requested.connect(self.delete_note_from_reminder)
            self.reminder_window.dismissed.connect(self.update_interval_from_reminder)
        self.active_reminder_note_id = note.id
        self.stop_reminder_timer(note.id)
        self.reminder_window.show_note(note)

    def show_next_queued_reminder(self) -> None:
        while self.pending_reminder_note_ids:
            note_id = self.pending_reminder_note_ids.pop(0)
            try:
                note = self.store.get_note(note_id)
            except KeyError:
                continue
            if note.is_priority:
                self.show_reminder_now(note)
                return

    def stop_reminder_timer(self, note_id: int) -> None:
        timer = self.reminder_timers.get(note_id)
        if timer is not None:
            timer.stop()

    def update_interval_from_reminder(self, note_id: int, reminder_minutes: int) -> None:
        try:
            note = self.store.get_note(note_id)
        except KeyError:
            self.finish_active_reminder(note_id)
            return
        note.reminder_minutes = max(REMINDER_OPTIONS[0], reminder_minutes)

        if self.current_note is not None and self.current_note.id == note_id:
            self.current_note.reminder_minutes = note.reminder_minutes
            reminder_index = self.reminder_combo.findData(note.reminder_minutes)
            self.reminder_combo.setCurrentIndex(max(0, reminder_index))
            self.save_current_note()
            self.restart_reminder_from_now(self.current_note)
            self.finish_active_reminder(note_id)
            return

        note = self.store.save_note(note)
        self.sync_priority_reminder(note)
        self.restart_reminder_from_now(note)
        self.finish_active_reminder(note_id)

    def finish_active_reminder(self, note_id: int) -> None:
        if self.active_reminder_note_id == note_id:
            self.active_reminder_note_id = None
        self.pending_reminder_note_ids = [pending_id for pending_id in self.pending_reminder_note_ids if pending_id != note_id]
        QTimer.singleShot(0, self.show_next_queued_reminder)

    def restart_reminder_from_now(self, note: Note) -> None:
        if note.id is None or not note.is_priority:
            return
        timer = self.reminder_timers.get(note.id)
        if timer is None:
            self.sync_priority_reminder(note)
            timer = self.reminder_timers.get(note.id)
        if timer is not None:
            timer.start(max(REMINDER_OPTIONS[0], note.reminder_minutes) * 60 * 1000)

    def delete_note_from_reminder(self, note_id: int, reminder_window: QWidget | None = None) -> None:
        try:
            note = self.store.get_note(note_id)
            title = note.title or "this priority note"
        except KeyError:
            return
        parent = reminder_window or self
        confirmation = QMessageBox(parent)
        confirmation.setWindowTitle("Delete priority note")
        confirmation.setText(f"Delete \"{title}\"?")
        confirmation.setIcon(QMessageBox.Question)
        confirmation.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirmation.setDefaultButton(QMessageBox.No)
        confirmation.setWindowModality(Qt.WindowModal)
        confirmation.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        confirmation.show()
        confirmation.raise_()
        confirmation.activateWindow()
        answer = confirmation.exec()
        if answer != QMessageBox.Yes:
            return
        self.delete_note_by_id(note_id)

    def save_before_hiding(self) -> None:
        if self.autosave_timer.isActive():
            self.autosave_timer.stop()
            self.save_current_note()
        if self.resize_save_timer.isActive():
            self.resize_save_timer.stop()
        self.save_window_size()

    def exit_application(self) -> None:
        self.is_quitting = True
        self.close()

    def shutdown(self) -> None:
        self.save_before_hiding()
        self.selection_idle_timer.stop()
        self.countdown_refresh_timer.stop()
        for timer in self.reminder_timers.values():
            timer.stop()
        if self.reminder_window is not None:
            self.reminder_window.close()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        if self.single_instance_server is not None:
            self.single_instance_server.close()
            QLocalServer.removeServer(SINGLE_INSTANCE_SERVER_NAME)
        self.store.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self.is_quitting and self.tray_icon is not None:
            self.save_before_hiding()
            event.ignore()
            self.hide()
            if not self.tray_notice_shown:
                self.tray_icon.showMessage(
                    "Sticky Notes is still running",
                    "Use the tray icon to reopen Sticky Notes or exit.",
                    QSystemTrayIcon.Information,
                    3500,
                )
                self.tray_notice_shown = True
            return

        self.shutdown()
        super().closeEvent(event)
        QApplication.quit()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and self.isMinimized() and self.tray_icon is not None:
            self.save_before_hiding()
            QTimer.singleShot(0, self.hide)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if notify_existing_instance():
        return 0
    single_instance_server = create_single_instance_server()
    if single_instance_server is None and notify_existing_instance():
        return 0
    window = MainWindow(single_instance_server)
    if window.tray_icon is None:
        window.show()
    return app.exec()
