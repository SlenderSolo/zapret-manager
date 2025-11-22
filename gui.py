import sys
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, QSize, QTimer
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, 
                             QCheckBox, QMessageBox, QComboBox, QSizePolicy)

from src.service_manager import install, uninstall, status, list_presets
from src.utils import run_as_admin


# ============================================================================
# Theme
# ============================================================================

class Theme:
    """Application colors."""
    BG_PRIMARY = "#1E1E1E"
    BG_SECONDARY = "#2D2D2D"
    BG_HOVER = "#333333"
    ACCENT = "#60CDFF"
    
    STATUS_RUNNING = "#60CDFF"
    STATUS_STOPPED = "#999"
    STATUS_BUSY = "#E0E0E0"
    
    @staticmethod
    def stylesheet() -> str:
        return f"""
            QWidget {{
                background-color: {Theme.BG_PRIMARY};
                color: white;
                font-family: "Segoe UI", Arial;
            }}
            QComboBox {{
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid #444;
                border-radius: 0.5em;
                padding: 0.5em 1em;
                font-size: 12pt;
                font-weight: bold;
                min-height: 1.5em;
            }}
            QComboBox::drop-down {{ border: 0px; width: 2em; }}
            QComboBox:hover {{
                border: 1px solid {Theme.ACCENT};
                background-color: {Theme.BG_HOVER};
            }}
            QComboBox QAbstractItemView {{
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid #444;
                selection-background-color: {Theme.ACCENT};
                selection-color: black;
                outline: none;
                font-size: 12pt;
            }}
            QComboBox QAbstractItemView::item {{ padding: 0.5em; min-height: 1.5em; }}
        """


# ============================================================================
# Toggle Switch
# ============================================================================

