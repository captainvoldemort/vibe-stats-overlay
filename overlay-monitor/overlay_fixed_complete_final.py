import sys
import time
import platform
from functools import partial

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QProgressBar, QSizePolicy, QFrame, QTextBrowser, QDialog, QSlider,
    QGroupBox, QGridLayout, QRadioButton, QButtonGroup, QScrollArea
)
from PyQt5.QtCore import Qt, QTimer, QSize, QRect
from PyQt5.QtGui import QFont, QColor, QCursor, QPainter, QBrush

import psutil

# GPU libs: prefer pynvml (nvidia-ml-py3), fallback GPUtil
NVML = False
try:
    import pynvml
    pynvml.nvmlInit()
    NVML = True
except Exception:
    NVML = False

GPUtil_available = False
try:
    import GPUtil
    GPUtil_available = True
except Exception:
    GPUtil_available = False

is_windows = (platform.system() == "Windows")
if is_windows:
    import ctypes
    user32 = ctypes.windll.user32
    gwl_exstyle = -20
    ws_ex_layered = 0x80000
    ws_ex_toolwindow = 0x80

def enable_layered(hwnd):
    if not is_windows:
        return
    style = user32.GetWindowLongW(hwnd, gwl_exstyle)
    style |= ws_ex_layered | ws_ex_toolwindow
    user32.SetWindowLongW(hwnd, gwl_exstyle, style)


# ----------------- Small UI components -----------------
class SmallCloseButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__("✕", parent)
        self.setFixedSize(18, 18)
        self.setFont(QFont("Arial", 9))
        self.setStyleSheet("""
            QPushButton {
                border: none;
                background: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.95);
                border-radius: 3px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,1.0);
                color: rgba(10,10,12,1.0);
            }
        """)


class TinyToggleButton(QPushButton):
    def __init__(self, label, tooltip=""):
        super().__init__(label)
        self.setFixedSize(22, 18)
        self.setFont(QFont("Consolas", 9))
        self.setToolTip(tooltip)
        self.setCheckable(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setStyleSheet("""
            QPushButton {
                color: rgba(255,255,255,0.92);
                background: rgba(255,255,255,0.02);
                border: 0px;
                padding: 1px;
                border-radius: 3px;
            }
            QPushButton:checked {
                background: rgba(255,255,255,0.12);
            }
        """)


class DragHandle(QLabel):
    """Small drag handle (three lines) used to drag the overlay."""
    def __init__(self):
        super().__init__("≡")
        self.setFixedSize(22, 18)
        self.setFont(QFont("Consolas", 12))
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("""
            QLabel {
                color: rgba(255,255,255,0.9);
                background: rgba(255,255,255,0.02);
                border-radius: 4px;
            }
            QLabel:hover {
                background: rgba(255,255,255,0.08);
            }
        """)


class StatRow(QWidget):
    """Label | progress bar | percent"""
    def __init__(self, label_text: str, compact=True, theme="dark"):
        super().__init__()
        self.compact = compact
        self.theme = theme

        # Create widgets first (avoid order bugs)
        self.label = QLabel(label_text)
        fsize = 9 if compact else 11
        self.label.setFont(QFont("Consolas", fsize))

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10 if compact else 12)
        self.bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.value_label = QLabel("0%")
        self.value_label.setFont(QFont("Consolas", fsize))

        layout = QHBoxLayout()
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)
        layout.addWidget(self.label, 0)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.value_label, 0)
        self.setLayout(layout)

        self.set_theme(theme)

    def set_theme(self, theme):
        # For light theme: dark chunk (so visible on light background).
        if theme == "light":
            bg = "rgba(0,0,0,0.06)"
            chunk = "rgba(20,20,24,0.95)"   # dark chunk on light background
            textcol = "rgba(0,0,0,0.9)"
        else:
            bg = "rgba(255,255,255,0.04)"
            chunk = "rgba(255,255,255,0.95)"  # white chunk on dark background
            textcol = "rgba(255,255,255,0.95)"
        self.bar.setStyleSheet(f"""
            QProgressBar {{
                border: 0px solid transparent;
                background: {bg};
                border-radius: 6px;
            }}
            QProgressBar::chunk {{
                background: {chunk};
                border-radius: 6px;
            }}
        """)
        self.label.setStyleSheet(f"color: {textcol};")
        self.value_label.setStyleSheet(f"color: {textcol};")

    def update_value(self, percent):
        if percent is None:
            percent = 0
        try:
            p = max(0, min(100, int(percent)))
        except Exception:
            p = 0
        self.bar.setValue(p)
        self.value_label.setText(f"{p}%")


