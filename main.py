# -*- coding: utf-8 -*-
import sys, ctypes
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv
from datetime import datetime, timezone
import os, threading, time as _time
from queue import SimpleQueue

from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import (
    QColor, QPainter, QLinearGradient, QBrush, QPainterPath, QFont,
    QPixmap, QIcon,
)

load_dotenv()
ORG_ID      = os.getenv("CLAUDE_ORG_ID")
SESSION_KEY = os.getenv("CLAUDE_SESSION_KEY")
DEVICE_ID   = os.getenv("CLAUDE_DEVICE_ID")
ANON_ID     = os.getenv("CLAUDE_ANON_ID")
INTERVAL    = 20

TEXT   = "#ffffff"
DIM    = "#9ea6c8"
ACCENT = "#bd93f9"
GREEN  = "#50fa7b"
YELLOW = "#f1fa8c"
RED    = "#ff5555"

CARD_BG    = "#1Affffff"
CARD_EDGE  = "#30ffffff"
PANEL_EDGE = "#38ffffff"
SEP_COL    = "#20ffffff"
TITLE_TINT = "#18000000"

SPIN_FRAMES = ["◐", "◓", "◑", "◒"]

def _qss_rgba(hex_col: str, alpha_frac: float) -> str:
    return f"#{int(alpha_frac * 255):02x}{hex_col[1:]}"

SECTIONS = [
    ("five_hour",        "⏱   Current Session",    "5hr window"),
    ("seven_day",        "⬡   Weekly · All Models", "7 day"),
    ("seven_day_sonnet", "✦   Weekly · Sonnet",     "7 day"),
    ("extra_usage",      "◈   Extra Credits",       "monthly"),
]

# ── Windows helpers ──────────────────────────────────────────────────────
GWL_EXSTYLE       = -20
WS_EX_TRANSPARENT = 0x00000020

class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState",   ctypes.c_uint),
        ("AccentFlags",   ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId",   ctypes.c_uint),
    ]

class _WINCOMPATTR(ctypes.Structure):
    _fields_ = [
        ("Attribute",  ctypes.c_int),
        ("Data",       ctypes.POINTER(ctypes.c_int)),
        ("SizeOfData", ctypes.c_size_t),
    ]

def apply_acrylic(hwnd: int, tint_abgr: int = 0xCC0a0814) -> bool:
    try:
        policy = _ACCENT_POLICY()
        policy.AccentState   = 4
        policy.AccentFlags   = 2
        policy.GradientColor = tint_abgr
        attr = _WINCOMPATTR()
        attr.Attribute  = 19
        attr.Data       = ctypes.cast(ctypes.pointer(policy), ctypes.POINTER(ctypes.c_int))
        attr.SizeOfData = ctypes.sizeof(policy)
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.pointer(attr))
        return True
    except Exception:
        return False

def set_click_through(hwnd: int, enable: bool):
    try:
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enable:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass

# ── API ──────────────────────────────────────────────────────────────────
def get_usage():
    url = f"https://claude.ai/api/organizations/{ORG_ID}/usage"
    h = {
        "accept": "*/*", "accept-language": "en-US,en;q=0.9",
        "anthropic-anonymous-id": ANON_ID,
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-version": "1.0.0",
        "anthropic-device-id": DEVICE_ID,
        "content-type": "application/json",
        "referer": "https://claude.ai/settings/usage",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    }
    c = {"sessionKey": SESSION_KEY, "anthropic-device-id": DEVICE_ID, "lastActiveOrg": ORG_ID}
    r = cffi_requests.get(url, headers=h, cookies=c, impersonate="chrome110", timeout=15)
    r.raise_for_status()
    return r.json()

def fmt_reset(iso):
    if not iso:
        return None
    try:
        dt    = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        total = int((dt - datetime.now(timezone.utc)).total_seconds())
        if total <= 0:
            return "resetting soon"
        hh, rem = divmod(total, 3600)
        return f"in {hh//24}d {hh%24}h" if hh >= 24 else f"in {hh}h {rem//60}m"
    except Exception:
        return iso