class AnimatedToggle(QCheckBox):
    """Animated toggle switch."""
    
    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)
        self._width = width
        self._height = height
        self._position = 0.0
        
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def sizeHint(self):
        return QSize(self._width, self._height)

    @pyqtProperty(float)
    def position(self):
        return self._position

    @position.setter
    def position(self, pos):
        self._position = pos
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        radius = h / 2
        margin = max(4, h // 10)
        circle_size = h - (margin * 2)
        on = self.isChecked()
        
        # Background
        if on:
            p.setBrush(QBrush(QColor(Theme.ACCENT)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(0, 0, w, h, radius, radius)
        else:
            p.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            p.setPen(QPen(QColor("#888"), max(2, h // 20)))
            p.drawRoundedRect(2, 2, w-4, h-4, radius-2, radius-2)

        # Circle
        p.setBrush(QBrush(QColor("#000" if on else "#CCC")))
        p.setPen(Qt.PenStyle.NoPen)
        x_pos = margin + (w - margin * 2 - circle_size) * self._position
        p.drawEllipse(int(x_pos), margin, circle_size, circle_size)

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)


# ============================================================================
# Main Window
# ============================================================================

class MainWindow(QWidget):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self._busy = False
        
        # Calculate dimensions from font
        base = self.fontMetrics().height()
        self._d = {
            'win': base * 25, 'margin': base, 'space': int(base * 1.5),
            'status_font': int(base * 3), 'preset_font': int(base * 1.1),
            'combo_w': base * 15, 'toggle_w': base * 8, 'toggle_h': base * 4,
        }
        
        self._setup_window()
        self._setup_ui()
        self._load_state()

    def _setup_window(self):
        self.setWindowTitle("Zapret Control")
        self.setFont(QFont("Segoe UI", 8))
        self.setFixedSize(self._d['win'], self._d['win'])
        self.setStyleSheet(Theme.stylesheet())

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        layout.setContentsMargins(self._d['margin'], self._d['margin'] * 2, 
                                 self._d['margin'], self._d['margin'])
        layout.setSpacing(self._d['space'])
        self.setLayout(layout)

        # Status
        self.status_label = QLabel("CHECKING")
        font = QFont("Segoe UI", -1, QFont.Weight.Bold)
        font.setPixelSize(self._d['status_font'])
        self.status_label.setFont(font)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        layout.addSpacing(self._d['space'] // 3)

        # Toggle
        self.toggle = AnimatedToggle(self._d['toggle_w'], self._d['toggle_h'])
        self.toggle.clicked.connect(self._handle_toggle)
        layout.addWidget(self.toggle, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(self._d['space'])

        # Preset label
        label = QLabel("ACTIVE PRESET")
        font = QFont("Segoe UI", -1, QFont.Weight.Bold)
        font.setPixelSize(self._d['preset_font'])
        label.setFont(font)
        label.setStyleSheet("color: #888; letter-spacing: 0.1em;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        
        # Preset combo
        self.preset_combo = QComboBox()
        self.preset_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preset_combo.setFixedWidth(self._d['combo_w'])
        self.preset_combo.activated.connect(self._handle_preset_change)
        
        layout.addWidget(self.preset_combo, alignment=Qt.AlignmentFlag.AlignCenter)
        
        layout.addStretch()

    def _load_state(self):
        self._refresh_presets()
        self._sync_with_service()

    def _refresh_presets(self):
        self.preset_combo.clear()
        presets = list_presets()
        
        if presets:
            # addItems быстрее чем addItem в цикле
            self.preset_combo.addItems(presets)
        else:
            self.preset_combo.addItem("No presets found!")
            self.preset_combo.setEnabled(False)

    def _sync_with_service(self):
        info = status()
        running = (info.status == "RUNNING")
        
        # Update status
        self.status_label.setText("RUNNING" if running else "NOT INSTALLED")
        color = Theme.STATUS_RUNNING if running else Theme.STATUS_STOPPED
        self.status_label.setStyleSheet(f"color: {color};")
        
        # Update toggle
        self.toggle.blockSignals(True)
        self.toggle.setChecked(running)
        self.toggle.position = 1.0 if running else 0.0
        self.toggle.blockSignals(False)
        
        # Update preset
        if running and info.preset != "Unknown":
            self._select_preset(info.preset)

    def _select_preset(self, name: str):
        self.preset_combo.blockSignals(True)
        
        # Find exact match (service returns clean preset name)
        idx = self.preset_combo.findText(name, Qt.MatchFlag.MatchExactly)
        
        # If not found, add it (handles case when preset file was deleted)
        if idx < 0:
            self.preset_combo.addItem(name)
            idx = self.preset_combo.count() - 1
        
        self.preset_combo.setCurrentIndex(idx)
        self.preset_combo.blockSignals(False)

    def _handle_toggle(self):
        if self._busy:
            return
        
        if self.toggle.isChecked():
            self._install()
        else:
            self._uninstall()

    def _handle_preset_change(self, index):
        if self._busy or not self.toggle.isChecked():
            return
        self._install()

    def _set_busy(self, busy: bool, text: str = None):
        self._busy = busy
        self.toggle.setEnabled(not busy)
        if text:
            self.status_label.setText(text)
            self.status_label.setStyleSheet(f"color: {Theme.STATUS_BUSY};")
            QApplication.processEvents()

    def _uninstall(self):
        self._set_busy(True, "UNINSTALLING")
        uninstall()
        self._sync_with_service()
        self._set_busy(False)

    def _install(self):
        preset = self.preset_combo.currentText()
        
        if not preset or "No presets" in preset:
            QMessageBox.warning(self, "Warning", "Select a preset first.")
            self._sync_with_service()
            return

        self._set_busy(True, "INSTALLING")
        success, error = install(preset)
        
        if not success:
            QMessageBox.critical(self, "Error", f"Failed:\n\n{error}")
        
        self._sync_with_service()
        self._set_busy(False)


# ============================================================================
# Entry Point
# ============================================================================

def main():
    run_as_admin()
    
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
