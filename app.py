import csv
import math
import re
import sys
from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QValidator
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


SCIENTIFIC_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
SCIENTIFIC_PARTIAL = re.compile(
    r"^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d*)?)?$"
)


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
        self.hardware_thread: QThread | None = None
        self.hardware_worker: HardwareWorker | None = None
        self.operation_success: Callable[[object], None] | None = None

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
        navigation_labels = ("Dashboard", "Oscilloscope", "Settings")
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

        self.fft_magnitude_plot = pg.PlotWidget()
        self.fft_magnitude_plot.setMinimumHeight(260)
        self.fft_magnitude_plot.setBackground("#ffffff")
        self.fft_magnitude_plot.showGrid(x=True, y=True, alpha=0.18)
        self.fft_magnitude_plot.setLabel("bottom", "Frequency", units="Hz")
        self.fft_magnitude_plot.setLabel("left", "Magnitude", units="dBV")
        self.fft_magnitude_plot.addLegend(offset=(12, 12))
        self._add_plot_header(
            run_layout, "FFT magnitude", self.fft_magnitude_plot
        )
        run_layout.addWidget(self.fft_magnitude_plot)

        self.fft_phase_plot = pg.PlotWidget()
        self.fft_phase_plot.setMinimumHeight(260)
        self.fft_phase_plot.setBackground("#ffffff")
        self.fft_phase_plot.showGrid(x=True, y=True, alpha=0.18)
        self.fft_phase_plot.setLabel("bottom", "Frequency", units="Hz")
        self.fft_phase_plot.setLabel("left", "Unwrapped phase", units="deg")
        self.fft_phase_plot.addLegend(offset=(12, 12))
        self._add_plot_header(run_layout, "FFT phase", self.fft_phase_plot)
        run_layout.addWidget(self.fft_phase_plot)
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
        fit_button = QPushButton("Zoom to fit")
        fit_button.setObjectName("secondaryButton")
        fit_button.clicked.connect(lambda checked=False: self._fit_plot(plot))
        header.addWidget(title_label)
        header.addStretch()
        header.addWidget(fit_button)
        layout.addLayout(header)

    def _fit_plot(self, plot: pg.PlotWidget) -> None:
        plot.enableAutoRange(x=True, y=True)
        plot.autoRange()

    def _summary_item(self, layout: QHBoxLayout, label: str) -> QLabel:
        group = QVBoxLayout()
        group.addWidget(QLabel(label))
        value = QLabel("--")
        value.setObjectName("summaryValue")
        group.addWidget(value)
        layout.addLayout(group)
        layout.addSpacing(36)
        return value

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
        ):
            control.clear()
            control.addItem("Select channel", None)
            for channel in range(count):
                control.addItem(f"Channel {channel + 1}", channel)
        self.configure_button.setEnabled(True)

    def _required_combo(
        self, control: QComboBox, label: str, errors: list[str]
    ) -> object | None:
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
        if errors:
            self._show_missing(errors)
            return

        operation = lambda: self.api.configure_oscilloscope(
            mode=mode,
            sample_rate=self.sample_rate_input.value(),
            measured_voltage_range=self.measured_range_input.value(),
            reference_voltage_range=self.reference_range_input.value(),
            filtering_scheme=filtering,
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
                voltage=voltage,
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
            )

        self._start_hardware_operation(
            operation,
            self._acquisition_finished,
            "Arming acquisition and generating pulse...",
        )

    def _acquisition_finished(self, result: object) -> None:
        if self.api is None or not isinstance(result, dict):
            return
        self.status.setText(str(result.get("message") or "Acquisition finished."))
        if not result.get("ready"):
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
        self._update_fft_plots(sample_rate)
        self.summary_samples.setText(f"{self.api.nSamples:,}")
        self.summary_rate.setText(f"{sample_rate:g} Hz")
        self.summary_duration.setText(f"{self.api.nSamples / sample_rate:g} s")
        self.dashboard_acquisition.setText("Acquisition complete")
        self._set_busy(False)

    def _update_fft_plots(self, sample_rate: float) -> None:
        if self.last_measured is None or self.last_reference is None:
            return

        sample_count = min(len(self.last_measured), len(self.last_reference))
        if sample_count < 2:
            self.fft_magnitude_plot.clear()
            self.fft_phase_plot.clear()
            return

        window = np.hanning(sample_count)
        coherent_gain = window.sum()
        frequencies = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate)

        spectra: list[tuple[str, str, np.ndarray]] = []
        for label, color, samples in (
            ("Measured", "#087e8b", self.last_measured),
            ("Reference", "#d1495b", self.last_reference),
        ):
            centered_samples = samples[:sample_count] - np.mean(samples[:sample_count])
            spectrum = np.fft.rfft(centered_samples * window)
            magnitude = np.abs(spectrum) / coherent_gain
            if sample_count % 2 == 0:
                magnitude[1:-1] *= 2
            else:
                magnitude[1:] *= 2
            magnitude_dbv = 20 * np.log10(
                np.maximum(magnitude, np.finfo(float).tiny)
            )
            phase_degrees = np.degrees(np.unwrap(np.angle(spectrum)))
            spectra.append((label, color, np.vstack((magnitude_dbv, phase_degrees))))

        self.fft_magnitude_plot.clear()
        self.fft_phase_plot.clear()
        for label, color, spectral_data in spectra:
            pen = pg.mkPen(color, width=2)
            self.fft_magnitude_plot.plot(
                frequencies,
                spectral_data[0],
                pen=pen,
                name=label,
            )
            self.fft_phase_plot.plot(
                frequencies,
                spectral_data[1],
                pen=pen,
                name=label,
            )

    def _start_hardware_operation(
        self,
        operation: Callable[[], object],
        on_success: Callable[[object], None],
        message: str,
    ) -> None:
        if self.hardware_thread is not None:
            return
        self.status.setText(message)
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
        self.status.setText(message)
        self.dashboard_acquisition.setText("Operation failed")
        self._set_busy(False)
        QMessageBox.critical(self, "Hardware operation failed", message)

    @Slot()
    def _operation_thread_finished(self) -> None:
        if self.hardware_thread is not None:
            self.hardware_thread.deleteLater()
        self.hardware_thread = None
        self.hardware_worker = None
        self.operation_success = None

    def _set_busy(self, busy: bool) -> None:
        connected = self.selected_device is not None
        self.device_button.setEnabled(not busy)
        self.configure_button.setEnabled(connected and not busy)
        self.run_button.setEnabled(
            connected and self.oscilloscope_configured and not busy
        )
        self.export_button.setEnabled(
            self.last_time is not None and not busy
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
        try:
            self._populate_channel_controls()
        except Exception as error:
            self.configure_button.setEnabled(False)
            self.run_button.setEnabled(False)
            self.status.setText(f"Unable to read device channels: {error}")
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