def pct_color(pct):
    return GREEN if pct < 50 else YELLOW if pct < 80 else RED

# ── GlassBar ─────────────────────────────────────────────────────────────
class GlassBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pct   = 0.0
        self._color = QColor(GREEN)
        self.setFixedHeight(13)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_value(self, pct: float, hex_color: str):
        self._pct   = max(0.0, min(100.0, pct))
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, _):
        w, h = self.width(), self.height()
        if w < 4:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = h / 2.0

        # track
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.fillPath(track, QColor(255, 255, 255, 38))

        fw = w * self._pct / 100.0
        if fw >= 2:
            rx = min(r, fw / 2.0)

            # glow layers (drawn under the fill)
            glow_c = QColor(self._color)
            for expand, alpha in [(8, 14), (5, 22), (3, 35)]:
                glow_c.setAlpha(alpha)
                gp = QPainterPath()
                gp.addRoundedRect(
                    QRectF(-expand / 2, -expand / 2, fw + expand, h + expand),
                    r + expand / 2, r + expand / 2,
                )
                p.fillPath(gp, glow_c)

            # fill gradient
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(0, 0, fw, h), rx, rx)
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, self._color.lighter(150))
            grad.setColorAt(0.4, self._color.lighter(110))
            grad.setColorAt(1.0, self._color.darker(125))
            p.fillPath(fill, QBrush(grad))

            # glass highlight strip
            if fw > rx * 4:
                hi = QPainterPath()
                hi.addRoundedRect(QRectF(1, 1, fw - 2, h * 0.4), rx * 0.7, rx * 0.7)
                p.fillPath(hi, QColor(255, 255, 255, 35))
        p.end()

# ── SectionCard ──────────────────────────────────────────────────────────
class SectionCard(QFrame):
    def __init__(self, label: str, hint: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(f"""
            QFrame#SCard {{
                background: {CARD_BG};
                border-radius: 10px;
                border: 1px solid {CARD_EDGE};
            }}
        """)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._stripe = QFrame()
        self._stripe.setFixedWidth(3)
        self._stripe.setStyleSheet(f"background:{DIM}; border:none; border-radius:1px;")
        outer.addWidget(self._stripe)

        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        body.setStyleSheet("background:transparent; border:none;")
        vl = QVBoxLayout(body)
        vl.setContentsMargins(12, 10, 14, 10)
        vl.setSpacing(5)

        hl = QHBoxLayout()
        hl.setSpacing(5)
        hl.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(label)
        lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color:{TEXT}; background:transparent;")

        hint_lbl = QLabel(hint)
        hint_lbl.setFont(QFont("Segoe UI", 9))
        hint_lbl.setStyleSheet(f"color:{DIM}; background:transparent;")

        self._pct_lbl = QLabel("—")
        self._pct_lbl.setFont(QFont("Consolas", 15, QFont.Weight.Bold))
        self._pct_lbl.setStyleSheet(f"color:{DIM}; background:transparent;")
        self._pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pct_lbl.setMinimumWidth(58)

        hl.addWidget(lbl)
        hl.addWidget(hint_lbl)
        hl.addStretch()
        hl.addWidget(self._pct_lbl)

        self._bar = GlassBar()

        self._detail = QLabel("")
        self._detail.setFont(QFont("Segoe UI", 9))
        self._detail.setStyleSheet(f"color:{DIM}; background:transparent;")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignRight)

        vl.addLayout(hl)
        vl.addWidget(self._bar)
        vl.addWidget(self._detail)
        outer.addWidget(body)

    def refresh(self, pct: float, detail: str, color: str):
        self._pct_lbl.setText(f"{pct:.0f}%")
        self._pct_lbl.setStyleSheet(f"color:{color}; background:transparent;")
        self._bar.set_value(pct, color)
        self._detail.setText(detail)
        self._stripe.setStyleSheet(f"background:{color}; border:none; border-radius:1px;")