# ----------------- Settings Dialog -----------------
class SettingsDialog(QDialog):
    def __init__(self, parent_overlay):
        super().__init__(parent_overlay)
        self.parent_overlay = parent_overlay
        # Allow flexible sizing (remove fixed size to allow scaling inside settings_area)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.init_ui()

    def init_ui(self):
        # default dark styling; apply_theme will update later
        self.setStyleSheet("background: rgba(10,10,12,0.92); color: white; border-radius:8px;")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # -- Transparency group (use label above slider to avoid group-title overlap) --
        g_trans = QGroupBox()
        g_trans_layout = QVBoxLayout()
        g_trans_layout.setContentsMargins(6, 6, 6, 6)
        g_trans_layout.setSpacing(6)
        trans_label = QLabel("Transparency")
        trans_label.setFont(QFont("Consolas", 10))
        self.trans_slider = QSlider(Qt.Horizontal)
        self.trans_slider.setRange(30, 100)
        self.trans_slider.setValue(int(self.parent_overlay.windowOpacity() * 100))
        self.trans_slider.valueChanged.connect(self.on_trans_changed)
        g_trans_layout.addWidget(trans_label)
        g_trans_layout.addWidget(self.trans_slider)
        g_trans.setLayout(g_trans_layout)
        g_trans.setStyleSheet("QGroupBox{border:none;}")

        # Size group
        g_size = QGroupBox()
        g_size_layout = QHBoxLayout()
        g_size_layout.setContentsMargins(6, 6, 6, 6)
        g_size_layout.setSpacing(8)
        self.btn_small = QPushButton("Small")
        self.btn_med = QPushButton("Medium")
        self.btn_large = QPushButton("Large")
        for b, sz in ((self.btn_small, "small"), (self.btn_med, "medium"), (self.btn_large, "large")):
            b.clicked.connect(partial(self.on_size_selected, sz))
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setFixedHeight(26)
            g_size_layout.addWidget(b)
        g_size.setLayout(g_size_layout)
        g_size.setStyleSheet("QGroupBox{border:none;}")

        # Theme group
        g_theme = QGroupBox()
        g_theme_layout = QHBoxLayout()
        g_theme_layout.setContentsMargins(6, 6, 6, 6)
        g_theme_layout.setSpacing(8)
        self.rb_dark = QRadioButton("Dark")
        self.rb_light = QRadioButton("Light")
        if self.parent_overlay.theme == "light":
            self.rb_light.setChecked(True)
        else:
            self.rb_dark.setChecked(True)
        self.rb_dark.toggled.connect(self.on_theme_changed)
        g_theme_layout.addWidget(self.rb_dark)
        g_theme_layout.addWidget(self.rb_light)
        g_theme.setLayout(g_theme_layout)
        g_theme.setStyleSheet("QGroupBox{border:none;}")

        layout.addWidget(g_trans)
        layout.addWidget(g_size)
        layout.addWidget(g_theme)
        layout.addStretch(1)
        self.setLayout(layout)

    def apply_theme(self, theme):
        if theme == "light":
            bg = "rgba(255,255,255,0.95)"
            fg = "black"
            btn_bg = "rgba(0,0,0,0.06)"
            btn_hover = "rgba(0,0,0,0.12)"
        else:
            bg = "rgba(10,10,12,0.92)"
            fg = "white"
            btn_bg = "rgba(255,255,255,0.05)"
            btn_hover = "rgba(255,255,255,0.15)"

        self.setStyleSheet(f"background: {bg}; color: {fg}; border-radius:8px;")
        for lbl in self.findChildren(QLabel):
            lbl.setStyleSheet(f"color: {fg};")
        for rb in self.findChildren(QRadioButton):
            rb.setStyleSheet(f"color: {fg};")
        for btn in (self.btn_small, self.btn_med, self.btn_large):
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {btn_bg};
                    border: 1px solid rgba(0,0,0,0.06);
                    color: {fg};
                    border-radius: 4px;
                    padding: 4px 8px;
                }}
                QPushButton:hover {{
                    background: {btn_hover};
                }}
            """)
        # slider style
        if theme == "light":
            slider_style = """
                QSlider::groove:horizontal { height: 6px; background: rgba(0,0,0,0.06); border-radius:3px; }
                QSlider::handle:horizontal { width: 12px; background: rgba(20,20,24,0.95); border-radius:6px; }
            """
        else:
            slider_style = """
                QSlider::groove:horizontal { height: 6px; background: rgba(255,255,255,0.06); border-radius:3px; }
                QSlider::handle:horizontal { width: 12px; background: rgba(255,255,255,0.95); border-radius:6px; }
            """
        self.trans_slider.setStyleSheet(slider_style)

    def apply_size_preset(self, preset):
        if preset == "small":
            font_size = 9
            btn_h = 24
        elif preset == "medium":
            font_size = 10
            btn_h = 28
        else:
            font_size = 11
            btn_h = 30

        for lbl in self.findChildren(QLabel):
            lbl.setFont(QFont("Consolas", font_size))
        for rb in self.findChildren(QRadioButton):
            rb.setFont(QFont("Consolas", font_size))
        for btn in (self.btn_small, self.btn_med, self.btn_large):
            btn.setFont(QFont("Consolas", font_size))
            btn.setFixedHeight(btn_h)

        # ensure the dialog width expands so slider label doesn't overlap
        parent_w = self.parent_overlay.width() if hasattr(self, "parent_overlay") else 360
        self.setMinimumWidth(max(260, int(parent_w * 0.6)))

    def on_trans_changed(self, val):
        self.parent_overlay.setWindowOpacity(val / 100.0)

    def on_size_selected(self, size):
        self.parent_overlay.apply_size_preset(size)

    def on_theme_changed(self):
        new_theme = "light" if self.rb_light.isChecked() else "dark"
        self.parent_overlay.apply_theme(new_theme)


# ----------------- Main Overlay Window -----------------
class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()

        # base window flags
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        # allow transparent background and preserve for painting rounded panel
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # Default user settings
        self.theme = "dark"
        self.size_preset = "small"
        self.transparency = 0.92  # default 0.92 -> panel alpha applied in paintEvent

        # internal
        self.previous_net = None
        self._drag_pos = None
        self._dragging = False

        # Build UI (create all widgets before calling presets)
        self.build_ui()

        # Detect GPUs (uses pynvml first then GPUtil)
        self.detect_gpus_and_populate()

        # Populate Info page content
        self.populate_info_text()

        # Timers: clock and stats
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(500)

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_stats)
        self.update_timer.start(1000)

        # Apply initial theme, size, transparency AFTER widgets created
        self.apply_theme(self.theme)
        self.apply_size_preset(self.size_preset)
        self.setWindowOpacity(self.transparency)

        # place at a sane default
        self.move(20, 20)
        # ensure layered style on Windows so transparency works well
        if is_windows:
            enable_layered(int(self.winId()))

        # show
        self.show()

    def build_ui(self):
        # Top bar: drag-handle, time, home (hidden initially), Info tab, About tab, Settings, spacer, close
        self.drag_handle = DragHandle()
        self.time_label = QLabel("--:--:--")
        self.time_label.setFont(QFont("Consolas", 10))
        self.time_label.setFixedHeight(18)
        self.time_label.setStyleSheet("color: white;")

        # Battery label (top-left, next to time)
        self.batt_label = QLabel("")                   # created empty, updated by update_battery()
        self.batt_label.setFont(QFont("Consolas", 9))
        self.batt_label.setFixedHeight(18)
        self.batt_label.setStyleSheet("color: white;")


        self.home_btn = QPushButton("⌂")
        self.home_btn.setFixedSize(22, 20)
        self.home_btn.setVisible(False)
        self.home_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.home_btn.setToolTip("Home (return to live)")
        self.home_btn.clicked.connect(self.show_live_from_tab)
        self.home_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(255,255,255,0.06);
                color: white;
                border-radius:6px;
                padding: 2px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.06); }
        """)

        self.tab_info = TinyToggleButton("i", "Info")
        self.tab_about = TinyToggleButton("?", "About")
        self.btn_settings = TinyToggleButton("⚙", "Settings")
        # connect tabs
        self.tab_info.clicked.connect(partial(self.toggle_tab, "info"))
        self.tab_about.clicked.connect(partial(self.toggle_tab, "about"))
        self.btn_settings.clicked.connect(self.open_settings)

        spacer = QFrame()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.close_button = SmallCloseButton()
        self.close_button.clicked.connect(QApplication.instance().quit)

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(8, 6, 6, 0)
        top_layout.setSpacing(6)
        top_layout.addWidget(self.drag_handle)
        top_layout.addWidget(self.time_label)
        top_layout.addWidget(self.batt_label)
        top_layout.addWidget(self.home_btn)
        top_layout.addWidget(self.tab_info)
        top_layout.addWidget(self.tab_about)
        top_layout.addWidget(self.btn_settings)
        top_layout.addWidget(spacer)
        top_layout.addWidget(self.close_button)

        # Live area
        self.live_area = QWidget()
        live_layout = QVBoxLayout()
        live_layout.setContentsMargins(8, 8, 8, 8)
        live_layout.setSpacing(6)

        # stat rows (created now)
        self.row_cpu = StatRow("CPU", compact=True, theme=self.theme)
        self.row_ram = StatRow("RAM", compact=True, theme=self.theme)
        self.row_disk = StatRow("DISK", compact=True, theme=self.theme)
        self.row_net = StatRow("NETWORK", compact=True, theme=self.theme)

        live_layout.addWidget(self.row_cpu)
        live_layout.addWidget(self.row_ram)
        live_layout.addWidget(self.row_disk)

        # GPU container layout (we will add rows dynamically after detection)
        self.gpus_container = QVBoxLayout()
        self.gpus_container.setSpacing(4)
        live_layout.addLayout(self.gpus_container)

        live_layout.addWidget(self.row_net)
        self.live_area.setLayout(live_layout)

        # Info area (text browser inside scroll area with minimal scrollbar)
        self.info_area = QWidget()
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(8, 8, 8, 8)
        info_layout.setSpacing(6)

        self.info_text = QTextBrowser()
        self.info_text.setOpenExternalLinks(True)
        self.info_text.setFont(QFont("Consolas", 10))
        # Minimal scrollbar styling
        self.info_text.setStyleSheet("""
            QTextBrowser { background: transparent; color: white; border: none; }
            QScrollBar:vertical { width: 8px; background: transparent; margin: 0px 0px 0px 0px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.12); min-height: 20px; border-radius:4px; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }
            a { color: #91d1ff; }
        """)
        self.info_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        info_layout.addWidget(self.info_text)
        self.info_area.setLayout(info_layout)
        self.info_area.setVisible(False)  # hidden by default

        # About area
        self.about_area = QWidget()
        about_layout = QVBoxLayout()
        about_layout.setContentsMargins(8, 8, 8, 8)
        about_layout.setSpacing(6)
        self.about_text = QTextBrowser()
        self.about_text.setOpenExternalLinks(True)
        self.about_text.setFont(QFont("Consolas", 10))
        self.about_text.setStyleSheet("""
            QTextBrowser { background: transparent; color: white; border: none; }
            QScrollBar:vertical { width: 8px; background: transparent; margin: 0px 0px 0px 0px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.12); min-height: 20px; border-radius:4px; }
            a { color: #91d1ff; }
        """)
        about_html = """
        <h3>About</h3>
        <p>Windows Laptop/PC Stats Monitor (Overlay) app — live system metrics.</p>
        <p><a href='https://captainvoldemort.github.io/vibe-stats-overlay/'>Visit our website</a></p>
        """
        self.about_text.setHtml(about_html)
        about_layout.addWidget(self.about_text)
        self.about_area.setLayout(about_layout)
        self.about_area.setVisible(False)

        # Compose master layout
        master_layout = QVBoxLayout()
        master_layout.setContentsMargins(6, 6, 6, 6)
        master_layout.setSpacing(6)
        master_layout.addLayout(top_layout)
        master_layout.addWidget(self.live_area)
        master_layout.addWidget(self.info_area)
        master_layout.addWidget(self.about_area)
        self.setLayout(master_layout)

        # set fixed height initially (adjustable by size preset)
        self.setFixedHeight(260)

    # ---------- GPU detection and populate rows ----------
    def detect_gpus_and_populate(self):
        gpu_names = []
        gpu_count = 0
        if NVML:
            try:
                gpu_count = pynvml.nvmlDeviceGetCount()
                for i in range(gpu_count):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(h)
                    if isinstance(name, bytes):
                        name = name.decode(errors="ignore")
                    gpu_names.append(name)
            except Exception:
                gpu_names = []
                gpu_count = 0

        if not gpu_names and GPUtil_available:
            try:
                gpus = GPUtil.getGPUs()
                for g in gpus:
                    gpu_names.append(g.name)
                gpu_count = len(gpus)
            except Exception:
                gpu_names = []
                gpu_count = 0

        # clear container if any old widgets
        for i in reversed(range(self.gpus_container.count())):
            item = self.gpus_container.takeAt(i)
            w = item.widget()
            if w:
                w.deleteLater()
        self.gpu_rows = []

        if gpu_count == 0:
            # placeholder single GPU row
            r = StatRow("GPU", compact=True, theme=self.theme)
            self.gpus_container.addWidget(r)
            self.gpu_rows.append(("GPU", r))
        else:
            for idx, name in enumerate(gpu_names):
                label = f"GPU{idx} ({name})"
                r = StatRow(label, compact=True, theme=self.theme)
                self.gpus_container.addWidget(r)
                self.gpu_rows.append((name, r))

    # ---------- Info text ----------
    def populate_info_text(self):
        uname = platform.uname()
        total_ram_gb = psutil.virtual_memory().total / (1024**3)
        cpu_count_physical = psutil.cpu_count(logical=False)
        cpu_count_logical = psutil.cpu_count(logical=True)
        py_ver = platform.python_version()

        # NICs
        nic_lines = []
        try:
            nics = psutil.net_if_addrs()
            for nic, addrs in nics.items():
                addr_strs = []
                for a in addrs:
                    # show ipv4/ipv6/mac
                    addr_strs.append(a.address)
                nic_lines.append(f"<b>{nic}</b>: {', '.join([x for x in addr_strs if x])}")
        except Exception:
            nic_lines.append("No network info")

        # GPUs
        gpu_lines = []
        if NVML:
            try:
                c = pynvml.nvmlDeviceGetCount()
                for i in range(c):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(h)
                    if isinstance(name, bytes):
                        name = name.decode(errors="ignore")
                    mem = pynvml.nvmlDeviceGetMemoryInfo(h).total / (1024**3)
                    gpu_lines.append(f"{name} — {mem:.1f} GB")
            except Exception:
                pass
        elif GPUtil_available:
            try:
                gpus = GPUtil.getGPUs()
                for g in gpus:
                    gpu_lines.append(f"{g.name} — {g.memoryTotal} MB")
            except Exception:
                pass
        if not gpu_lines:
            gpu_lines = ["No dedicated GPUs detected"]

        info_html = f"""
        <h3>System Information</h3>
        <p><b>System:</b> {uname.system} {uname.release} ({uname.version})</p>
        <p><b>Machine:</b> {uname.machine} — {uname.node}</p>
        <p><b>CPU cores:</b> physical {cpu_count_physical}, logical {cpu_count_logical}</p>
        <p><b>Total RAM:</b> {total_ram_gb:.1f} GB</p>
        <p><b>Network Interfaces:</b><br>{'<br>'.join(nic_lines[:6])}</p>
        <p><b>GPUs:</b><br>{'<br>'.join(gpu_lines)}</p>
        <p><b>Python:</b> {py_ver}</p>
        """
        self.info_text.setHtml(info_html)

    # ---------- Tab toggling ----------
    def toggle_tab(self, which):
        if which == "info":
            if self.tab_info.isChecked():
                self.tab_about.setChecked(False)
                self.home_btn.setVisible(True)
                self.info_area.setVisible(True)
                self.about_area.setVisible(False)
                self.live_area.setVisible(False)
                if hasattr(self, "settings_area"):
                    self.settings_area.setVisible(False)
            else:
                self.info_area.setVisible(False)
                self.live_area.setVisible(True)
                self.home_btn.setVisible(False)
        elif which == "about":
            if self.tab_about.isChecked():
                self.tab_info.setChecked(False)
                self.home_btn.setVisible(True)
                self.about_area.setVisible(True)
                self.info_area.setVisible(False)
                self.live_area.setVisible(False)
                if hasattr(self, "settings_area"):
                    self.settings_area.setVisible(False)
            else:
                self.about_area.setVisible(False)
                self.live_area.setVisible(True)
                self.home_btn.setVisible(False)

    def show_live_from_tab(self):
        self.tab_info.setChecked(False)
        self.tab_about.setChecked(False)
        self.home_btn.setVisible(False)
        self.info_area.setVisible(False)
        self.about_area.setVisible(False)
        self.live_area.setVisible(True)
        if hasattr(self, "settings_area"):
            self.settings_area.setVisible(False)

    def open_settings(self):
        # treat settings like its own pane instead of floating dialog
        self.tab_info.setChecked(False)
        self.tab_about.setChecked(False)
        self.home_btn.setVisible(True)
        self.live_area.setVisible(False)
        self.info_area.setVisible(False)
        self.about_area.setVisible(False)

        # create settings area lazily if not made yet
        if not hasattr(self, "settings_area"):
            self.settings_area = QWidget()
            s_layout = QVBoxLayout()
            s_layout.setContentsMargins(8, 8, 8, 8)
            s_layout.setSpacing(6)
            self.settings_widget = SettingsDialog(self)
            # make it look embedded (widget) not floating
            self.settings_widget.setWindowFlags(Qt.Widget)
            self.settings_widget.setAttribute(Qt.WA_TranslucentBackground, False)
            s_layout.addWidget(self.settings_widget)
            self.settings_area.setLayout(s_layout)
            self.layout().addWidget(self.settings_area)
        self.settings_area.setVisible(True)

        # hide other panes if they exist
        if hasattr(self, "info_area"):
            self.info_area.setVisible(False)
        if hasattr(self, "about_area"):
            self.about_area.setVisible(False)

    # ---------- Theme / Size / Transparency ----------
    def apply_theme(self, theme):
        self.theme = theme
        # update rows theme
        for row in (self.row_cpu, self.row_ram, self.row_disk, self.row_net):
            row.set_theme(theme)
        for name, row in self.gpu_rows:
            row.set_theme(theme)
        # Info/About text
        if theme == "light":
            self.info_text.setStyleSheet("""
                QTextBrowser { background: transparent; color: black; border: none; }
                QScrollBar:vertical { width: 8px; background: transparent; margin: 0px 0px 0px 0px; }
                QScrollBar::handle:vertical { background: rgba(0,0,0,0.12); min-height: 20px; border-radius:4px; }
                a { color: #1a73e8; }
            """)
            self.about_text.setStyleSheet(self.info_text.styleSheet())
            self.time_label.setStyleSheet("color: black;")
            self.batt_label.setStyleSheet("color: black;")
            # close button style for light
            self.close_button.setStyleSheet("""
                QPushButton { border: none; background: rgba(0,0,0,0.04); color: rgba(0,0,0,0.95); border-radius:3px; }
                QPushButton:hover { background: rgba(0,0,0,1.0); color: white; }
            """)
        else:
            self.info_text.setStyleSheet("""
                QTextBrowser { background: transparent; color: white; border: none; }
                QScrollBar:vertical { width: 8px; background: transparent; margin: 0px 0px 0px 0px; }
                QScrollBar::handle:vertical { background: rgba(255,255,255,0.12); min-height: 20px; border-radius:4px; }
                a { color: #91d1ff; }
            """)
            self.about_text.setStyleSheet(self.info_text.styleSheet())
            self.time_label.setStyleSheet("color: white;")
            self.batt_label.setStyleSheet("color: white;")
            self.close_button.setStyleSheet("""
                QPushButton { border: none; background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.95); border-radius:3px; }
                QPushButton:hover { background: rgba(255,255,255,1.0); color: rgba(10,10,12,1.0); }
            """)

        # ensure top bar icons visible in light theme
        if theme == "light":
            icon_color = "black"
            drag_color = "black"
            home_bg = "rgba(0,0,0,0.04)"
            home_hover = "rgba(0,0,0,0.08)"
        else:
            icon_color = "white"
            drag_color = "rgba(255,255,255,0.9)"
            home_bg = "transparent"
            home_hover = "rgba(255,255,255,0.06)"

        for btn in [self.tab_info, self.tab_about, self.btn_settings]:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.02);
                    border: none;
                    color: {icon_color};
                    border-radius: 3px;
                }}
                QPushButton:hover {{
                    background: rgba(255,255,255,0.12);
                }}
            """)
        # style home button for theme
        self.home_btn.setStyleSheet(f"""
            QPushButton {{
                background: {home_bg};
                border: 1px solid rgba(255,255,255,0.06);
                color: {icon_color};
                border-radius:6px;
                padding: 2px;
            }}
            QPushButton:hover {{ background: {home_hover}; }}
        """)
        # drag handle color
        self.drag_handle.setStyleSheet(f"""
            QLabel {{
                color: {drag_color};
                background: rgba(255,255,255,0.02);
                border-radius: 4px;
            }}
            QLabel:hover {{
                background: rgba(255,255,255,0.08);
            }}
        """)

        # Update settings widget too (theme)
        if hasattr(self, "settings_widget"):
            self.settings_widget.apply_theme(theme)

        # repaint to update panel color if needed
        self.update()

    def apply_size_preset(self, preset):
        self.size_preset = preset
        # small/medium/large adjusts widths, font sizes, row heights
        if preset == "small":
            width = 380
            fsize = 9
            bar_h = 10
            height = 240
            top_font = 10
        elif preset == "medium":
            width = 460
            fsize = 10
            bar_h = 12
            height = 300
            top_font = 11
        else:
            width = 540
            fsize = 11
            bar_h = 14
            height = 350
            top_font = 12

        self.setFixedWidth(width)
        self.setFixedHeight(height)

        # --- scale everything proportionally ---
        self.time_label.setFont(QFont("Consolas", top_font))
        self.batt_label.setFont(QFont("Consolas", max(8, top_font-1)))
        self.drag_handle.setFont(QFont("Consolas", top_font + 1))
        self.home_btn.setFixedSize(top_font + 12, top_font + 8)
        self.close_button.setFixedSize(top_font + 8, top_font + 6)
        self.close_button.setFont(QFont("Arial", max(8, top_font - 2)))

        # tiny toggle buttons (tabs/settings)
        for btn in [self.tab_info, self.tab_about, self.btn_settings]:
            btn.setFont(QFont("Consolas", top_font - 1))
            btn.setFixedSize(top_font + 12, top_font + 6)

        # stat rows (main system metrics)
        for row in (self.row_cpu, self.row_ram, self.row_disk, self.row_net):
            row.label.setFont(QFont("Consolas", fsize))
            row.value_label.setFont(QFont("Consolas", fsize))
            row.bar.setFixedHeight(bar_h)
        for name, row in self.gpu_rows:
            row.label.setFont(QFont("Consolas", fsize))
            row.value_label.setFont(QFont("Consolas", fsize))
            row.bar.setFixedHeight(bar_h)

        # info/about text scaling
        self.info_text.setFont(QFont("Consolas", fsize + 1))
        self.about_text.setFont(QFont("Consolas", fsize + 1))

        # settings widget size/scale
        if hasattr(self, "settings_widget"):
            self.settings_widget.apply_size_preset(preset)
    
    # ---------- Clock & Stats ----------
    def update_clock(self):
        self.time_label.setText(time.strftime("%H:%M:%S"))

    def update_battery(self):
        """Updates the battery percentage and charging status label."""
        try:
            batt = psutil.sensors_battery()
            if batt is None:
                self.batt_label.setText("No battery")
                return

            percent = int(batt.percent)
            plugged = batt.power_plugged

            if plugged:
                plug_symbol = "🔌"
                status = "Charging"
            else:
                plug_symbol = "🔋"
                status = "On Battery"

            self.batt_label.setText(f"{plug_symbol} {percent}% ({status})")
        except Exception:
            self.batt_label.setText("Battery: --")
    
    def update_stats(self):
        # Update battery info
        self.update_battery()
        # CPU
        try:
            cpu = psutil.cpu_percent(interval=None)
        except Exception:
            cpu = 0.0
        self.row_cpu.update_value(cpu)

        # RAM
        try:
            ram = psutil.virtual_memory().percent
        except Exception:
            ram = 0.0
        self.row_ram.update_value(ram)

        # Disk (root)
        try:
            disk = psutil.disk_usage("/").percent
        except Exception:
            disk = 0.0
        self.row_disk.update_value(disk)

        # GPUs
        if NVML:
            try:
                count = pynvml.nvmlDeviceGetCount()
                for idx in range(max(len(self.gpu_rows), count)):
                    if idx < count and idx < len(self.gpu_rows):
                        handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                        util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
                        self.gpu_rows[idx][1].update_value(util)
                    elif idx < len(self.gpu_rows):
                        self.gpu_rows[idx][1].update_value(0)
            except Exception:
                self._update_gpus_gputil()
        else:
            self._update_gpus_gputil()

        # Network: choose NIC with most traffic, compute delta speed mapped to 0..100
        try:
            pernic = psutil.net_io_counters(pernic=True)
            top_nic = None
            top_total = 0
            for nic, s in pernic.items():
                total = getattr(s, "bytes_sent", 0) + getattr(s, "bytes_recv", 0)
                if total > top_total:
                    top_total = total
                    top_nic = (nic, s)
            if top_nic:
                nic_name = top_nic[0]
                curr = top_nic[1]
                now = time.time()
                if self.previous_net and self.previous_net.get("name") == nic_name:
                    dt = now - self.previous_net["time"]
                    if dt <= 0:
                        dt = 1.0
                    sent_delta = (curr.bytes_sent - self.previous_net["sent"]) / dt
                    recv_delta = (curr.bytes_recv - self.previous_net["recv"]) / dt
                    kbps = (sent_delta + recv_delta) / 1024.0
                    pct = min(100, int((kbps / 102400) * 100))
                    self.row_net.label.setText(f"NETWORK ({nic_name})")
                    self.row_net.update_value(pct)
                else:
                    self.row_net.update_value(0)
                self.previous_net = {
                    "name": nic_name,
                    "sent": curr.bytes_sent,
                    "recv": curr.bytes_recv,
                    "time": now
                }
            else:
                self.row_net.update_value(0)
        except Exception:
            self.row_net.update_value(0)

    def _update_gpus_gputil(self):
        if GPUtil_available:
            try:
                gpus = GPUtil.getGPUs()
                for idx, (name, row) in enumerate(self.gpu_rows):
                    if idx < len(gpus):
                        load = int(gpus[idx].load * 100)
                        row.update_value(load)
                    else:
                        row.update_value(0)
            except Exception:
                for name, row in self.gpu_rows:
                    row.update_value(0)
        else:
            for name, row in self.gpu_rows:
                row.update_value(0)

    # ---------- Painting background panel ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        # dark panel default
        if self.theme == "light":
            brush = QBrush(QColor(255, 255, 255, int(255 * 0.95)))  # near-opaque light panel
        else:
            # semi-transparent dark panel; actual opacity controlled by windowOpacity() too
            brush = QBrush(QColor(8, 8, 10, 220))
        painter.setBrush(brush)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect.adjusted(0, 0, 0, 0), 10, 10)

    # ---------- Dragging: only when drag_handle pressed ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # check if press is inside drag_handle
            handle_geom = self.drag_handle.geometry()
            if handle_geom.contains(event.pos()):
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                self._dragging = True
                self.drag_handle.setCursor(QCursor(Qt.ClosedHandCursor))
            else:
                self._drag_pos = None
                self._dragging = False

    def mouseMoveEvent(self, event):
        if self._drag_pos and (event.buttons() & Qt.LeftButton) and self._dragging:
            self.move(event.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        if self._dragging:
            self.drag_handle.setCursor(QCursor(Qt.OpenHandCursor))
        self._dragging = False


# ----------------- main -----------------
def main():
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps)
    overlay = OverlayWindow()
    overlay.setWindowTitle("Overlay")
    overlay.move(20, 20)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
