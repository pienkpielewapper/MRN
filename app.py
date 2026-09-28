import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digilent.api import DigilentApi
from digilent.types import AvailableDevice


class DeviceDialog(QDialog):
    def __init__(self, api: DigilentApi, parent: QWidget | None = None):
        super().__init__(parent)
        self.api = api
        self.devices: list[AvailableDevice] = []
        self.selected_device: AvailableDevice | None = None

        self.setWindowTitle("Select a device")
        self.setMinimumSize(640, 360)

        title = QLabel("Available devices")
        title.setObjectName("dialogTitle")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Device", "Serial number", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._update_connect_button)
        self.table.itemDoubleClicked.connect(self._connect_selected)

        self.empty_label = QLabel()
        self.empty_label.setObjectName("mutedText")

        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("secondaryButton")
        refresh_button.clicked.connect(self.load_devices)

        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("secondaryButton")
        cancel_button.clicked.connect(self.reject)

        self.connect_button = QPushButton("Connect")
        self.connect_button.setEnabled(False)
        self.connect_button.clicked.connect(self._connect_selected)

        actions = QHBoxLayout()
        actions.addWidget(refresh_button)
        actions.addStretch()
        actions.addWidget(cancel_button)
        actions.addWidget(self.connect_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(self.table)
        layout.addWidget(self.empty_label)
        layout.addLayout(actions)

        self.load_devices()

    def load_devices(self):
        self.table.setRowCount(0)
        self.empty_label.clear()
        self.connect_button.setEnabled(False)

        try:
            self.devices = self.api.get_available_devices() or []
            print(self.devices)
        except Exception as error:
            self.devices = []
            QMessageBox.critical(self, "Device retrieval failed", str(error))
            self.empty_label.setText("Unable to retrieve devices.")
            return

        self.table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            error = device.get("error")
            values = (device["name"], device["serialnum"], error or "Available")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if error:
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, column, item)

        if not self.devices:
            self.empty_label.setText("No devices found.")

    def _selected_row(self) -> int:
        selected_rows = self.table.selectionModel().selectedRows()
        return selected_rows[0].row() if selected_rows else -1

    def _update_connect_button(self):
        row = self._selected_row()
        can_connect = row >= 0 and not self.devices[row].get("error")
        self.connect_button.setEnabled(can_connect)

    def _connect_selected(self):
        row = self._selected_row()
        if row < 0:
            return

        device = self.devices[row]
        if device.get("error"):
            QMessageBox.critical(self, "Device unavailable", device["error"])
            return

        try:
            error = self.api.connect_to_device(device["idx"])
        except Exception as exception:
            QMessageBox.critical(self, "Connection failed", str(exception))
            return

        if error:
            QMessageBox.critical(self, "Connection failed", error)
            return

        self.selected_device = device
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api: DigilentApi | None = None
        self.selected_device: AvailableDevice | None = None

        self.setWindowTitle("Digilent Controller")
        self.resize(1100, 700)
        self.setMinimumSize(820, 520)

        self.device_button = QPushButton("No device selected")
        self.device_button.setObjectName("deviceButton")
        self.device_button.clicked.connect(self.open_device_dialog)

        brand = QLabel("MRN Instruments")
        brand.setObjectName("brand")

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(68)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(24, 0, 20, 0)
        top_layout.addWidget(brand)
        top_layout.addStretch()
        top_layout.addWidget(self.device_button)

        side_bar = QFrame()
        side_bar.setObjectName("sideBar")
        side_bar.setFixedWidth(220)
        side_layout = QVBoxLayout(side_bar)
        side_layout.setContentsMargins(16, 24, 16, 24)
        side_layout.setSpacing(8)

        for label in ("Dashboard", "Oscilloscope", "Settings"):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            if label == "Dashboard":
                button.setChecked(True)
            side_layout.addWidget(button)
        side_layout.addStretch()

        page_title = QLabel("Dashboard")
        page_title.setObjectName("pageTitle")
        self.status = QLabel("Select a device to begin.")
        self.status.setObjectName("mutedText")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 32, 32, 32)
        content_layout.addWidget(page_title)
        content_layout.addWidget(self.status)
        content_layout.addStretch()

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(side_bar)
        body_layout.addWidget(content, 1)

        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(top_bar)
        shell_layout.addWidget(body, 1)
        self.setCentralWidget(shell)

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f4f6f8;
                color: #17212b;
                font-family: "Segoe UI";
                font-size: 14px;
            }
            #topBar {
                background: #ffffff;
                border-bottom: 1px solid #dce2e8;
            }
            #brand {
                font-size: 18px;
                font-weight: 700;
                color: #102a43;
            }
            #sideBar {
                background: #102a43;
                border: none;
            }
            #navButton {
                background: transparent;
                color: #cbd9e6;
                border: none;
                border-radius: 4px;
                padding: 11px 14px;
                text-align: left;
            }
            #navButton:hover, #navButton:checked {
                background: #1f4868;
                color: #ffffff;
            }
            #pageTitle, #dialogTitle {
                font-size: 24px;
                font-weight: 700;
                color: #102a43;
            }
            #mutedText {
                color: #607080;
            }
            QPushButton {
                background: #087e8b;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 9px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #076b76;
            }
            QPushButton:disabled {
                background: #b9c4cc;
                color: #edf1f4;
            }
            #deviceButton, #secondaryButton {
                background: #ffffff;
                color: #243b53;
                border: 1px solid #bcc8d3;
            }
            #deviceButton:hover, #secondaryButton:hover {
                background: #edf3f7;
            }
            QDialog {
                background: #f8fafb;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #d7dfe6;
                border-radius: 4px;
                gridline-color: #e7ecf0;
                selection-background-color: #d8eef0;
                selection-color: #17212b;
            }
            QHeaderView::section {
                background: #edf2f5;
                color: #405466;
                border: none;
                border-bottom: 1px solid #d7dfe6;
                padding: 10px;
                font-weight: 600;
            }
            """
        )

    def open_device_dialog(self):
        if self.api is None:
            try:
                self.api = DigilentApi()
            except Exception as error:
                QMessageBox.critical(self, "DWF library unavailable", str(error))
                return

        dialog = DeviceDialog(self.api, self)
        if dialog.exec() != QDialog.Accepted or dialog.selected_device is None:
            return

        self.selected_device = dialog.selected_device
        self.device_button.setText(self.selected_device["name"])
        self.status.setText(
            f"Connected to {self.selected_device['name']} "
            f"({self.selected_device['serialnum']})."
        )


if __name__ == "__main__":
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(application.exec())