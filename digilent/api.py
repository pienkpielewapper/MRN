from ctypes import byref, c_double, c_int, cdll, create_string_buffer, c_byte, sizeof
from .dwfconstants import *
import time
import sys
from .types import AvailableDevice, OscillsocopeStatus
from collections.abc import Callable, Sequence
from typing import Literal

class DigilentApi():
    def __init__(self) :
        super().__init__()
        if sys.platform.startswith("win"):
            self.dwf = cdll.dwf
        elif sys.platform.startswith("darwin"):
            self.dwf = cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
        else:
            self.dwf = cdll.LoadLibrary("libdwf.so")
        self.hdwf = c_int()
        self.connected_device_serial: str | None = None

        self.oscilloscope_mode = None
        self.measuredSamples = None
        self.referenceSamples = None
        self.nSamples = 0
        self.analogMeasurementChannel = None
        self.analogReferenceChannel = None
        self.frequencyAcquire=None

    def _get_error(self):
        "Returns digilent waveforms sdk error"
        szerr = create_string_buffer(512)
        self.dwf.FDwfGetLastErrorMsg(szerr)
        error=szerr.value.decode("utf-8", errors="replace")
        print(error)
        return error

    def _require_dwf(self, result: int, operation: str) -> None:
        if not result:
            raise RuntimeError(f"{operation} failed: {self._get_error()}")

    def get_available_devices(self):
        cDevice = c_int()
        # declare string variables
        szerr = create_string_buffer(512)
        sz0 = create_string_buffer(256)
        sz1 = create_string_buffer(256)

        devicename = create_string_buffer(64)
        serialnum = create_string_buffer(16)

        # print(DWF version
        version = create_string_buffer(16)
        self.dwf.FDwfGetVersion(version)
        print("DWF Version: " + version.value.decode("utf-8", errors="replace"))

        # enumerate connected devices
        self.dwf.FDwfEnum(c_int(0), byref(cDevice))
        print("Number of Devices: "+str(cDevice.value))
        
        available_devices: list[AvailableDevice]=[]
        # open devices
        for iDevice in range(0, cDevice.value):
            self.dwf.FDwfEnumDeviceName(c_int(iDevice), devicename)
            self.dwf.FDwfEnumSN(c_int(iDevice), serialnum)
            device_serial = serialnum.value.decode("utf-8", errors="replace")
            print(serialnum.value)
            
            print("------------------------------")
            print("Device "+str(iDevice+1)+" : ")
            print("\t" + devicename.value.decode("utf-8", errors="replace"))
            print("\t" + serialnum.value.decode("utf-8", errors="replace"))
            if (
                self.hdwf.value != hdwfNone.value
                and self.connected_device_serial == device_serial
            ):
                number_of_channels = c_int()
                if self.dwf.FDwfAnalogInChannelCount(
                    self.hdwf, byref(number_of_channels)
                ):
                    available_devices.append({
                        "idx": iDevice,
                        "name": devicename.value.decode("utf-8", errors="replace"),
                        "serialnum": device_serial,
                        "error": None,
                        "connected": True,
                    })
                    continue

                self.dwf.FDwfDeviceClose(self.hdwf)
                self.hdwf = c_int()
                self.connected_device_serial = None

            probe_handle = c_int()
            self.dwf.FDwfDeviceOpen(c_int(iDevice), byref(probe_handle))
            if probe_handle.value == hdwfNone.value:
                szerr = create_string_buffer(512)
                self.dwf.FDwfGetLastErrorMsg(szerr)
                print(szerr.value.decode("utf-8", errors="replace"))
                available_devices.append({
                        "idx": iDevice,
                        "name": devicename.value.decode("utf-8", errors="replace"),
                        "serialnum": device_serial,
                        "error": szerr.value.decode("utf-8", errors="replace"),
                        "connected": False,
                })
            else:
                available_devices.append({
                        "idx": iDevice,
                        "name": devicename.value.decode("utf-8", errors="replace"),
                        "serialnum": device_serial,
                        "error":None,
                        "connected": False,
                        })
                self.dwf.FDwfDeviceClose(probe_handle)
                
        return available_devices

    def connect_to_device(self,device_idx)->str|None:
        print(f"Connecting to device {device_idx}")
        connect_error=create_string_buffer(512)
        new_handle = c_int()
        self.dwf.FDwfDeviceOpen(c_int(device_idx), byref(new_handle))
        if new_handle.value == hdwfNone.value:
            self.dwf.FDwfGetLastErrorMsg(connect_error)
            return connect_error.value.decode("utf-8", errors="replace")

        serialnum = create_string_buffer(16)
        if not self.dwf.FDwfEnumSN(c_int(device_idx), serialnum):
            self.dwf.FDwfDeviceClose(new_handle)
            return self._get_error()

        if self.hdwf.value != hdwfNone.value:
            self.dwf.FDwfDeviceClose(self.hdwf)
        self.hdwf = new_handle
        self.connected_device_serial = serialnum.value.decode(
            "utf-8", errors="replace"
        )

        number_of_out_channels=c_int()

        self.dwf.FDwfAnalogOutCount(self.hdwf,byref(number_of_out_channels))

        print(f"Output channels:{number_of_out_channels.value:g}")

        number_of_channels=c_int()
        self.dwf.FDwfAnalogInChannelCount(self.hdwf,byref(number_of_channels))

        print(f"Analog in channels: {number_of_channels.value}")
        print("Connected Succesfully")

    def get_channel_counts(self) -> tuple[int, int]:
        if self.hdwf.value == hdwfNone.value:
            raise RuntimeError("No device is connected")

        analog_input_count = c_int()
        analog_output_count = c_int()
        self._require_dwf(
            self.dwf.FDwfAnalogInChannelCount(
                self.hdwf, byref(analog_input_count)
            ),
            "Reading the analog input channel count",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutCount(
                self.hdwf, byref(analog_output_count)
            ),
            "Reading the analog output channel count",
        )
        return analog_input_count.value, analog_output_count.value

    def disconnect_from_device(self):
        print("Disconnecting from device")
        self.dwf.FDwfDeviceClose(self.hdwf)
        self.hdwf=c_int()
        self.connected_device_serial = None
        
    def configure_oscilloscope(
        self,
        mode: Literal["read", "record"],
        sample_rate: float,
        measured_voltage_range: float,
        reference_voltage_range: float,
        filtering_scheme: Literal[
            "filterDecimate",
            "filterAverage",
            "filterMinMax",
            "filterAverageFit",
        ],
        number_of_samples: int,
        measured_channel: int,
        reference_channel: int,
    ) -> OscillsocopeStatus:
        """Configure simultaneous measured and reference analog input channels."""

        if mode not in ["read", "record"]:
            return {
                "ready": False,
                "message": "Invalid mode selected, either choose read or record.",
            }

        if self.hdwf.value == hdwfNone.value:
            return {
                "ready": False,
                "message": "No device is connected. Please connect to a device before attempting to set oscilloscope.",
            }

        number_of_channels = c_int()
        if not self.dwf.FDwfAnalogInChannelCount(
            self.hdwf, byref(number_of_channels)
        ):
            return {"ready": False, "message": self._get_error()}

        for channel_name, channel in (
            ("Measured", measured_channel),
            ("Reference", reference_channel),
        ):
            if not 0 <= channel < number_of_channels.value:
                return {
                    "ready": False,
                    "message": (
                        f"{channel_name} channel falls outside the allowed range. "
                        f"Select a value between 0 and {number_of_channels.value - 1}."
                    ),
                }

        if measured_channel == reference_channel:
            return {
                "ready": False,
                "message": "Measured and reference channels must be different.",
            }

        minimum_frequency = c_double()
        maximum_frequency = c_double()
        if not self.dwf.FDwfAnalogInFrequencyInfo(
            self.hdwf,
            byref(minimum_frequency),
            byref(maximum_frequency),
        ):
            return {"ready": False, "message": self._get_error()}

        if not minimum_frequency.value <= sample_rate <= maximum_frequency.value:
            return {
                "ready": False,
                "message": (
                    "Selected sample rate is not supported. Select a value between "
                    f"{minimum_frequency.value:g} and {maximum_frequency.value:g} Hz."
                ),
            }

        minimum_buffer = c_int()
        maximum_buffer = c_int()
        if not self.dwf.FDwfAnalogInBufferSizeInfo(
            self.hdwf, byref(minimum_buffer), byref(maximum_buffer)
        ):
            return {"ready": False, "message": self._get_error()}

        if mode=="read" and not minimum_buffer.value <= number_of_samples <= maximum_buffer.value:
            return {
                "ready": False,
                "message": (
                    "Selected number of samples is not supported. Select a value "
                    f"between {minimum_buffer.value} and {maximum_buffer.value}."
                ),
            }

        if number_of_samples<=0:
            return {"ready":False,"message":"Number of samples must be greater than 0."}

        filtering_options = {
            "filterDecimate": filterDecimate,
            "filterAverage": filterAverage,
            "filterMinMax": filterMinMax,
            "filterAverageFit": filterAverageFit,
        }
        filtering = filtering_options.get(filtering_scheme)
        if filtering is None:
            return {
                "ready": False,
                "message": "Invalid analog input filtering scheme selected.",
            }

        measured_samples = (c_double * number_of_samples)()
        reference_samples = (c_double * number_of_samples)()

        def configure_channel(
            channel: int,
            voltage_range: float,
            channel_name: str,
        ) -> None:
            self._require_dwf(
                self.dwf.FDwfAnalogInChannelEnableSet(
                    self.hdwf, c_int(channel), c_int(1)
                ),
                f"Enabling {channel_name} analog input channel",
            )
            self._require_dwf(
                self.dwf.FDwfAnalogInChannelRangeSet(
                    self.hdwf, c_int(channel), c_double(voltage_range)
                ),
                f"Setting {channel_name} analog input range",
            )
            self._require_dwf(
                self.dwf.FDwfAnalogInChannelFilterSet(
                    self.hdwf, c_int(channel), filtering
                ),
                f"Setting {channel_name} analog input filter",
            )

        self._require_dwf(
            self.dwf.FDwfDeviceAutoConfigureSet(self.hdwf, c_int(0)),
            "Disabling automatic configuration",
        )
        for channel in range(number_of_channels.value):
            self._require_dwf(
                self.dwf.FDwfAnalogInChannelEnableSet(
                    self.hdwf, c_int(channel), c_int(0)
                ),
                f"Disabling analog input channel {channel}",
            )
        self._require_dwf(
            self.dwf.FDwfAnalogInFrequencySet(self.hdwf, c_double(sample_rate)),
            "Setting the analog input frequency",
        )

        configure_channel(
            measured_channel,
            measured_voltage_range,
            "measured",
        )
        configure_channel(
            reference_channel,
            reference_voltage_range,
            "reference",
        )
        
        
        if mode == "read":
            self._require_dwf(
                            self.dwf.FDwfAnalogInAcquisitionModeSet(self.hdwf, acqmodeSingle),
                            "Setting record acquisition mode",
                        )
            self._require_dwf(
                                    self.dwf.FDwfAnalogInBufferSizeSet(
                                        self.hdwf, c_int(number_of_samples)
                                    ),
                                    "Setting the analog input buffer size",
                                )           

        else:
            self._require_dwf(
                self.dwf.FDwfAnalogInAcquisitionModeSet(self.hdwf, acqmodeRecord),
                "Setting record acquisition mode",
            )
            self._require_dwf(
                self.dwf.FDwfAnalogInRecordLengthSet(
                    self.hdwf, c_double(number_of_samples / sample_rate)
                ),
                "Setting the record length",
            )
            print(f"Record length: {number_of_samples / sample_rate}")

        self._require_dwf(
            self.dwf.FDwfAnalogInConfigure(self.hdwf, c_int(1), c_int(0)),
            "Applying the analog input configuration",
        )

        self.oscilloscope_mode = mode
        self.analogMeasurementChannel = c_int(measured_channel)
        self.analogReferenceChannel = c_int(reference_channel)
        self.measuredSamples = measured_samples
        self.referenceSamples = reference_samples
        self.nSamples = number_of_samples
        # Set sample rate in memory
        self.frequencyAcquire=sample_rate
        time.sleep(2)
        return {
            "ready": True,
            "message": f'The oscilloscope has been set to mode: "{mode.title()}".',
        }

    def read_oscilloscope(
        self,
        timeout_seconds: float | None = None,
        on_acquisition_started: Callable[[], None] | None = None,
    ) -> OscillsocopeStatus:
        sts = c_byte()

        if self.oscilloscope_mode  not in ["read","record"] or self.measuredSamples is None or self.analogMeasurementChannel is None or self.referenceSamples is None or self.frequencyAcquire is None:
            return {"ready":False,"message":"Oscilloscope not configured yet"}

        if timeout_seconds is not None and timeout_seconds <= 0:
            return {"ready": False, "message": "Timeout must be greater than 0 seconds."}

        acquisition_duration = self.nSamples / self.frequencyAcquire

        print(f"Acquisition duration: {acquisition_duration}")
        effective_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else max(10.0, acquisition_duration * 1.5 + 1.0)
        )
        deadline = time.monotonic() + effective_timeout

        if self.oscilloscope_mode == "read":
            # Start the acquisition
            if on_acquisition_started is not None:
                on_acquisition_started()

            self._require_dwf(
                self.dwf.FDwfAnalogInConfigure(self.hdwf, c_int(1), c_int(1)),
                "Starting acquisition",
            )
            
            # Wait for the acquisition to complete before reading the buffer.
            while True:
                self._require_dwf(
                    self.dwf.FDwfAnalogInStatus(self.hdwf, c_int(1), byref(sts)),
                    "Reading acquisition status",
                )
                if sts.value == DwfStateDone.value :
                    break
                if time.monotonic() >= deadline:
                    self._require_dwf(
                        self.dwf.FDwfAnalogInConfigure(
                            self.hdwf, c_int(1), c_int(0)
                        ),
                        "Stopping timed-out acquisition",
                    )
                    return {
                        "ready": False,
                        "message": "Oscilloscope acquisition timed out.",
                    }
                # time.sleep(0.1)
            print("Acquisition done")

            self._require_dwf(
                self.dwf.FDwfAnalogInStatusData(self.hdwf, self.analogMeasurementChannel, self.measuredSamples, self.nSamples),
                "Reading analog measured samples",
            )

            self._require_dwf(
                            self.dwf.FDwfAnalogInStatusData(self.hdwf, self.analogReferenceChannel, self.referenceSamples, self.nSamples),
                            "Reading analog reference samples",
                        )

            return {"ready": True, "message": f"Oscilloscope data aqcuired"}

        else:
            buffer_size = c_int()
            self._require_dwf(
                self.dwf.FDwfAnalogInBufferSizeGet(
                    self.hdwf,
                    byref(buffer_size),
                ),
                "Reading the configured analog input buffer size",
            )

            buffer_duration = buffer_size.value / self.frequencyAcquire
            polling_interval = buffer_duration 

            print("Start Recorder")
            self._require_dwf(
                self.dwf.FDwfAnalogInConfigure(self.hdwf, 0, 1),
                "Starting record acquisition",
            )
            if on_acquisition_started is not None:
                on_acquisition_started()

            cSamples = 0
            cAvailable = c_int()
            cLost = c_int()
            cCorrupted = c_int()
            fLost = 0
            fCorrupted = 0

            print(f"Polling interval: {polling_interval}")
            while cSamples < self.nSamples:
                if time.monotonic() >= deadline:
                    self._require_dwf(
                        self.dwf.FDwfAnalogInConfigure(
                            self.hdwf, c_int(1), c_int(0)
                        ),
                        "Stopping timed-out record acquisition",
                    )
                    return {
                        "ready": False,
                        "message": "Oscilloscope record acquisition timed out.",
                    }
                remaining_time = deadline - time.monotonic()
                # time.sleep(min(polling_interval, max(0.0, remaining_time)))
                
                self._require_dwf(
                    self.dwf.FDwfAnalogInStatus(self.hdwf, 1, byref(sts)),
                    "Reading acquisition status",
                )

                if cSamples == 0 and (sts.value == DwfStateConfig.value or sts.value == DwfStatePrefill.value or sts.value == DwfStateArmed.value) :
                    # Acquisition not yet started.
                    print("Acquisition not yet started")
                    continue
                # check for lost samples or corrupt data
                self._require_dwf(
                    self.dwf.FDwfAnalogInStatusRecord(self.hdwf, byref(cAvailable), byref(cLost), byref(cCorrupted)),
                    "Reading record status",
                )
                
                cSamples += cLost.value
                if cLost.value :
                    fLost = 1
                if cCorrupted.value :
                    fCorrupted = 1
                if cSamples >= self.nSamples:
                    print("All samples acquired")
                    break
                if cAvailable.value==0 :
                    continue
                if cSamples+cAvailable.value > self.nSamples :
                    cAvailable.value = self.nSamples-cSamples

                self._require_dwf(
                    self.dwf.FDwfAnalogInStatusData(
                        self.hdwf,
                        self.analogMeasurementChannel,
                        byref(self.measuredSamples, sizeof(c_double)*cSamples),
                        cAvailable,
                    ),
                    "Reading recorded samples",
                )

                self._require_dwf(
                    self.dwf.FDwfAnalogInStatusData(
                        self.hdwf,
                        self.analogReferenceChannel,
                        byref(self.referenceSamples, sizeof(c_double)*cSamples),
                        cAvailable,
                    ),
                    "Reading recorded samples",
                )

                cSamples += cAvailable.value

            warnings = []
            if fLost:
                warnings.append("samples were lost")
            if fCorrupted:
                warnings.append("samples may be corrupted")

            message = "Oscilloscope data retrieved"
            if warnings:
                message += "; " + " and ".join(warnings)


            return {"ready": True, "message": f"{message}"}

    def prepare_wave(
        self,
        out_channel: int,
        voltage: Sequence[float],
        waveform_frequency: float,
        voltage_amplitude: float,
    ) -> OscillsocopeStatus:

        number_of_out_channels=c_int()

        if not self.dwf.FDwfAnalogOutCount(self.hdwf,byref(number_of_out_channels)):
            return {"ready":False,"message":self._get_error()}

        if not 0<=out_channel<=(number_of_out_channels.value-1):
            return {"ready":False,"message":f"Channel provided falls outside allowed range, pick a value between 0 and {number_of_out_channels.value-1}"}
        if waveform_frequency <= 0:
            return {"ready": False, "message": "Waveform frequency must be larger than 0"}

        channel = c_int(out_channel)
        AWG_Function = funcCustom
        points = len(voltage)
        buffer = (c_double * points)(*[float(value) for value in voltage])

        total_time=1/waveform_frequency
        samplesMin=c_int()
        samplesmax=c_int()

        if not self.dwf.FDwfAnalogOutNodeDataInfo(self.hdwf,channel,AnalogOutNodeCarrier,byref(samplesMin),byref(samplesmax)):
            return {"ready":False,"message":self._get_error()}

        if not samplesMin.value<= points <=samplesmax.value:
            suggestion = (
            "Increase frequency."
            if points < samplesMin.value
            else "Reduce frequency."
            )

            print(f"Frequency cannot support the appropriate number of points. The points should be between {samplesMin.value} and {samplesmax.value}, where your config had {points}")
            return {
                "ready": False,
                "message": (
                    f"Frequency cannot support the appropriate number of points. The points should be between {samplesMin.value} and {samplesmax.value}, where your config had {points} "
                    f"{suggestion}"
                ),}

        # Check if frequency within allowed range
        frequencyMin=c_double()
        frequencyMax=c_double()

        if not self.dwf.FDwfAnalogOutNodeFrequencyInfo(self.hdwf,channel,AnalogOutNodeCarrier,byref(frequencyMin),byref(frequencyMax)):
             return {"ready":False,"message":self._get_error()}

        if not frequencyMin.value<=waveform_frequency<=frequencyMax.value or waveform_frequency<=0:
                    suggestion = (
                                f"Increase frequency to above {frequencyMin.value} and below {frequencyMax.value}."
                                if points < samplesMin.value
                                else f"Reduce frequency to above {frequencyMin.value} and below {frequencyMax.value}."
                                )
                    
                    return {
                            "ready": False,
                            "message": (
                                "Frequency cannot support the appropriate number of points. "
                                f"{suggestion}"
                            ),}
                
        # Check if runtime within allowed range
        minSeconds=c_double()
        maxSeconds=c_double()
        if not self.dwf.FDwfAnalogOutRunInfo(self.hdwf,channel,byref(minSeconds),byref(maxSeconds)):
            return {"ready":False,"message":self._get_error()}

        if not minSeconds.value<=total_time<=maxSeconds.value:
            suggestion=f"The total run time is outside the limits of the hardware. The total time should be between {minSeconds.value}s and {maxSeconds.value}s. Your current setup results in {total_time}s"

            if total_time<minSeconds.value:
                 suggestion += "Consider decreasing the frequency or increasing the cycle count."
            else:
                suggestion += "Consider increasing the frequency or decreasing the cycle count."
            return {"ready":False,"message":suggestion}
      
        voltage_min=c_double()
        voltage_max=c_double()

        if not self.dwf.FDwfAnalogOutNodeAmplitudeInfo(self.hdwf,channel,AnalogOutNodeCarrier,byref(voltage_min),byref(voltage_max)):
            return {"ready":False,"message":self._get_error()}

        if not voltage_min.value<=voltage_amplitude<=voltage_max.value:
            return{"ready":False,"message":f"Invalid voltage amplitude selection, please select a value between {voltage_min.value} and {voltage_max.value}"}
        
        print("Configure Wavegen")
        
        self._require_dwf(
            self.dwf.FDwfAnalogOutNodeEnableSet(
                self.hdwf, channel, AnalogOutNodeCarrier, c_int(1)
            ),
            "Enabling the analog output carrier",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutNodeFunctionSet(
                self.hdwf, channel, AnalogOutNodeCarrier, AWG_Function
            ),
            "Setting the analog output function",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutNodeDataSet(
                self.hdwf,
                channel,
                AnalogOutNodeCarrier,
                buffer,
                c_int(points),
            ),
            "Setting the custom analog output data",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutNodeFrequencySet(
                self.hdwf,
                channel,
                AnalogOutNodeCarrier,
                c_double(waveform_frequency),
            ),
            "Setting the analog output frequency",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutNodeAmplitudeSet(
                self.hdwf,
                channel,
                AnalogOutNodeCarrier,
                c_double(voltage_amplitude),
            ),
            "Setting the analog output amplitude",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutNodeOffsetSet(
                self.hdwf, channel, AnalogOutNodeCarrier, c_double(0)
            ),
            "Setting the analog output offset",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutNodeSymmetrySet(
                self.hdwf, channel, AnalogOutNodeCarrier, c_double(50)
            ),
            "Setting the analog output symmetry",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutRunSet(
                self.hdwf, channel, c_double(total_time)
            ),
            "Setting the analog output run time",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutWaitSet(
                self.hdwf, channel, c_double(0)
            ),
            "Setting the analog output wait time",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutRepeatSet(
                self.hdwf, channel, c_int(1)
            ),
            "Setting the analog output repeat count",
        )
        self._require_dwf(
            self.dwf.FDwfAnalogOutConfigure(
                self.hdwf, channel, c_int(0)
            ),
            "Applying the analog output configuration",
        )

        self.analogOutChannel = channel
        return {"ready": True, "message": "Signal prepared"}

    def generate_wave(self) -> OscillsocopeStatus:
        if not hasattr(self, "analogOutChannel"):
            return {"ready": False, "message": "Signal not prepared yet"}

        self._require_dwf(
            self.dwf.FDwfAnalogOutConfigure(
                self.hdwf, self.analogOutChannel, c_int(1)
            ),
            "Starting the analog output",
        )
        return {"ready": True, "message": "Signal started"}
    
            

        