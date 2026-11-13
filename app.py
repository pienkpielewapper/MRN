import csv
import math
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digilent.api import DigilentApi
from digilent.signals import generate_pulse
from digilent.types import AvailableDevice
from model.ultrasonic import review_ultrasonic_signals


SCIENTIFIC_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
SCIENTIFIC_PARTIAL = re.compile(
    r"^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d*)?)?$"
)
ANALYSIS_SAMPLE_COUNT = 15_000


@dataclass
class AnalysisResult:
    acquisition: int
    raw_time_of_flight: float
    adjusted_time_of_flight: float
    attenuation: float
    reference_peak: tuple[float, float]
    measured_peak: tuple[float, float]
    time: np.ndarray
    reference: np.ndarray
    measured: np.ndarray
    snr_before: float
    snr_after: float


@dataclass
class CalibrationResult:
    acquisition: int
    time_of_flight: float
    transmission_loss: float
    retained: bool = False
    transmission_retained: bool = False


@dataclass
class AnalysisSummary:
    mean_time_of_flight: float
    velocity: float
    attenuation: float
    mean_snr_before: float
    mean_snr_after: float


class ScientificDoubleSpinBox(QDoubleSpinBox):
    def _number_text(self, text: str) -> str:
        suffix = self.suffix()
        if suffix and text.endswith(suffix):
            text = text[:-len(suffix)]
        return text.strip()

    def validate(
        self, text: str, position: int
    ) -> tuple[QValidator.State, str, int]:
        number_text = self._number_text(text)
        if SCIENTIFIC_NUMBER.fullmatch(number_text):
            value = float(number_text)
            if math.isfinite(value) and self.minimum() <= value <= self.maximum():
                return QValidator.State.Acceptable, text, position
        if SCIENTIFIC_PARTIAL.fullmatch(number_text):
            return QValidator.State.Intermediate, text, position
        return QValidator.State.Invalid, text, position

    def valueFromText(self, text: str) -> float:
        return float(self._number_text(text))

    def textFromValue(self, value: float) -> str:
        return f"{value:g}"


class ScientificIntegerSpinBox(QSpinBox):
    def _number_text(self, text: str) -> str:
        suffix = self.suffix()
        if suffix and text.endswith(suffix):
            text = text[:-len(suffix)]
        return text.strip()

    def _integer_value(self, text: str) -> int | None:
        try:
            value = float(self._number_text(text))
        except ValueError:
            return None
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)

    def validate(
        self, text: str, position: int
    ) -> tuple[QValidator.State, str, int]:
        value = self._integer_value(text)
        if value is not None and self.minimum() <= value <= self.maximum():
            return QValidator.State.Acceptable, text, position
        if SCIENTIFIC_PARTIAL.fullmatch(self._number_text(text)):
            return QValidator.State.Intermediate, text, position
        return QValidator.State.Invalid, text, position

    def valueFromText(self, text: str) -> int:
        value = self._integer_value(text)
        return self.minimum() if value is None else value

    def textFromValue(self, value: int) -> str:
        return f"{value:g}"


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
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
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
            status = "Already connected" if device["connected"] else error or "Available"
            values = (device["name"], device["serialnum"], status)
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
        error = device.get("error")
        if error:
            QMessageBox.critical(self, "Device unavailable", error)
            return

        if device["connected"]:
            self.selected_device = device
            self.accept()
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


class HardwareWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], object]):
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self):
        try:
            self.completed.emit(self.operation())
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api: DigilentApi | None = None
        self.selected_device: AvailableDevice | None = None
        self.oscilloscope_configured = False
        self.last_time: np.ndarray | None = None
        self.last_measured: np.ndarray | None = None
        self.last_reference: np.ndarray | None = None
        self.analysis_results: list[AnalysisResult] = []
        self.calibration_results: list[CalibrationResult] = []
        self.calibration_delay_s: float | None = None
        self.calibration_transmission_loss_db: float | None = None
        self.analysis_summary: AnalysisSummary | None = None
        self.analysis_specimen_length_m: float | None = None
        self.analysis_metadata: dict[str, float] = {}
        self.analysis_plot_items: list[object] = []
        self.analysis_segment_starts: list[float] = []
        self.hardware_thread: QThread | None = None
        self.hardware_worker: HardwareWorker | None = None
        self.operation_success: Callable[[object], None] | None = None
        self.operation_status_label: QLabel | None = None
        self.pending_specimen_operation: Callable[[], object] | None = None
        self.post_operation_action: Callable[[], None] | None = None

        self.setWindowTitle("Digilent Controller")
        self.resize(1280, 860)
        self.setMinimumSize(960, 680)

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

        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        navigation_labels = ("Dashboard", "Oscilloscope", "Analysis", "Settings")
        for index, label in enumerate(navigation_labels):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            self.navigation_group.addButton(button, index)
            if index == 0:
                button.setChecked(True)
            side_layout.addWidget(button)
        side_layout.addStretch()

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_dashboard_page())
        self.pages.addWidget(self._build_oscilloscope_page())
        self.pages.addWidget(self._build_analysis_page())
        self.pages.addWidget(self._build_settings_page())
        self.navigation_group.idClicked.connect(self.pages.setCurrentIndex)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(side_bar)
        body_layout.addWidget(self.pages, 1)

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
            #sectionTitle {
                font-size: 17px;
                font-weight: 700;
                color: #243b53;
            }
            #section {
                background: #ffffff;
                border-top: 1px solid #d7dfe6;
            }
            #mutedText {
                color: #607080;
            }
            #statusText {
                color: #405466;
                background: #eaf2f5;
                border-left: 3px solid #087e8b;
                padding: 10px 12px;
            }
            #summaryValue {
                color: #102a43;
                font-size: 16px;
                font-weight: 700;
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
            QComboBox, QSpinBox, QDoubleSpinBox {
                background: #ffffff;
                color: #17212b;
                border: 1px solid #bcc8d3;
                border-radius: 3px;
                padding: 7px 9px;
                min-height: 22px;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #087e8b;
            }
            QScrollArea {
                border: none;
                background: #f4f6f8;
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

    def _page_heading(self, title: str) -> QLabel:
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        return heading

    def _section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        section = QFrame()
        section.setObjectName("section")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        return section, layout

    def _blank_double(self, maximum: float, suffix: str = "") -> QDoubleSpinBox:
        control = ScientificDoubleSpinBox()
        control.setRange(0, maximum)
        control.setDecimals(3)
        control.setSpecialValueText("")
        control.setValue(0)
        control.setSuffix(suffix)
        control.setSingleStep(1)
        return control

    def _blank_integer(self, maximum: int, suffix: str = "") -> QSpinBox:
        control = ScientificIntegerSpinBox()
        control.setRange(0, maximum)
        control.setSpecialValueText("")
        control.setValue(0)
        control.setSuffix(suffix)
        return control

    def _placeholder_combo(self, label: str) -> QComboBox:
        control = QComboBox()
        control.addItem(label, None)
        return control

    def _add_field(
        self,
        layout: QGridLayout,
        row: int,
        column: int,
        label: str,
        control: QWidget,
    ) -> None:
        label_widget = QLabel(label)
        layout.addWidget(label_widget, row * 2, column)
        layout.addWidget(control, row * 2 + 1, column)

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)
        layout.addWidget(self._page_heading("Dashboard"))

        connection_section, connection_layout = self._section("Connection")
        self.dashboard_connection = QLabel("No device connected")
        self.dashboard_connection.setObjectName("summaryValue")
        connection_layout.addWidget(self.dashboard_connection)
        layout.addWidget(connection_section)

        workflow_section, workflow_layout = self._section("Acquisition state")
        self.dashboard_acquisition = QLabel("Not configured")
        self.dashboard_acquisition.setObjectName("summaryValue")
        workflow_layout.addWidget(self.dashboard_acquisition)
        layout.addWidget(workflow_section)
        layout.addStretch()
        return page

    def _build_oscilloscope_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(18)
        layout.addWidget(self._page_heading("Oscilloscope"))

        self.status = QLabel("Connect a device to begin.")
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        scope_section, scope_layout = self._section("1. Configure oscilloscope")
        scope_grid = QGridLayout()
        scope_grid.setHorizontalSpacing(18)
        scope_grid.setVerticalSpacing(8)
        scope_grid.setColumnStretch(0, 1)
        scope_grid.setColumnStretch(1, 1)
        scope_grid.setColumnStretch(2, 1)

        self.mode_input = self._placeholder_combo("Select mode")
        self.mode_input.addItem("Read", "read")
        self.mode_input.addItem("Record", "record")
        self.sample_rate_input = self._blank_double(1_000_000_000, " Hz")
        self.sample_count_input = self._blank_integer(1_000_000_000, " samples")
        self.measured_channel_input = self._placeholder_combo("Select channel")
        self.reference_channel_input = self._placeholder_combo("Select channel")
        self.measured_range_input = self._blank_double(1_000_000, " V")
        self.reference_range_input = self._blank_double(1_000_000, " V")
        self.filter_input = self._placeholder_combo("Select filter")
        for label, value in (
            ("Decimate", "filterDecimate"),
            ("Average", "filterAverage"),
            ("Min / max", "filterMinMax"),
            ("Average fit", "filterAverageFit"),
        ):
            self.filter_input.addItem(label, value)

        self._add_field(scope_grid, 0, 0, "Mode", self.mode_input)
        self._add_field(scope_grid, 0, 1, "Sample rate", self.sample_rate_input)
        self._add_field(scope_grid, 0, 2, "Sample count", self.sample_count_input)
        self._add_field(scope_grid, 1, 0, "Measured channel", self.measured_channel_input)
        self._add_field(scope_grid, 1, 1, "Measured range", self.measured_range_input)
        self._add_field(scope_grid, 1, 2, "Filter", self.filter_input)
        self._add_field(scope_grid, 2, 0, "Reference channel", self.reference_channel_input)
        self._add_field(scope_grid, 2, 1, "Reference range", self.reference_range_input)
        scope_layout.addLayout(scope_grid)

        self.configure_button = QPushButton("Apply oscilloscope configuration")
        self.configure_button.setEnabled(False)
        self.configure_button.clicked.connect(self.configure_oscilloscope)
        scope_layout.addWidget(self.configure_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(scope_section)

        wave_section, wave_layout = self._section("2. Configure pulse")
        wave_grid = QGridLayout()
        wave_grid.setHorizontalSpacing(18)
        wave_grid.setColumnStretch(0, 1)
        wave_grid.setColumnStretch(1, 1)
        wave_grid.setColumnStretch(2, 1)
        wave_grid.setColumnStretch(3, 1)
        self.output_channel_input = self._placeholder_combo("Select channel")
        self.center_frequency_input = self._blank_double(1_000_000_000, " Hz")
        self.cycle_count_input = self._blank_integer(1_000_000, " cycles")
        self.amplitude_input = self._blank_double(1_000_000, " V")
        self._add_field(wave_grid, 0, 0, "Output channel", self.output_channel_input)
        self._add_field(wave_grid, 0, 1, "Center frequency", self.center_frequency_input)
        self._add_field(wave_grid, 0, 2, "Cycle count", self.cycle_count_input)
        self._add_field(wave_grid, 0, 3, "Amplitude", self.amplitude_input)
        wave_layout.addLayout(wave_grid)
        layout.addWidget(wave_section)

        run_section, run_layout = self._section("3. Acquire")
        run_controls = QHBoxLayout()
        timeout_group = QVBoxLayout()
        timeout_group.addWidget(QLabel("Timeout"))
        self.timeout_input = self._blank_double(86_400, " s")
        timeout_group.addWidget(self.timeout_input)
        run_controls.addLayout(timeout_group)
        run_controls.addStretch()
        self.export_button = QPushButton("Export CSV")
        self.export_button.setObjectName("secondaryButton")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_csv)
        self.run_button = QPushButton("Arm & pulse")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.run_acquisition)
        run_controls.addWidget(self.export_button, 0, Qt.AlignmentFlag.AlignBottom)
        run_controls.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignBottom)
        run_layout.addLayout(run_controls)

        summary = QHBoxLayout()
        self.summary_samples = self._summary_item(summary, "Samples")
        self.summary_rate = self._summary_item(summary, "Sample rate")
        self.summary_duration = self._summary_item(summary, "Duration")
        summary.addStretch()
        run_layout.addLayout(summary)

        pg.setConfigOptions(antialias=True)
        self.signal_plot = pg.PlotWidget()
        self.signal_plot.setMinimumHeight(320)
        self.signal_plot.setBackground("#ffffff")
        self.signal_plot.showGrid(x=True, y=True, alpha=0.18)
        self.signal_plot.setLabel("bottom", "Time", units="s")
        self.signal_plot.setLabel("left", "Voltage", units="V")
        self.signal_plot.setMouseEnabled(x=True, y=False)
        self.signal_plot.addLegend(offset=(12, 12))
        self._add_plot_header(run_layout, "Signals", self.signal_plot)
        run_layout.addWidget(self.signal_plot)
        layout.addWidget(run_section)
        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    def _add_plot_header(
        self,
        layout: QVBoxLayout,
        title: str,
        plot: pg.PlotWidget,
    ) -> None:
        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")

        pan_button = QPushButton()
        pan_button.setObjectName("secondaryButton")
        pan_button.setCheckable(True)
        pan_button.setChecked(True)
        pan_button.setIcon(self._plot_control_icon("pan"))
        pan_button.setToolTip("Pan")
        pan_button.setAccessibleName("Pan")

        box_zoom_button = QPushButton()
        box_zoom_button.setObjectName("secondaryButton")
        box_zoom_button.setCheckable(True)
        box_zoom_button.setIcon(self._plot_control_icon("box_zoom"))
        box_zoom_button.setToolTip("Box zoom")
        box_zoom_button.setAccessibleName("Box zoom")

        mouse_mode_group = QButtonGroup(plot)
        mouse_mode_group.setExclusive(True)
        mouse_mode_group.addButton(pan_button)
        mouse_mode_group.addButton(box_zoom_button)
        pan_button.clicked.connect(
            lambda _checked=False: self._set_box_zoom(plot, False)
        )
        box_zoom_button.clicked.connect(
            lambda _checked=False: self._set_box_zoom(plot, True)
        )

        fit_button = QPushButton()
        fit_button.setObjectName("secondaryButton")
        fit_button.setIcon(self._plot_control_icon("home"))
        fit_button.setToolTip("Reset view")
        fit_button.setAccessibleName("Reset view")
        fit_button.clicked.connect(
            lambda _checked=False: self._fit_plot(plot)
        )
        header.addWidget(title_label)
        header.addStretch()
        header.addWidget(pan_button)
        header.addWidget(box_zoom_button)
        header.addWidget(fit_button)
        layout.addLayout(header)

    def _plot_control_icon(self, icon_name: str) -> QIcon:
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#17212b"), 2))

        if icon_name == "pan":
            painter.drawLine(4, 10, 16, 10)
            painter.drawLine(10, 4, 10, 16)
            painter.drawLine(4, 10, 7, 7)
            painter.drawLine(4, 10, 7, 13)
            painter.drawLine(16, 10, 13, 7)
            painter.drawLine(16, 10, 13, 13)
            painter.drawLine(10, 4, 7, 7)
            painter.drawLine(10, 4, 13, 7)
            painter.drawLine(10, 16, 7, 13)
            painter.drawLine(10, 16, 13, 13)
        elif icon_name == "box_zoom":
            painter.drawRect(3, 3, 11, 11)
            painter.drawLine(13, 13, 17, 17)
        elif icon_name == "home":
            painter.drawLine(3, 9, 10, 3)
            painter.drawLine(10, 3, 17, 9)
            painter.drawLine(5, 8, 5, 17)
            painter.drawLine(5, 17, 15, 17)
            painter.drawLine(15, 17, 15, 8)
            painter.drawLine(9, 17, 9, 12)
            painter.drawLine(9, 12, 12, 12)
            painter.drawLine(12, 12, 12, 17)

        painter.end()
        return QIcon(pixmap)

    def _fit_plot(self, plot: pg.PlotWidget) -> None:
        plot.enableAutoRange(x=True, y=True)
        plot.autoRange()

    def _set_box_zoom(self, plot: pg.PlotWidget, enabled: bool) -> None:
        mode = pg.ViewBox.RectMode if enabled else pg.ViewBox.PanMode
        plot.getViewBox().setMouseMode(mode)

    def _summary_item(self, layout: QHBoxLayout, label: str) -> QLabel:
        group = QVBoxLayout()
        group.addWidget(QLabel(label))
        value = QLabel("--")
        value.setObjectName("summaryValue")
        group.addWidget(value)
        layout.addLayout(group)
        layout.addSpacing(36)
        return value

    def _build_analysis_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(18)
        layout.addWidget(self._page_heading("Analysis"))

        self.analysis_status = QLabel("Connect a device to begin.")
        self.analysis_status.setObjectName("statusText")
        self.analysis_status.setWordWrap(True)
        layout.addWidget(self.analysis_status)

        scope_section, scope_layout = self._section(
            "1. Configure analysis acquisition"
        )
        scope_grid = QGridLayout()
        scope_grid.setHorizontalSpacing(18)
        scope_grid.setVerticalSpacing(8)
        for column in range(3):
            scope_grid.setColumnStretch(column, 1)

        self.analysis_sample_rate_input = self._blank_double(
            1_000_000_000, " Hz"
        )
        self.analysis_measured_channel_input = self._placeholder_combo(
            "Select channel"
        )
        self.analysis_reference_channel_input = self._placeholder_combo(
            "Select channel"
        )
        self.analysis_measured_range_input = self._blank_double(1_000_000, " V")
        self.analysis_reference_range_input = self._blank_double(
            1_000_000, " V"
        )
        self.analysis_filter_input = self._placeholder_combo("Select filter")
        for label, value in (
            ("Decimate", "filterDecimate"),
            ("Average", "filterAverage"),
            ("Min / max", "filterMinMax"),
            ("Average fit", "filterAverageFit"),
        ):
            self.analysis_filter_input.addItem(label, value)

        self._add_field(
            scope_grid, 0, 0, "Sample rate", self.analysis_sample_rate_input
        )
        self._add_field(
            scope_grid,
            0,
            1,
            "Measured channel",
            self.analysis_measured_channel_input,
        )
        self._add_field(
            scope_grid,
            0,
            2,
            "Measured range",
            self.analysis_measured_range_input,
        )
        self._add_field(
            scope_grid,
            1,
            0,
            "Reference channel",
            self.analysis_reference_channel_input,
        )
        self._add_field(
            scope_grid,
            1,
            1,
            "Reference range",
            self.analysis_reference_range_input,
        )
        self._add_field(
            scope_grid, 1, 2, "Filter", self.analysis_filter_input
        )
        scope_layout.addLayout(scope_grid)
        fixed_mode = QLabel("Fixed acquisition: Record mode, 15,000 points")
        fixed_mode.setObjectName("mutedText")
        scope_layout.addWidget(fixed_mode)
        layout.addWidget(scope_section)

        pulse_section, pulse_layout = self._section("2. Configure pulse")
        pulse_grid = QGridLayout()
        pulse_grid.setHorizontalSpacing(18)
        for column in range(4):
            pulse_grid.setColumnStretch(column, 1)

        self.analysis_output_channel_input = self._placeholder_combo(
            "Select channel"
        )
        self.analysis_center_frequency_input = self._blank_double(
            1_000_000_000, " Hz"
        )
        self.analysis_cycle_count_input = self._blank_integer(
            1_000_000, " cycles"
        )
        self.analysis_amplitude_input = self._blank_double(1_000_000, " V")
        self.analysis_repetitions_input = QSpinBox()
        self.analysis_repetitions_input.setRange(1, 50)
        self.analysis_repetitions_input.setValue(1)
        self.analysis_repetitions_input.setSuffix(" acquisitions")
        self.analysis_calibration_repetitions_input = QSpinBox()
        self.analysis_calibration_repetitions_input.setRange(30, 500)
        self.analysis_calibration_repetitions_input.setValue(30)
        self.analysis_calibration_repetitions_input.setSuffix(" acquisitions")
        self.analysis_timeout_input = self._blank_double(86_400, " s")

        self._add_field(
            pulse_grid, 0, 0, "Output channel", self.analysis_output_channel_input
        )
        self._add_field(
            pulse_grid,
            0,
            1,
            "Center frequency",
            self.analysis_center_frequency_input,
        )
        self._add_field(
            pulse_grid, 0, 2, "Cycle count", self.analysis_cycle_count_input
        )
        self._add_field(
            pulse_grid, 0, 3, "Amplitude", self.analysis_amplitude_input
        )
        self._add_field(
            pulse_grid, 1, 0, "Specimen acquisitions", self.analysis_repetitions_input
        )
        self._add_field(
            pulse_grid, 1, 1, "Timeout per acquisition", self.analysis_timeout_input
        )
        self._add_field(
            pulse_grid,
            1,
            2,
            "Calibration acquisitions",
            self.analysis_calibration_repetitions_input,
        )
        pulse_layout.addLayout(pulse_grid)
        layout.addWidget(pulse_section)

        specimen_section, specimen_layout = self._section(
            "3. Configure specimen"
        )
        specimen_grid = QGridLayout()
        specimen_grid.setHorizontalSpacing(18)
        specimen_grid.setVerticalSpacing(8)
        specimen_grid.setColumnStretch(0, 2)
        specimen_grid.setColumnStretch(1, 2)
        specimen_grid.setColumnStretch(2, 2)
        specimen_grid.setColumnStretch(3, 1)
        for column, heading in enumerate(("", "Avg Value", "Std", "Unit")):
            label = QLabel(heading)
            if heading:
                label.setObjectName("mutedText")
            specimen_grid.addWidget(label, 0, column)

        self.analysis_specimen_length_input = self._blank_double(1_000_000)
        self.analysis_specimen_length_input.setDecimals(6)
        self.analysis_specimen_length_std_input = self._blank_double(1_000_000)
        self.analysis_specimen_length_std_input.setDecimals(6)
        self.analysis_length_unit_input = QComboBox()
        self.analysis_length_unit_input.addItem("mm", 0.001)
        self.analysis_length_unit_input.addItem("cm", 0.01)
        self.analysis_length_unit_input.addItem("m", 1.0)

        self.analysis_specimen_density_input = self._blank_double(1_000_000)
        self.analysis_specimen_density_input.setDecimals(6)
        self.analysis_specimen_density_std_input = self._blank_double(1_000_000)
        self.analysis_specimen_density_std_input.setDecimals(6)
        self.analysis_density_unit_input = QComboBox()
        self.analysis_density_unit_input.addItem("kg/m³", 1.0)
        self.analysis_density_unit_input.addItem("g/cm³", 1_000.0)

        specimen_grid.addWidget(QLabel("Length"), 1, 0)
        specimen_grid.addWidget(self.analysis_specimen_length_input, 1, 1)
        specimen_grid.addWidget(self.analysis_specimen_length_std_input, 1, 2)
        specimen_grid.addWidget(self.analysis_length_unit_input, 1, 3)
        specimen_grid.addWidget(QLabel("Density"), 2, 0)
        specimen_grid.addWidget(self.analysis_specimen_density_input, 2, 1)
        specimen_grid.addWidget(self.analysis_specimen_density_std_input, 2, 2)
        specimen_grid.addWidget(self.analysis_density_unit_input, 2, 3)
        specimen_layout.addLayout(specimen_grid)
        layout.addWidget(specimen_section)

        parameters_section, parameters_layout = self._section(
            "4. Configure signal analysis"
        )
        parameters_grid = QGridLayout()
        parameters_grid.setHorizontalSpacing(18)
        for column in range(3):
            parameters_grid.setColumnStretch(column, 1)
        self.analysis_cutoff_percentage_input = QDoubleSpinBox()
        self.analysis_cutoff_percentage_input.setRange(0.001, 100)
        self.analysis_cutoff_percentage_input.setDecimals(3)
        self.analysis_cutoff_percentage_input.setValue(5)
        self.analysis_cutoff_percentage_input.setSuffix(" %")
        self.analysis_frequency_cutoff_expander_input = QSpinBox()
        self.analysis_frequency_cutoff_expander_input.setRange(
            0, ANALYSIS_SAMPLE_COUNT - 1
        )
        self.analysis_frequency_cutoff_expander_input.setValue(10)
        self.analysis_frequency_cutoff_expander_input.setSuffix(" bins")
        self.analysis_time_cutoff_expander_input = QSpinBox()
        self.analysis_time_cutoff_expander_input.setRange(
            0, ANALYSIS_SAMPLE_COUNT - 1
        )
        self.analysis_time_cutoff_expander_input.setValue(10)
        self.analysis_time_cutoff_expander_input.setSuffix(" samples")
        self.analysis_min_frequency_input = self._blank_double(
            1_000_000_000, " Hz"
        )
        self._add_field(
            parameters_grid,
            0,
            0,
            "Cutoff percentage",
            self.analysis_cutoff_percentage_input,
        )
        self._add_field(
            parameters_grid,
            0,
            1,
            "Frequency cutoff expander",
            self.analysis_frequency_cutoff_expander_input,
        )
        self._add_field(
            parameters_grid,
            0,
            2,
            "Minimum cutoff frequency",
            self.analysis_min_frequency_input,
        )
        self._add_field(
            parameters_grid,
            1,
            0,
            "Time cutoff expander",
            self.analysis_time_cutoff_expander_input,
        )
        parameters_layout.addLayout(parameters_grid)

        gain_grid = QGridLayout()
        gain_grid.setHorizontalSpacing(18)
        gain_grid.setVerticalSpacing(8)
        gain_grid.setColumnStretch(0, 2)
        gain_grid.setColumnStretch(1, 2)
        gain_grid.setColumnStretch(2, 2)
        gain_grid.setColumnStretch(3, 1)
        for column, heading in enumerate(("", "Avg Value", "Std", "Unit")):
            label = QLabel(heading)
            if heading:
                label.setObjectName("mutedText")
            gain_grid.addWidget(label, 0, column)

        self.analysis_reference_gain_input = ScientificDoubleSpinBox()
        self.analysis_reference_gain_input.setRange(-1_000_000, 1_000_000)
        self.analysis_reference_gain_input.setDecimals(6)
        self.analysis_reference_gain_std_input = self._blank_double(1_000_000)
        self.analysis_reference_gain_std_input.setDecimals(6)
        self.analysis_measured_gain_input = ScientificDoubleSpinBox()
        self.analysis_measured_gain_input.setRange(-1_000_000, 1_000_000)
        self.analysis_measured_gain_input.setDecimals(6)
        self.analysis_measured_gain_std_input = self._blank_double(1_000_000)
        self.analysis_measured_gain_std_input.setDecimals(6)
        gain_grid.addWidget(QLabel("Reference gain"), 1, 0)
        gain_grid.addWidget(self.analysis_reference_gain_input, 1, 1)
        gain_grid.addWidget(self.analysis_reference_gain_std_input, 1, 2)
        gain_grid.addWidget(QLabel("dB"), 1, 3)
        gain_grid.addWidget(QLabel("Measured gain"), 2, 0)
        gain_grid.addWidget(self.analysis_measured_gain_input, 2, 1)
        gain_grid.addWidget(self.analysis_measured_gain_std_input, 2, 2)
        gain_grid.addWidget(QLabel("dB"), 2, 3)
        parameters_layout.addLayout(gain_grid)
        layout.addWidget(parameters_section)

        results_section, results_layout = self._section("5. Acquire and analyze")
        actions = QHBoxLayout()
        self.analysis_selection_input = QComboBox()
        self.analysis_selection_input.setEnabled(False)
        self.analysis_selection_input.currentIndexChanged.connect(
            self._update_analysis_annotation
        )
        actions.addWidget(QLabel("Annotated acquisition"))
        actions.addWidget(self.analysis_selection_input)
        actions.addStretch()
        self.analysis_export_button = QPushButton("Export CSV")
        self.analysis_export_button.setObjectName("secondaryButton")
        self.analysis_export_button.setEnabled(False)
        self.analysis_export_button.clicked.connect(self.export_analysis_csv)
        self.analysis_run_button = QPushButton("Run analysis")
        self.analysis_run_button.setEnabled(False)
        self.analysis_run_button.clicked.connect(self.run_analysis)
        actions.addWidget(self.analysis_export_button)
        actions.addWidget(self.analysis_run_button)
        results_layout.addLayout(actions)

        summary = QHBoxLayout()
        self.analysis_calibration_delay = self._summary_item(
            summary, "System delay"
        )
        self.analysis_calibration_transmission = self._summary_item(
            summary, "Setup transmission loss"
        )
        self.analysis_summary_tof = self._summary_item(
            summary, "Mean adjusted ToF"
        )
        self.analysis_summary_velocity = self._summary_item(summary, "Velocity")
        self.analysis_summary_attenuation = self._summary_item(
            summary, "Attenuation"
        )
        self.analysis_summary_snr_before = self._summary_item(
            summary, "Mean SNR before"
        )
        self.analysis_summary_snr_after = self._summary_item(
            summary, "Mean SNR after"
        )
        summary.addStretch()
        results_layout.addLayout(summary)

        pg.setConfigOptions(antialias=True)
        self.analysis_signal_plot = pg.PlotWidget()
        self.analysis_signal_plot.setMinimumHeight(360)
        self.analysis_signal_plot.setBackground("#ffffff")
        self.analysis_signal_plot.showGrid(x=True, y=True, alpha=0.18)
        self.analysis_signal_plot.setLabel("bottom", "Concatenated time", units="s")
        self.analysis_signal_plot.setLabel("left", "Voltage", units="V")
        self.analysis_signal_plot.addLegend(offset=(12, 12))
        self._add_plot_header(
            results_layout, "Filtered time-domain signals", self.analysis_signal_plot
        )
        results_layout.addWidget(self.analysis_signal_plot)

        calibration_heading = QLabel("Calibration ToF results")
        calibration_heading.setObjectName("sectionTitle")
        results_layout.addWidget(calibration_heading)
        self.analysis_calibration_table = QTableWidget(0, 3)
        self.analysis_calibration_table.setHorizontalHeaderLabels(
            ["Acquisition", "Raw ToF (s)", "Transmission loss (dB)"]
        )
        self.analysis_calibration_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.analysis_calibration_table.verticalHeader().setVisible(False)
        self.analysis_calibration_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        results_layout.addWidget(self.analysis_calibration_table)

        specimen_heading = QLabel("Specimen results")
        specimen_heading.setObjectName("sectionTitle")
        results_layout.addWidget(specimen_heading)
        self.analysis_results_table = QTableWidget(0, 6)
        self.analysis_results_table.setHorizontalHeaderLabels(
            [
                "Acquisition",
                "Raw ToF (s)",
                "Adjusted ToF (s)",
                "Attenuation (dB)",
                "SNR before (dB)",
                "SNR after (dB)",
            ]
        )
        self.analysis_results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.analysis_results_table.verticalHeader().setVisible(False)
        self.analysis_results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.analysis_results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        results_layout.addWidget(self.analysis_results_table)
        layout.addWidget(results_section)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addWidget(self._page_heading("Settings"))
        section, section_layout = self._section("Application")
        section_layout.addWidget(QLabel("No application settings configured."))
        layout.addWidget(section)
        layout.addStretch()
        return page

    def _populate_channel_controls(self) -> None:
        if self.api is None:
            return
        input_count, output_count = self.api.get_channel_counts()
        for control, count in (
            (self.measured_channel_input, input_count),
            (self.reference_channel_input, input_count),
            (self.output_channel_input, output_count),
            (self.analysis_measured_channel_input, input_count),
            (self.analysis_reference_channel_input, input_count),
            (self.analysis_output_channel_input, output_count),
        ):
            control.clear()
            control.addItem("Select channel", None)
            for channel in range(count):
                control.addItem(f"Channel {channel + 1}", channel)
        self.configure_button.setEnabled(True)
        self.analysis_run_button.setEnabled(True)

    def _required_combo(
        self, control: QComboBox, label: str, errors: list[str]
    ) -> str | int | float | None:
        value = control.currentData()
        if value is None:
            errors.append(label)
        return value

    def _show_missing(self, fields: list[str]) -> None:
        QMessageBox.warning(
            self,
            "Incomplete form",
            "Enter or select: " + ", ".join(fields) + ".",
        )

    def configure_oscilloscope(self) -> None:
        if self.api is None:
            return
        api = self.api
        errors: list[str] = []
        mode = self._required_combo(self.mode_input, "mode", errors)
        filtering = self._required_combo(self.filter_input, "filter", errors)
        measured_channel = self._required_combo(
            self.measured_channel_input, "measured channel", errors
        )
        reference_channel = self._required_combo(
            self.reference_channel_input, "reference channel", errors
        )
        if self.sample_rate_input.value() <= 0:
            errors.append("sample rate")
        if self.sample_count_input.value() <= 0:
            errors.append("sample count")
        if self.measured_range_input.value() <= 0:
            errors.append("measured range")
        if self.reference_range_input.value() <= 0:
            errors.append("reference range")
        if not isinstance(mode, str) or mode not in ("read", "record"):
            errors.append("valid mode")
        if not isinstance(filtering, str) or filtering not in (
            "filterDecimate",
            "filterAverage",
            "filterMinMax",
            "filterAverageFit",
        ):
            errors.append("valid filter")
        if not isinstance(measured_channel, int):
            errors.append("valid measured channel")
        if not isinstance(reference_channel, int):
            errors.append("valid reference channel")
        if errors:
            self._show_missing(errors)
            return

        valid_mode = "read" if mode == "read" else "record"
        if filtering == "filterDecimate":
            valid_filtering = "filterDecimate"
        elif filtering == "filterAverage":
            valid_filtering = "filterAverage"
        elif filtering == "filterMinMax":
            valid_filtering = "filterMinMax"
        else:
            valid_filtering = "filterAverageFit"
        if not isinstance(measured_channel, int) or not isinstance(
            reference_channel, int
        ):
            return

        operation = lambda: api.configure_oscilloscope(
            mode=valid_mode,
            sample_rate=self.sample_rate_input.value(),
            measured_voltage_range=self.measured_range_input.value(),
            reference_voltage_range=self.reference_range_input.value(),
            filtering_scheme=valid_filtering,
            number_of_samples=self.sample_count_input.value(),
            measured_channel=measured_channel,
            reference_channel=reference_channel,
        )
        self._start_hardware_operation(
            operation,
            self._configuration_finished,
            "Applying oscilloscope configuration...",
        )

    def _configuration_finished(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        self.status.setText(str(result.get("message") or "Configuration complete."))
        self.oscilloscope_configured = bool(result.get("ready"))
        self.dashboard_acquisition.setText(
            "Configured" if self.oscilloscope_configured else "Configuration failed"
        )
        self._set_busy(False)

    def run_acquisition(self) -> None:
        if self.api is None or not self.oscilloscope_configured:
            return
        api = self.api
        errors: list[str] = []
        output_channel = self._required_combo(
            self.output_channel_input, "output channel", errors
        )
        if output_channel is not None and not isinstance(output_channel, int):
            errors.append("valid output channel")
        if self.center_frequency_input.value() <= 0:
            errors.append("center frequency")
        if self.cycle_count_input.value() <= 0:
            errors.append("cycle count")
        if self.amplitude_input.value() <= 0:
            errors.append("amplitude")
        if errors:
            self._show_missing(errors)
            return
        if not isinstance(output_channel, int):
            return

        timeout = self.timeout_input.value() or None
        center_frequency = self.center_frequency_input.value()
        cycle_count = self.cycle_count_input.value()
        amplitude = self.amplitude_input.value()

        def operation():
            _, voltage, waveform_frequency = generate_pulse(
                center_frequency,
                cycle_count,
            )
            preparation_status = api.prepare_wave(
                out_channel=output_channel,
                voltage=voltage.tolist(),
                waveform_frequency=waveform_frequency,
                voltage_amplitude=amplitude,
            )
            if not preparation_status["ready"]:
                raise RuntimeError(
                    preparation_status["message"] or "Pulse preparation failed"
                )

            def trigger_pulse() -> None:
                wave_status = api.generate_wave()
                if not wave_status["ready"]:
                    raise RuntimeError(wave_status["message"] or "Pulse generation failed")

            return api.read_oscilloscope(
                timeout_seconds=timeout,
                on_acquisition_started=trigger_pulse,
                trigger_output_channel=(
                    output_channel if api.oscilloscope_mode == "record" else None
                ),
            )

        self._start_hardware_operation(
            operation,
            self._acquisition_finished,
            "Arming acquisition and generating pulse...",
        )

    def run_analysis(self) -> None:
        if self.api is None:
            return
        api = self.api
        errors: list[str] = []
        filtering = self._required_combo(
            self.analysis_filter_input, "filter", errors
        )
        measured_channel = self._required_combo(
            self.analysis_measured_channel_input, "measured channel", errors
        )
        reference_channel = self._required_combo(
            self.analysis_reference_channel_input, "reference channel", errors
        )
        output_channel = self._required_combo(
            self.analysis_output_channel_input, "output channel", errors
        )
        if measured_channel == reference_channel and measured_channel is not None:
            errors.append("different measured and reference channels")
        for control, label in (
            (self.analysis_sample_rate_input, "sample rate"),
            (self.analysis_measured_range_input, "measured range"),
            (self.analysis_reference_range_input, "reference range"),
            (self.analysis_center_frequency_input, "center frequency"),
            (self.analysis_cycle_count_input, "cycle count"),
            (self.analysis_amplitude_input, "amplitude"),
            (self.analysis_specimen_length_input, "specimen length"),
            (self.analysis_specimen_density_input, "specimen density"),
        ):
            if control.value() <= 0:
                errors.append(label)
        if not isinstance(filtering, str) or filtering not in (
            "filterDecimate",
            "filterAverage",
            "filterMinMax",
            "filterAverageFit",
        ):
            errors.append("valid filter")
        if not isinstance(measured_channel, int):
            errors.append("valid measured channel")
        if not isinstance(reference_channel, int):
            errors.append("valid reference channel")
        if not isinstance(output_channel, int):
            errors.append("valid output channel")
        if errors:
            self._show_missing(errors)
            return

        if filtering == "filterDecimate":
            valid_filtering = "filterDecimate"
        elif filtering == "filterAverage":
            valid_filtering = "filterAverage"
        elif filtering == "filterMinMax":
            valid_filtering = "filterMinMax"
        else:
            valid_filtering = "filterAverageFit"
        if (
            not isinstance(measured_channel, int)
            or not isinstance(reference_channel, int)
            or not isinstance(output_channel, int)
        ):
            return

        length_factor = self.analysis_length_unit_input.currentData()
        density_factor = self.analysis_density_unit_input.currentData()
        if not isinstance(length_factor, (int, float)):
            QMessageBox.critical(
                self, "Invalid length unit", "Select a valid specimen length unit."
            )
            return
        if not isinstance(density_factor, (int, float)):
            QMessageBox.critical(
                self, "Invalid density unit", "Select a valid specimen density unit."
            )
            return

        sample_rate = self.analysis_sample_rate_input.value()
        measured_voltage_range = self.analysis_measured_range_input.value()
        reference_voltage_range = self.analysis_reference_range_input.value()
        timeout = self.analysis_timeout_input.value() or None
        specimen_length = self.analysis_specimen_length_input.value() * length_factor
        specimen_length_std = (
            self.analysis_specimen_length_std_input.value() * length_factor
        )
        specimen_density = (
            self.analysis_specimen_density_input.value() * density_factor
        )
        specimen_density_std = (
            self.analysis_specimen_density_std_input.value() * density_factor
        )
        reference_gain = self.analysis_reference_gain_input.value()
        reference_gain_std = self.analysis_reference_gain_std_input.value()
        measured_gain = self.analysis_measured_gain_input.value()
        measured_gain_std = self.analysis_measured_gain_std_input.value()
        repetitions = self.analysis_repetitions_input.value()
        calibration_repetitions = (
            self.analysis_calibration_repetitions_input.value()
        )
        cutoff_percentage = self.analysis_cutoff_percentage_input.value() / 100
        frequency_cutoff_expander = (
            self.analysis_frequency_cutoff_expander_input.value()
        )
        time_cutoff_expander = self.analysis_time_cutoff_expander_input.value()
        min_cutoff_frequency = self.analysis_min_frequency_input.value()
        center_frequency = self.analysis_center_frequency_input.value()
        cycle_count = self.analysis_cycle_count_input.value()
        amplitude = self.analysis_amplitude_input.value()

        self.analysis_results = []
        self.calibration_results = []
        self.calibration_delay_s = None
        self.calibration_transmission_loss_db = None
        self.analysis_summary = None
        self.analysis_specimen_length_m = specimen_length
        self.analysis_metadata = {
            "specimen_length_m": specimen_length,
            "specimen_length_std_m": specimen_length_std,
            "specimen_density_kg_per_m3": specimen_density,
            "specimen_density_std_kg_per_m3": specimen_density_std,
            "reference_gain_db": reference_gain,
            "reference_gain_std_db": reference_gain_std,
            "measured_gain_db": measured_gain,
            "measured_gain_std_db": measured_gain_std,
        }
        for label in (
            self.analysis_calibration_delay,
            self.analysis_calibration_transmission,
            self.analysis_summary_tof,
            self.analysis_summary_velocity,
            self.analysis_summary_attenuation,
            self.analysis_summary_snr_before,
            self.analysis_summary_snr_after,
        ):
            label.setText("--")
        self.analysis_calibration_table.setRowCount(0)
        self.analysis_results_table.setRowCount(0)
        self.analysis_selection_input.clear()
        self.analysis_selection_input.setEnabled(False)
        self.analysis_export_button.setEnabled(False)
        self.analysis_signal_plot.clear()

        response = QMessageBox.question(
            self,
            "Calibration required",
            (
                "Remove the specimen from between the transducers, then select "
                "OK to begin the calibration acquisitions."
            ),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if response != QMessageBox.StandardButton.Ok:
            self.analysis_status.setText("Analysis cancelled before calibration.")
            return

        valid_measured_channel = measured_channel
        valid_reference_channel = reference_channel
        valid_output_channel = output_channel

        def prepare_hardware() -> None:
            configuration = api.configure_oscilloscope(
                mode="record",
                sample_rate=sample_rate,
                measured_voltage_range=measured_voltage_range,
                reference_voltage_range=reference_voltage_range,
                filtering_scheme=valid_filtering,
                number_of_samples=ANALYSIS_SAMPLE_COUNT,
                measured_channel=valid_measured_channel,
                reference_channel=valid_reference_channel,
            )
            if not configuration["ready"]:
                raise RuntimeError(
                    configuration["message"] or "Oscilloscope configuration failed"
                )
            _, voltage, waveform_frequency = generate_pulse(
                center_frequency, cycle_count
            )
            preparation = api.prepare_wave(
                out_channel=valid_output_channel,
                voltage=voltage.tolist(),
                waveform_frequency=waveform_frequency,
                voltage_amplitude=amplitude,
            )
            if not preparation["ready"]:
                raise RuntimeError(
                    preparation["message"] or "Pulse preparation failed"
                )

        time_values = np.arange(ANALYSIS_SAMPLE_COUNT, dtype=float) / sample_rate

        def acquire_once(acquisition: int, apply_gain: bool) -> tuple[
            float,
            float,
            tuple[float, float],
            tuple[float, float],
            np.ndarray,
            np.ndarray,
            np.ndarray,
            float,
            float,
        ]:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    def trigger_pulse() -> None:
                        wave_status = api.generate_wave()
                        if not wave_status["ready"]:
                            raise RuntimeError(
                                wave_status["message"]
                                or "Pulse generation failed"
                            )

                    acquisition_status = api.read_oscilloscope(
                        timeout_seconds=timeout,
                        on_acquisition_started=trigger_pulse,
                        trigger_output_channel=valid_output_channel,
                    )
                    if not acquisition_status["ready"]:
                        raise RuntimeError(
                            acquisition_status["message"]
                            or "Oscilloscope acquisition failed"
                        )
                    if (
                        api.measuredSamples is None
                        or api.referenceSamples is None
                    ):
                        raise RuntimeError(
                            "Acquisition completed without sample data"
                        )
                    measured = np.ctypeslib.as_array(api.measuredSamples).copy()
                    reference = np.ctypeslib.as_array(api.referenceSamples).copy()
                    return review_ultrasonic_signals(
                        time_values,
                        reference,
                        measured,
                        cutoff_percentage=cutoff_percentage,
                        frequency_cutoff_expander=frequency_cutoff_expander,
                        time_cutoff_expander=time_cutoff_expander,
                        min_cutoff_frequency=min_cutoff_frequency,
                        ref_db_gain=reference_gain,
                        measured_db_gain=measured_gain,
                        apply_gain=apply_gain,
                    )
                except Exception as error:
                    last_error = error
                    if attempt == 0:
                        continue
            raise RuntimeError(
                f"Acquisition {acquisition} failed after one retry: {last_error}"
            ) from last_error

        def calibration_operation() -> list[CalibrationResult]:
            prepare_hardware()
            results: list[CalibrationResult] = []
            for acquisition in range(1, calibration_repetitions + 1):
                analysis = acquire_once(acquisition, apply_gain=True)
                results.append(
                    CalibrationResult(
                        acquisition=acquisition,
                        time_of_flight=analysis[0],
                        transmission_loss=analysis[1],
                    )
                )
            return results

        def specimen_operation() -> list[AnalysisResult]:
            if self.calibration_delay_s is None:
                raise RuntimeError("System delay has not been calibrated")
            results: list[AnalysisResult] = []
            for acquisition in range(1, repetitions + 1):
                analysis = acquire_once(acquisition, apply_gain=False)
                adjusted_time_of_flight = analysis[0] - self.calibration_delay_s
                if adjusted_time_of_flight <= 0:
                    raise RuntimeError(
                        f"Acquisition {acquisition} produced a non-positive "
                        "adjusted time of flight"
                    )
                results.append(
                    AnalysisResult(
                        acquisition=acquisition,
                        raw_time_of_flight=analysis[0],
                        adjusted_time_of_flight=adjusted_time_of_flight,
                        attenuation=analysis[1],
                        reference_peak=analysis[2],
                        measured_peak=analysis[3],
                        time=analysis[4],
                        reference=analysis[5],
                        measured=analysis[6],
                        snr_before=analysis[7],
                        snr_after=analysis[8],
                    )
                )
            return results

        self.pending_specimen_operation = specimen_operation
        self._start_hardware_operation(
            calibration_operation,
            self._calibration_finished,
            f"Running {calibration_repetitions} calibration acquisition(s)...",
            self.analysis_status,
        )

    def _calibration_finished(self, result: object) -> None:
        if not isinstance(result, list) or not all(
            isinstance(item, CalibrationResult) for item in result
        ):
            self.analysis_status.setText("Calibration returned an invalid result.")
            self.pending_specimen_operation = None
            self._set_busy(False)
            return
        time_of_flights = np.asarray(
            [item.time_of_flight for item in result], dtype=float
        )
        first_quartile, third_quartile = np.percentile(
            time_of_flights, [25, 75]
        )
        interquartile_range = third_quartile - first_quartile
        lower_fence = first_quartile - 1.5 * interquartile_range
        upper_fence = third_quartile + 1.5 * interquartile_range
        retained_mask = (
            (time_of_flights >= lower_fence)
            & (time_of_flights <= upper_fence)
        )
        retained_values = time_of_flights[retained_mask]
        if retained_values.size == 0:
            self.analysis_status.setText(
                "Calibration rejected every time-of-flight measurement."
            )
            self.pending_specimen_operation = None
            self._set_busy(False)
            return

        transmission_losses = np.asarray(
            [abs(item.transmission_loss) for item in result], dtype=float
        )
        finite_transmission_mask = np.isfinite(transmission_losses)
        finite_transmission_losses = transmission_losses[
            finite_transmission_mask
        ]
        if finite_transmission_losses.size == 0:
            self.analysis_status.setText(
                "Calibration produced no finite transmission-loss measurements."
            )
            self.pending_specimen_operation = None
            self._set_busy(False)
            return
        transmission_q1, transmission_q3 = np.percentile(
            finite_transmission_losses, [25, 75]
        )
        transmission_iqr = transmission_q3 - transmission_q1
        transmission_lower = transmission_q1 - 1.5 * transmission_iqr
        transmission_upper = transmission_q3 + 1.5 * transmission_iqr
        transmission_retained_mask = (
            finite_transmission_mask
            & (transmission_losses >= transmission_lower)
            & (transmission_losses <= transmission_upper)
        )
        retained_transmission_losses = transmission_losses[
            transmission_retained_mask
        ]
        if retained_transmission_losses.size == 0:
            self.analysis_status.setText(
                "Calibration rejected every transmission-loss measurement."
            )
            self.pending_specimen_operation = None
            self._set_busy(False)
            return

        for calibration, retained, transmission_retained in zip(
            result, retained_mask, transmission_retained_mask
        ):
            calibration.retained = bool(retained)
            calibration.transmission_retained = bool(transmission_retained)
        self.calibration_results = result
        self.calibration_delay_s = float(np.mean(retained_values))
        self.calibration_transmission_loss_db = float(
            np.mean(retained_transmission_losses)
        )
        self.analysis_calibration_delay.setText(
            f"{self.calibration_delay_s:.9g} s"
        )
        self.analysis_calibration_transmission.setText(
            f"{self.calibration_transmission_loss_db:.9g} dB"
        )
        self.analysis_calibration_table.setRowCount(len(result))
        for row, calibration in enumerate(result):
            values = (
                str(calibration.acquisition),
                f"{calibration.time_of_flight:.9g}",
                f"{abs(calibration.transmission_loss):.9g}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setForeground(
                        QColor("#1b7f3a" if calibration.retained else "#b42318")
                    )
                elif column == 2:
                    item.setForeground(
                        QColor(
                            "#1b7f3a"
                            if calibration.transmission_retained
                            else "#b42318"
                        )
                    )
                self.analysis_calibration_table.setItem(row, column, item)
        self.analysis_status.setText(
            f"System delay: {self.calibration_delay_s:.9g} s; "
            f"setup transmission loss: "
            f"{self.calibration_transmission_loss_db:.9g} dB."
        )
        self.post_operation_action = self._prompt_for_specimen_stage

    def _prompt_for_specimen_stage(self) -> None:
        operation = self.pending_specimen_operation
        if (
            operation is None
            or self.calibration_delay_s is None
            or self.calibration_transmission_loss_db is None
        ):
            self.analysis_status.setText(
                "Specimen measurement cannot start without calibration."
            )
            self._set_busy(False)
            return
        response = QMessageBox.question(
            self,
            "Calibration complete",
            (
                f"The measured system delay is "
                f"{self.calibration_delay_s:.9g} s and the setup transmission "
                f"loss is {self.calibration_transmission_loss_db:.9g} dB.\n\n"
                "Place the specimen between the transducers, then select OK "
                "to begin the specimen acquisitions."
            ),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if response != QMessageBox.StandardButton.Ok:
            self.pending_specimen_operation = None
            self.analysis_status.setText(
                "Calibration complete; specimen measurement cancelled."
            )
            self._set_busy(False)
            return
        self.pending_specimen_operation = None
        self._start_hardware_operation(
            operation,
            self._analysis_finished,
            "Running specimen analysis acquisitions...",
            self.analysis_status,
        )

    def _analysis_finished(self, result: object) -> None:
        if not isinstance(result, list) or not all(
            isinstance(item, AnalysisResult) for item in result
        ):
            self.analysis_status.setText("Analysis returned an invalid result.")
            self._set_busy(False)
            return
        specimen_length = self.analysis_specimen_length_m
        transmission_loss = self.calibration_transmission_loss_db
        if (
            specimen_length is None
            or specimen_length <= 0
            or transmission_loss is None
        ):
            self.analysis_status.setText(
                "Analysis completed without a valid specimen length."
            )
            self._set_busy(False)
            return

        self.analysis_results = result
        adjusted_time_of_flights = np.asarray(
            [item.adjusted_time_of_flight for item in result], dtype=float
        )
        mean_time_of_flight = float(np.mean(adjusted_time_of_flights))
        mean_raw_attenuation = float(
            np.mean([item.attenuation for item in result])
        )
        measured_gain = self.analysis_metadata["measured_gain_db"]
        reference_gain = self.analysis_metadata["reference_gain_db"]
        self.analysis_summary = AnalysisSummary(
            mean_time_of_flight=mean_time_of_flight,
            velocity=specimen_length / mean_time_of_flight,
            attenuation=(
                mean_raw_attenuation
                - transmission_loss
                - measured_gain
                + reference_gain
            )
            / specimen_length,
            mean_snr_before=float(
                np.mean([item.snr_before for item in result])
            ),
            mean_snr_after=float(
                np.mean([item.snr_after for item in result])
            ),
        )
        self._render_analysis_results()
        self.analysis_status.setText(
            f"Analysis complete: {len(result)} acquisition(s) processed."
        )
        self.dashboard_acquisition.setText("Analysis complete")
        self._set_busy(False)

    def _render_analysis_results(self) -> None:
        self.analysis_signal_plot.clear()
        self.analysis_signal_plot.addLegend(offset=(12, 12))
        self.analysis_results_table.setRowCount(len(self.analysis_results))
        self.analysis_selection_input.blockSignals(True)
        self.analysis_selection_input.clear()
        plot_times = self._concatenated_analysis_times()
        self.analysis_segment_starts = [
            float(plot_time[0]) for plot_time in plot_times
        ]

        if plot_times:
            concatenated_time = np.concatenate(plot_times)
            self.analysis_signal_plot.plot(
                concatenated_time,
                np.concatenate(
                    [result.measured for result in self.analysis_results]
                ),
                pen=pg.mkPen("#087e8b", width=1.5),
                name="Measured",
                downsample=20,
                autoDownsample=False,
                downsampleMethod="peak",
            )
            self.analysis_signal_plot.plot(
                concatenated_time,
                np.concatenate(
                    [result.reference for result in self.analysis_results]
                ),
                pen=pg.mkPen("#d1495b", width=1.5),
                name="Reference",
                downsample=20,
                autoDownsample=False,
                downsampleMethod="peak",
            )

        for row, result in enumerate(self.analysis_results):
            values = (
                str(result.acquisition),
                f"{result.raw_time_of_flight:.9g}",
                f"{result.adjusted_time_of_flight:.9g}",
                f"{result.attenuation:.9g}",
                f"{result.snr_before:.9g}",
                f"{result.snr_after:.9g}",
            )
            for column, value in enumerate(values):
                self.analysis_results_table.setItem(
                    row, column, QTableWidgetItem(value)
                )
            self.analysis_selection_input.addItem(
                f"Acquisition {result.acquisition}", row
            )

        if self.analysis_summary is not None:
            self.analysis_summary_tof.setText(
                f"{self.analysis_summary.mean_time_of_flight:.9g} s"
            )
            self.analysis_summary_velocity.setText(
                f"{self.analysis_summary.velocity:.9g} m/s"
            )
            self.analysis_summary_attenuation.setText(
                f"{self.analysis_summary.attenuation:.9g} dB/m"
            )
            self.analysis_summary_snr_before.setText(
                f"{self.analysis_summary.mean_snr_before:.9g} dB"
            )
            self.analysis_summary_snr_after.setText(
                f"{self.analysis_summary.mean_snr_after:.9g} dB"
            )

        self.analysis_plot_items = []
        has_results = bool(self.analysis_results)
        self.analysis_selection_input.setEnabled(has_results)
        self.analysis_export_button.setEnabled(has_results)
        self.analysis_selection_input.blockSignals(False)
        if has_results:
            self.analysis_selection_input.setCurrentIndex(0)
            self._update_analysis_annotation()

    def _concatenated_analysis_times(self) -> list[np.ndarray]:
        plot_times: list[np.ndarray] = []
        next_start = 0.0
        fallback_interval = 1.0 / self.analysis_sample_rate_input.value()
        for result in self.analysis_results:
            local_time = result.time - result.time[0]
            plot_time = local_time + next_start
            plot_times.append(plot_time)
            if len(local_time) > 1:
                sample_interval = float(np.median(np.diff(local_time)))
            else:
                sample_interval = fallback_interval
            if not math.isfinite(sample_interval) or sample_interval <= 0:
                sample_interval = fallback_interval
            next_start = float(plot_time[-1]) + sample_interval
        return plot_times

    @Slot()
    def _update_analysis_annotation(self) -> None:
        for item in self.analysis_plot_items:
            self.analysis_signal_plot.removeItem(item)
        self.analysis_plot_items = []
        result_index = self.analysis_selection_input.currentData()
        if not isinstance(result_index, int) or not 0 <= result_index < len(
            self.analysis_results
        ):
            return
        if result_index >= len(self.analysis_segment_starts):
            return

        result = self.analysis_results[result_index]
        segment_start = self.analysis_segment_starts[result_index]
        time_start = result.time[0]
        reference_x = segment_start + result.reference_peak[0] - time_start
        measured_x = segment_start + result.measured_peak[0] - time_start
        reference_y = result.reference_peak[1]
        measured_y = result.measured_peak[1]
        segment_min = min(
            float(np.min(result.reference)), float(np.min(result.measured))
        )
        segment_max = max(
            float(np.max(result.reference)), float(np.max(result.measured))
        )
        arrow_y = segment_max + max(
            (segment_max - segment_min) * 0.12, 1e-12
        )

        peak_markers = pg.ScatterPlotItem(
            x=[reference_x, measured_x],
            y=[reference_y, measured_y],
            symbol="o",
            size=10,
            pen=pg.mkPen("#102a43", width=2),
            brush=pg.mkBrush("#ffffff"),
        )
        arrow_line = pg.PlotCurveItem(
            x=[reference_x, measured_x],
            y=[arrow_y, arrow_y],
            pen=pg.mkPen("#102a43", width=2),
        )
        arrow_head = pg.ArrowItem(
            pos=(measured_x, arrow_y),
            angle=180 if measured_x >= reference_x else 0,
            tipAngle=25,
            headLen=14,
            brush=pg.mkBrush("#102a43"),
            pen=pg.mkPen("#102a43"),
        )
        label = pg.TextItem(
            text=(
                f"Raw ToF: {result.raw_time_of_flight:.6g} s\n"
                f"Adjusted: {result.adjusted_time_of_flight:.6g} s"
            ),
            color="#102a43",
            anchor=(0.5, 1),
        )
        label.setPos((reference_x + measured_x) / 2, arrow_y)
        for item in (peak_markers, arrow_line, arrow_head, label):
            self.analysis_signal_plot.addItem(item)
            self.analysis_plot_items.append(item)

    def export_analysis_csv(self) -> None:
        if not self.analysis_results:
            return
        summary_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export analysis",
            "analysis_summary.csv",
            "CSV files (*.csv)",
        )
        if not summary_path:
            return
        summary_file = Path(summary_path)
        if summary_file.suffix.lower() != ".csv":
            summary_file = summary_file.with_suffix(".csv")
        waveform_file = summary_file.with_name(
            f"{summary_file.stem}_waveforms.csv"
        )

        with summary_file.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(
                [
                    "acquisition",
                    "raw_time_of_flight_s",
                    "adjusted_time_of_flight_s",
                    "attenuation_db",
                    "snr_before_db",
                    "snr_after_db",
                ]
            )
            for result in self.analysis_results:
                writer.writerow(
                    [
                        result.acquisition,
                        result.raw_time_of_flight,
                        result.adjusted_time_of_flight,
                        result.attenuation,
                        result.snr_before,
                        result.snr_after,
                    ]
                )
            writer.writerow([])
            writer.writerow(["aggregate", "value", "unit"])
            if self.calibration_delay_s is not None:
                writer.writerow(["system_delay", self.calibration_delay_s, "s"])
            if self.calibration_transmission_loss_db is not None:
                writer.writerow(
                    [
                        "setup_transmission_loss",
                        self.calibration_transmission_loss_db,
                        "dB",
                    ]
                )
            if self.analysis_summary is not None:
                writer.writerow(
                    [
                        "mean_adjusted_time_of_flight",
                        self.analysis_summary.mean_time_of_flight,
                        "s",
                    ]
                )
                writer.writerow(
                    ["velocity", self.analysis_summary.velocity, "m/s"]
                )
                writer.writerow(
                    ["attenuation", self.analysis_summary.attenuation, "dB/m"]
                )
                writer.writerow(
                    [
                        "mean_snr_before",
                        self.analysis_summary.mean_snr_before,
                        "dB",
                    ]
                )
                writer.writerow(
                    [
                        "mean_snr_after",
                        self.analysis_summary.mean_snr_after,
                        "dB",
                    ]
                )
            for key, value in self.analysis_metadata.items():
                writer.writerow([key, value, ""])
            writer.writerow([])
            writer.writerow(
                [
                    "calibration_acquisition",
                    "raw_time_of_flight_s",
                    "tof_tukey_iqr_status",
                    "setup_transmission_loss_db",
                    "transmission_tukey_iqr_status",
                ]
            )
            for calibration in self.calibration_results:
                writer.writerow(
                    [
                        calibration.acquisition,
                        calibration.time_of_flight,
                        "retained" if calibration.retained else "outlier",
                        abs(calibration.transmission_loss),
                        (
                            "retained"
                            if calibration.transmission_retained
                            else "outlier"
                        ),
                    ]
                )

        plot_times = self._concatenated_analysis_times()
        with waveform_file.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(
                [
                    "acquisition",
                    "sample",
                    "time_s",
                    "concatenated_time_s",
                    "filtered_reference_v",
                    "filtered_measured_v",
                ]
            )
            for result, concatenated_time in zip(
                self.analysis_results, plot_times
            ):
                local_time = result.time - result.time[0]
                writer.writerows(
                    (
                        result.acquisition,
                        sample,
                        time_value,
                        concatenated_value,
                        reference,
                        measured,
                    )
                    for sample, (
                        time_value,
                        concatenated_value,
                        reference,
                        measured,
                    ) in enumerate(
                        zip(
                            local_time,
                            concatenated_time,
                            result.reference,
                            result.measured,
                        )
                    )
                )
        self.analysis_status.setText(
            f"Exported {summary_file.name} and {waveform_file.name}."
        )

    def _acquisition_finished(self, result: object) -> None:
        if self.api is None or not isinstance(result, dict):
            return
        self.status.setText(str(result.get("message") or "Acquisition finished."))
        if not result.get("ready"):
            self._set_busy(False)
            return
        if (
            self.api.measuredSamples is None
            or self.api.referenceSamples is None
            or self.api.frequencyAcquire is None
        ):
            self.status.setText("Acquisition completed without sample data.")
            self._set_busy(False)
            return

        self.last_measured = np.ctypeslib.as_array(self.api.measuredSamples).copy()
        self.last_reference = np.ctypeslib.as_array(self.api.referenceSamples).copy()
        sample_rate = float(self.api.frequencyAcquire)
        self.last_time = np.arange(self.api.nSamples, dtype=float) / sample_rate

        self.signal_plot.clear()
        self.signal_plot.addLegend(offset=(12, 12))
        self.signal_plot.plot(
            self.last_time,
            self.last_measured,
            pen=pg.mkPen("#087e8b", width=2),
            name="Measured",
        )
        self.signal_plot.plot(
            self.last_time,
            self.last_reference,
            pen=pg.mkPen("#d1495b", width=2),
            name="Reference",
        )
        self.summary_samples.setText(f"{self.api.nSamples:,}")
        self.summary_rate.setText(f"{sample_rate:g} Hz")
        self.summary_duration.setText(f"{self.api.nSamples / sample_rate:g} s")
        self.dashboard_acquisition.setText("Acquisition complete")
        self._set_busy(False)

    def _start_hardware_operation(
        self,
        operation: Callable[[], object],
        on_success: Callable[[object], None],
        message: str,
        status_label: QLabel | None = None,
    ) -> None:
        if self.hardware_thread is not None:
            return
        self.operation_status_label = status_label or self.status
        self.operation_status_label.setText(message)
        self.operation_success = on_success
        self._set_busy(True)
        self.hardware_thread = QThread(self)
        self.hardware_worker = HardwareWorker(operation)
        self.hardware_worker.moveToThread(self.hardware_thread)
        self.hardware_thread.started.connect(self.hardware_worker.run)
        self.hardware_worker.completed.connect(self._operation_completed)
        self.hardware_worker.failed.connect(self._operation_failed)
        self.hardware_worker.completed.connect(self.hardware_thread.quit)
        self.hardware_worker.failed.connect(self.hardware_thread.quit)
        self.hardware_worker.completed.connect(self.hardware_worker.deleteLater)
        self.hardware_worker.failed.connect(self.hardware_worker.deleteLater)
        self.hardware_thread.finished.connect(self._operation_thread_finished)
        self.hardware_thread.start()

    @Slot(object)
    def _operation_completed(self, result: object) -> None:
        if self.operation_success is not None:
            self.operation_success(result)

    @Slot(str)
    def _operation_failed(self, message: str) -> None:
        status_label = self.operation_status_label or self.status
        status_label.setText(message)
        self.pending_specimen_operation = None
        self.post_operation_action = None
        self.dashboard_acquisition.setText("Operation failed")
        self._set_busy(False)
        QMessageBox.critical(self, "Hardware operation failed", message)

    @Slot()
    def _operation_thread_finished(self) -> None:
        post_operation_action = self.post_operation_action
        self.post_operation_action = None
        if self.hardware_thread is not None:
            self.hardware_thread.deleteLater()
        self.hardware_thread = None
        self.hardware_worker = None
        self.operation_success = None
        self.operation_status_label = None
        if post_operation_action is not None:
            post_operation_action()

    def _set_busy(self, busy: bool) -> None:
        connected = self.selected_device is not None
        self.device_button.setEnabled(not busy)
        self.configure_button.setEnabled(connected and not busy)
        self.analysis_run_button.setEnabled(connected and not busy)
        self.run_button.setEnabled(
            connected and self.oscilloscope_configured and not busy
        )
        self.export_button.setEnabled(
            self.last_time is not None and not busy
        )
        self.analysis_export_button.setEnabled(
            bool(self.analysis_results) and not busy
        )

    def export_csv(self) -> None:
        if (
            self.last_time is None
            or self.last_measured is None
            or self.last_reference is None
        ):
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export acquisition",
            "acquisition.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["time_s", "measured_v", "reference_v"])
            writer.writerows(
                zip(self.last_time, self.last_measured, self.last_reference)
            )
        self.status.setText(f"Exported acquisition to {path}")

    def open_device_dialog(self):
        if self.api is None:
            try:
                self.api = DigilentApi()
            except Exception as error:
                QMessageBox.critical(self, "DWF library unavailable", str(error))
                return

        dialog = DeviceDialog(self.api, self)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or dialog.selected_device is None
        ):
            return

        self.selected_device = dialog.selected_device
        self.device_button.setText(self.selected_device["name"])
        self.status.setText(
            f"Connected to {self.selected_device['name']} "
            f"({self.selected_device['serialnum']})."
        )
        self.analysis_status.setText(
            f"Connected to {self.selected_device['name']} "
            f"({self.selected_device['serialnum']})."
        )
        try:
            self._populate_channel_controls()
        except Exception as error:
            self.configure_button.setEnabled(False)
            self.analysis_run_button.setEnabled(False)
            self.run_button.setEnabled(False)
            self.status.setText(f"Unable to read device channels: {error}")
            self.analysis_status.setText(
                f"Unable to read device channels: {error}"
            )
            QMessageBox.critical(
                self,
                "Channel retrieval failed",
                str(error),
            )


if __name__ == "__main__":
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(application.exec())