# ── TitleBar ─────────────────────────────────────────────────────────────
class TitleBar(QWidget):
    def __init__(self, on_collapse, on_ghost, on_close, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self.setStyleSheet(f"background:{TITLE_TINT}; border:none;")

        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 8, 0)
        hl.setSpacing(0)

        dot = QLabel("◈")
        dot.setFont(QFont("Segoe UI", 12))
        dot.setStyleSheet(f"color:{ACCENT}; background:transparent;")
        dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        title = QLabel("  Claude Usage")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{TEXT}; background:transparent;")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        def _mk_btn(text, tooltip, callback, danger=False):
            b = QPushButton(text)
            b.setFont(QFont("Segoe UI", 9))
            b.setFixedSize(26, 26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tooltip)
            hover_col = RED if danger else ACCENT
            b.setStyleSheet(f"""
                QPushButton         {{ background:transparent; border:none;
                                       color:{DIM}; border-radius:5px; }}
                QPushButton:hover   {{ background:{_qss_rgba(hover_col, 0.18)}; color:{hover_col}; }}
                QPushButton:pressed {{ background:{_qss_rgba(hover_col, 0.32)}; }}
            """)
            b.clicked.connect(callback)
            return b

        self._ghost_btn    = _mk_btn("◎", "Toggle click-through", on_ghost)
        self._collapse_btn = _mk_btn("▾", "Collapse / expand",    on_collapse)
        close_btn          = _mk_btn("✕", "Hide to tray",         on_close, danger=True)

        hl.addWidget(dot)
        hl.addWidget(title)
        hl.addStretch()
        hl.addWidget(self._ghost_btn)
        hl.addSpacing(2)
        hl.addWidget(self._collapse_btn)
        hl.addSpacing(2)
        hl.addWidget(close_btn)

    def set_collapsed(self, collapsed: bool):
        self._collapse_btn.setText("▴" if collapsed else "▾")

    def set_ghost(self, ghost: bool):
        self._ghost_btn.setText("◉" if ghost else "◎")
        col = ACCENT if ghost else DIM
        self._ghost_btn.setStyleSheet(f"""
            QPushButton         {{ background:transparent; border:none;
                                   color:{col}; border-radius:5px; }}
            QPushButton:hover   {{ background:{_qss_rgba(ACCENT, 0.18)}; color:{ACCENT}; }}
            QPushButton:pressed {{ background:{_qss_rgba(ACCENT, 0.32)}; }}
        """)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()

