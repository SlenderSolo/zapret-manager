import sys
import ctypes
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, 
                             QCheckBox, QMessageBox, QComboBox)

from src.service_manager import install, uninstall, status, list_presets
from src.utils import run_as_admin

COLOR_BG_OFF = QColor("#3A3A3A")
COLOR_BORDER_OFF = QColor("#888888")
COLOR_CIRCLE_OFF = QColor("#CCCCCC")
COLOR_BG_ON = QColor("#60CDFF")
COLOR_CIRCLE_ON = QColor("#000000")

STYLESHEET = """
QWidget {
    background-color: #1E1E1E;
    color: white;
    font-family: "Segoe UI", Arial;
}

QComboBox {
    background-color: #2D2D2D;
    border: 1px solid #444;
    border-radius: 12px;
    padding: 15px 20px;
    font-size: 24px;
    font-weight: bold;
    min-height: 50px;
}

QComboBox::drop-down {
    border: 0px;
    width: 50px;
}

QComboBox:hover {
    border: 1px solid #60CDFF;
    background-color: #333;
}

QComboBox QAbstractItemView {
    background-color: #2D2D2D;
    border: 1px solid #444;
    selection-background-color: #60CDFF;
    selection-color: black;
    outline: none;
    font-size: 24px;
}

QComboBox QAbstractItemView::item {
    padding: 10px; 
    min-height: 40px;
}
"""


class BigToggle(QCheckBox):
    """Large animated toggle switch (160x84)."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(160, 84) 
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._position = 0.0
        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

    @pyqtProperty(float)
    def position(self): 
        return self._position

    @position.setter
    def position(self, pos):
        self._position = pos
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        w, h = rect.width(), rect.height()
        radius = h / 2
        margin = 8 
        circle_size = h - (margin * 2)

        if self.isChecked() or self._position > 0.5:
            p.setBrush(QBrush(COLOR_BG_ON))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(0, 0, w, h, radius, radius)
        else:
            p.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            pen = QPen(COLOR_BORDER_OFF)
            pen.setWidth(4)
            p.setPen(pen)
            p.drawRoundedRect(2, 2, w-4, h-4, radius-2, radius-2)

        if self.isChecked() or self._position > 0.5:
             p.setBrush(QBrush(COLOR_CIRCLE_ON))
        else:
             p.setBrush(QBrush(COLOR_CIRCLE_OFF))
        
        p.setPen(Qt.PenStyle.NoPen)
        x_off = margin
        x_on = w - margin - circle_size
        current_x = x_off + (x_on - x_off) * self._position
        p.drawEllipse(int(current_x), margin, int(circle_size), int(circle_size))

    def hitButton(self, pos): 
        return self.contentsRect().contains(pos)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Zapret Control")
        self.resize(500, 600)
        self.setStyleSheet(STYLESHEET)

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter) 
        layout.setContentsMargins(40, 60, 40, 40)
        layout.setSpacing(30)
        self.setLayout(layout)

        self.label_status = QLabel("CHECKING...")
        font_status = QFont("Segoe UI", 36, QFont.Weight.Bold)
        self.label_status.setFont(font_status)
        self.label_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label_status)

        layout.addSpacing(10)

        self.toggle = BigToggle()
        self.toggle.clicked.connect(self.on_toggle_click)
        layout.addWidget(self.toggle, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(30)

        lbl_preset = QLabel("ACTIVE PRESET")
        lbl_preset.setStyleSheet("color: #888; font-size: 22px; font-weight: bold; letter-spacing: 1px;")
        lbl_preset.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_preset)

        self.combo_presets = QComboBox()
        self.combo_presets.setCursor(Qt.CursorShape.PointingHandCursor)
        self.combo_presets.setFixedWidth(320)
        self.refresh_presets()
        self.combo_presets.activated.connect(self.on_preset_selected)
        layout.addWidget(self.combo_presets, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        self.sync_status()

    def refresh_presets(self):
        self.combo_presets.clear()
        presets = list_presets()
        
        if not presets:
            self.combo_presets.addItem("No presets found!")
            self.combo_presets.setEnabled(False)
        else:
            self.combo_presets.addItems(presets)
            self.combo_presets.setEnabled(True)

    def sync_status(self):
        info = status()
        
        is_running = (info.status == "RUNNING")
        
        self.label_status.setText(info.status)
        if is_running:
            self.label_status.setStyleSheet("color: #60CDFF;")
        else:
            self.label_status.setStyleSheet("color: #999;")

        self.toggle.blockSignals(True)
        self.toggle.setChecked(is_running)
        self.toggle.position = 1.0 if is_running else 0.0
        self.toggle.blockSignals(False)

        self.combo_presets.blockSignals(True)
        
        if is_running:
            preset_name = info.preset
            
            index = self.combo_presets.findText(preset_name, Qt.MatchFlag.MatchExactly)
            
            if index < 0:
                for i in range(self.combo_presets.count()):
                    item_text = self.combo_presets.itemText(i)
                    if preset_name.lower().startswith(item_text.lower()):
                         index = i
                         break

            if index >= 0:
                self.combo_presets.setCurrentIndex(index)
            else:
                self.combo_presets.addItem(preset_name)
                self.combo_presets.setCurrentIndex(self.combo_presets.count() - 1)
            
        self.combo_presets.blockSignals(False)

    def on_toggle_click(self):
        if self.toggle.isChecked():
            self.install_and_start()
        else:
            self.stop_and_delete()

    def on_preset_selected(self, index):
        if self.toggle.isChecked():
            self.install_and_start()

    def stop_and_delete(self):
        self.label_status.setText("STOPPING...")
        self.label_status.setStyleSheet("color: #E0E0E0;")
        self.toggle.setEnabled(False)
        QApplication.processEvents()

        uninstall()
        
        self.sync_status()
        self.toggle.setEnabled(True)

    def install_and_start(self):
        selected_file = self.combo_presets.currentText()
        
        if not selected_file or "No presets" in selected_file:
            QMessageBox.warning(self, "Warning", "Select a preset first.")
            self.sync_status()
            return

        self.label_status.setText("STARTING...")
        self.label_status.setStyleSheet("color: #E0E0E0;")
        self.toggle.setEnabled(False)
        QApplication.processEvents()

        success, error = install(selected_file)
        
        if not success:
            QMessageBox.critical(self, "Error", f"Operation failed:\n\n{error}")
            self.sync_status()
        else:
            self.sync_status()
        
        self.toggle.setEnabled(True)


if __name__ == "__main__":
    run_as_admin()
    
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    if hasattr(QApplication, 'setHighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    app = QApplication(sys.argv)
    if sys.platform == 'win32':
        try: 
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except: 
            pass

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
