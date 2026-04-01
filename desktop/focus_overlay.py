"""
Floating TikTok overlay for focus sessions.

The user logs in manually inside the embedded browser. Session cookies are kept
inside a local Qt profile directory so the app can reopen TikTok without asking
for credentials every run.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QColor, QKeySequence, QMouseEvent, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

TIKTOK_URL = "https://www.tiktok.com/foryou"
PROFILE_DIR = Path(__file__).resolve().parent.parent / ".webprofile" / "tiktok-overlay"


class FocusOverlayWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._drag_origin: QPoint | None = None
        self._fly_angle = 0.0
        self._fly_velocity = QPoint(3, 2)
        self._autoscroll_enabled = False

        self.setWindowTitle("OpenClaw Focus Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(430, 820)

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)

        shell = QFrame()
        shell.setObjectName("shell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(14, 14, 14, 14)
        shell_layout.setSpacing(10)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(0, 0, 0, 180))
        shell.setGraphicsEffect(shadow)

        shell_layout.addLayout(self._build_titlebar())
        shell_layout.addLayout(self._build_controls())
        shell_layout.addWidget(self._build_browser(), 1)
        shell_layout.addWidget(self._build_footer())

        root_layout.addWidget(shell)
        self.setCentralWidget(root)
        self._apply_theme()
        self._setup_shortcuts()
        self._setup_timers()

        self.web.load(QUrl(TIKTOK_URL))
        self._set_status("Авторизуйся в TikTok прямо внутри окна, потом жми Autoscroll.")

    def _build_titlebar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        self.title_label = QLabel("OpenClaw Air Mode")
        self.title_label.setObjectName("title")

        self.pin_label = QLabel("always on top")
        self.pin_label.setObjectName("chip")

        self.close_button = QPushButton("×")
        self.close_button.setFixedSize(32, 32)
        self.close_button.clicked.connect(self.close)

        layout.addWidget(self.title_label)
        layout.addStretch(1)
        layout.addWidget(self.pin_label)
        layout.addWidget(self.close_button)
        return layout

    def _build_controls(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        self.auth_button = QPushButton("Login / Reload")
        self.auth_button.clicked.connect(self._reload_feed)

        self.scroll_button = QPushButton("Autoscroll: OFF")
        self.scroll_button.clicked.connect(self._toggle_autoscroll)

        self.fly_button = QPushButton("Fly: OFF")
        self.fly_button.clicked.connect(self._toggle_fly_mode)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(1, 10)
        self.speed_slider.setValue(5)
        self.speed_slider.valueChanged.connect(self._update_speed_label)

        self.speed_label = QLabel()
        self.speed_label.setMinimumWidth(72)
        self._update_speed_label(self.speed_slider.value())

        layout.addWidget(self.auth_button)
        layout.addWidget(self.scroll_button)
        layout.addWidget(self.fly_button)
        layout.addWidget(self.speed_slider, 1)
        layout.addWidget(self.speed_label)
        return layout

    def _build_browser(self) -> QWebEngineView:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile("openclaw-tiktok", self)
        profile.setPersistentStoragePath(str(PROFILE_DIR))
        profile.setCachePath(str(PROFILE_DIR / "cache"))
        profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        profile.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        profile.settings().setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)

        self.web = QWebEngineView(self)
        page = QWebEnginePage(profile, self.web)
        self.web.setPage(page)
        self.web.urlChanged.connect(lambda url: self._set_status(f"Открыто: {url.toString()}"))
        self.web.loadFinished.connect(self._on_load_finished)
        return self.web

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        layout.addWidget(self.status_label, 1)
        return footer

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Space"), self, activated=self._toggle_autoscroll)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._reload_feed)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._toggle_fly_mode)

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    def _setup_timers(self) -> None:
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._autoscroll_step)

        self.fly_timer = QTimer(self)
        self.fly_timer.setInterval(16)
        self.fly_timer.timeout.connect(self._fly_step)

    def _apply_theme(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#0f1115"))
        self.setPalette(palette)
        self.setStyleSheet(
            """
            QFrame#shell {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #181d24,
                    stop:1 #0b0d11
                );
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 24px;
            }
            QLabel {
                color: #ecf2ff;
                font-size: 13px;
            }
            QLabel#title {
                font-size: 20px;
                font-weight: 700;
                color: #ffffff;
            }
            QLabel#chip {
                color: #8bf6c9;
                background: rgba(139,246,201,0.12);
                border: 1px solid rgba(139,246,201,0.35);
                border-radius: 10px;
                padding: 6px 10px;
            }
            QPushButton {
                color: #fff4e8;
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 12px;
                padding: 10px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.14);
            }
            QSlider::groove:horizontal {
                height: 6px;
                border-radius: 3px;
                background: rgba(255,255,255,0.14);
            }
            QSlider::handle:horizontal {
                width: 18px;
                margin: -7px 0;
                border-radius: 9px;
                background: #ffb469;
            }
            QWebEngineView {
                border-radius: 18px;
                background: #050608;
            }
            """
        )

    def _reload_feed(self) -> None:
        self.web.load(QUrl(TIKTOK_URL))
        self._set_status("Перезагружаю ленту TikTok.")

    def _toggle_autoscroll(self) -> None:
        self._autoscroll_enabled = not self._autoscroll_enabled
        self.scroll_button.setText(f"Autoscroll: {'ON' if self._autoscroll_enabled else 'OFF'}")
        if self._autoscroll_enabled:
            self.scroll_timer.start(self._scroll_interval_ms())
            self._set_status("Автоскролл включён. Если TikTok просит логин, авторизуйся во встроенном окне.")
        else:
            self.scroll_timer.stop()
            self._set_status("Автоскролл на паузе.")

    def _toggle_fly_mode(self) -> None:
        enabled = not self.fly_timer.isActive()
        self.fly_button.setText(f"Fly: {'ON' if enabled else 'OFF'}")
        if enabled:
            self.fly_timer.start()
            self._set_status("Fly mode включён. Окно мягко дрейфует поверх остальных.")
        else:
            self.fly_timer.stop()
            self._set_status("Fly mode выключен.")

    def _update_speed_label(self, value: int) -> None:
        self.speed_label.setText(f"speed {value}/10")
        if self._autoscroll_enabled:
            self.scroll_timer.start(self._scroll_interval_ms())

    def _scroll_interval_ms(self) -> int:
        speed = self.speed_slider.value()
        return max(800, 3800 - speed * 280)

    def _autoscroll_step(self) -> None:
        step_factor = 0.82 + self.speed_slider.value() * 0.08
        js = f"""
            (() => {{
                const root = document.scrollingElement || document.documentElement || document.body;
                const top = Math.round((window.innerHeight || 800) * {step_factor:.2f});
                root.scrollBy({{ top, left: 0, behavior: 'smooth' }});
                return {{ y: root.scrollTop, title: document.title }};
            }})()
        """
        self.web.page().runJavaScript(js, self._handle_scroll_result)

    def _handle_scroll_result(self, result: object) -> None:
        if not isinstance(result, dict):
            self._set_status("Скролл отправлен. Если ничего не двигается, открой ленту For You после логина.")
            return
        y_pos = result.get("y", 0)
        title = result.get("title", "TikTok")
        self._set_status(f"Autoscroll active. {title} • scrollY {y_pos}")

    def _fly_step(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return

        bounds: QRect = screen.availableGeometry()
        pos = self.frameGeometry().topLeft()
        next_x = pos.x() + self._fly_velocity.x()
        next_y = pos.y() + self._fly_velocity.y()

        if next_x <= bounds.left() or next_x + self.width() >= bounds.right():
            self._fly_velocity.setX(-self._fly_velocity.x())
        if next_y <= bounds.top() or next_y + self.height() >= bounds.bottom():
            self._fly_velocity.setY(-self._fly_velocity.y())

        self._fly_angle += 0.08
        wobble = math.sin(self._fly_angle) * 2.2
        self.move(
            pos.x() + self._fly_velocity.x(),
            int(pos.y() + self._fly_velocity.y() + wobble),
        )

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._set_status("Страница загружена. Войди в TikTok внутри окна и включай Autoscroll.")
        else:
            self._set_status("Не удалось загрузить TikTok. Проверь сеть или открой страницу ещё раз.")

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)


def run_focus_overlay() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = FocusOverlayWindow()
    screen = app.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        window.move(area.right() - window.width() - 36, area.center().y() - window.height() // 2)
    window.show()
    window.raise_()
    return app.exec()