# ── MainWindow ────────────────────────────────────────────────────────────
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._hwnd       = None
        self._collapsed  = False
        self._ghost      = False
        self._fetching   = False
        self._spin_frame = 0
        self._cards      = {}
        self._next_at    = [_time.time() + INTERVAL]
        self._last_ts    = [""]
        self._queue: SimpleQueue = SimpleQueue()

        self._build()
        self.adjustSize()
        scr = QApplication.primaryScreen().geometry()
        self.move(scr.width() - self.width() - 30, 60)

        self._setup_tray()

        tmr = QTimer(self)
        tmr.timeout.connect(self._tick)
        tmr.start(500)

        threading.Thread(target=self._fetch,        daemon=True).start()
        threading.Thread(target=self._refresh_loop, daemon=True).start()

    def _build(self):
        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(390)
        panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        panel.setStyleSheet(f"""
            QFrame#panel {{
                background: transparent;
                border-radius: 14px;
                border: 1px solid {PANEL_EDGE};
            }}
        """)
        wrap.addWidget(panel)

        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        self._titlebar = TitleBar(
            on_collapse=self._toggle_collapse,
            on_ghost=self._toggle_ghost,
            on_close=self._hide_to_tray,
            parent=self,
        )
        pl.addWidget(self._titlebar)

        self._div = QFrame()
        self._div.setFixedHeight(1)
        self._div.setStyleSheet(f"background:{SEP_COL}; border:none;")
        pl.addWidget(self._div)

        self._body = QWidget()
        self._body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._body.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(12, 12, 12, 12)
        bl.setSpacing(8)

        for key, label, hint in SECTIONS:
            card = SectionCard(label, hint)
            bl.addWidget(card)
            self._cards[key] = card

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{SEP_COL}; border:none;")
        bl.addWidget(sep)

        self._footer = QLabel("Starting up…")
        self._footer.setFont(QFont("Segoe UI", 9))
        self._footer.setStyleSheet(f"color:{DIM}; background:transparent; padding:2px 0;")
        bl.addWidget(self._footer)

        pl.addWidget(self._body)

    def _setup_tray(self):
        px = QPixmap(16, 16)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(ACCENT))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 12, 12)
        p.end()

        self._tray = QSystemTrayIcon(QIcon(px), self)
        self._tray.setToolTip("Claude Usage")

        menu = QMenu()
        menu.addAction("Show / Hide").triggered.connect(self._toggle_visibility)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(QApplication.quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self._toggle_visibility()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self._tray.show()

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()

    def _hide_to_tray(self):
        self.hide()

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        self._body.setVisible(not self._collapsed)
        self._div.setVisible(not self._collapsed)
        self._titlebar.set_collapsed(self._collapsed)
        self.adjustSize()

    def _toggle_ghost(self):
        self._ghost = not self._ghost
        self._titlebar.set_ghost(self._ghost)
        if self._ghost:
            self.setWindowOpacity(0.35)
            if self._hwnd:
                set_click_through(self._hwnd, True)
        else:
            self.setWindowOpacity(1.0)
            if self._hwnd:
                set_click_through(self._hwnd, False)

    def enterEvent(self, e):
        if self._ghost and self._hwnd:
            set_click_through(self._hwnd, False)
            self.setWindowOpacity(0.92)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._ghost and self._hwnd:
            set_click_through(self._hwnd, True)
            self.setWindowOpacity(0.35)
        super().leaveEvent(e)

    def _fetch(self):
        self._fetching = True
        try:
            self._queue.put(("ok", get_usage()))
        except Exception as e:
            msg = (f"✗ HTTP {e.response.status_code}"
                   if hasattr(e, "response") and e.response else f"✗ {e}")
            self._queue.put(("err", msg))
        finally:
            self._fetching = False

    def _refresh_loop(self):
        while True:
            _time.sleep(INTERVAL)
            self._next_at[0] = _time.time() + INTERVAL
            threading.Thread(target=self._fetch, daemon=True).start()

    def _apply(self, data: dict):
        for key, *_ in SECTIONS:
            val  = data.get(key)
            card = self._cards[key]
            if not val:
                card.refresh(0, "No data", DIM)
                continue
            pct   = float(val.get("utilization", 0) or 0)
            color = pct_color(pct)
            if key == "extra_usage":
                if not val.get("is_enabled"):
                    card.refresh(0, "Disabled", DIM)
                    continue
                used  = (val.get("used_credits",  0) or 0) / 100
                limit = (val.get("monthly_limit", 0) or 0) / 100
                card.refresh(pct, f"${used:.2f} used  ·  ${limit:.2f} limit", color)
            else:
                reset = fmt_reset(val.get("resets_at"))
                card.refresh(pct, f"Resets {reset}" if reset else "No active session", color)

    def _tick(self):
        while not self._queue.empty():
            kind, payload = self._queue.get()
            if kind == "ok":
                self._last_ts[0] = datetime.now().strftime("%b %d  %H:%M:%S")
                self._apply(payload)
            else:
                self._last_ts[0] = str(payload)

        secs = max(0, int(self._next_at[0] - _time.time()))
        if self._fetching:
            self._spin_frame = (self._spin_frame + 1) % len(SPIN_FRAMES)
            self._footer.setText(
                f"{self._last_ts[0] or ''}  {SPIN_FRAMES[self._spin_frame]} Fetching…"
            )
        else:
            self._footer.setText(f"{self._last_ts[0] or 'Fetching…'}  ·  ↻ {secs}s")

# ── entry ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not all([ORG_ID, SESSION_KEY, DEVICE_ID, ANON_ID]):
        print("✗ Missing env vars — check your .env file")
        raise SystemExit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)  # keep alive when hidden to tray

    win = MainWindow()
    win.show()

    win._hwnd = int(win.winId())
    apply_acrylic(win._hwnd, tint_abgr=0xCC0a0814)

    sys.exit(app.exec())
