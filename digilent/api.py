from ctypes import byref, c_double, c_int, cdll, create_string_buffer
from .dwfconstants import *
import csv
from datetime import datetime
import math
from pathlib import Path
import time
import matplotlib.pyplot as plt
import sys
import numpy
from PySide6.QtCore import QStandardPaths
from .types import AvailableDevice, OscillsocopeStatus
from typing import Literal
from .signals import generate_pulse

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

        self.oscilloscope_mode = None
        self.measuredSamples = None
        self.referenceSamples = None
        self.nSamples = 0
        self.analogMeasurementChannel = None
        self.analogReferenceChannel = None
        
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
            print(serialnum.value)
            
            print("------------------------------")
            print("Device "+str(iDevice+1)+" : ")
            print("\t" + devicename.value.decode("utf-8", errors="replace"))
            print("\t" + serialnum.value.decode("utf-8", errors="replace"))
            self.dwf.FDwfDeviceOpen(c_int(iDevice), byref(self.hdwf))
            if self.hdwf.value == 0:
                szerr = create_string_buffer(512)
                self.dwf.FDwfGetLastErrorMsg(szerr)
                print(szerr.value.decode("utf-8", errors="replace"))
                available_devices.append({
                        "idx": iDevice,
                        "name": devicename.value.decode("utf-8", errors="replace"),
                        "serialnum": serialnum.value.decode("utf-8", errors="replace"),
                        "error": szerr.value.decode("utf-8", errors="replace")
                })
            else:
                available_devices.append({
                        "idx": iDevice,
                        "name": devicename.value.decode("utf-8", errors="replace"),
                        "serialnum": serialnum.value.decode("utf-8", errors="replace"),
                        "error":None
                        })
            self.dwf.FDwfDeviceClose(self.hdwf)
                
        return available_devices

    def connect_to_device(self,device_idx)->str|None:
        print(f"Connecting to device {device_idx}")

        connect_error=create_string_buffer(512)
        self.connected_device_idx=device_idx
        self.dwf.FDwfDeviceOpen(c_int(device_idx), byref(self.hdwf))
        if self.hdwf.value == hdwfNone.value:
            self.dwf.FDwfGetLastErrorMsg(connect_error)
            return connect_error.value.decode("utf-8", errors="replace")

        number_of_out_channels=c_int()

        self.dwf.FDwfAnalogOutCount(self.hdwf,byref(number_of_out_channels))

        print(f"Output channels:{number_of_out_channels.value:g}")

        number_of_channels=c_int()
        self.dwf.FDwfAnalogInChannelCount(self.hdwf,byref(number_of_channels))

        print(f"Analog in channels: {number_of_channels.value}")
        print("Connected Succesfully")

    def disconnect_from_device(self):
        print("Disconnecting from device")
        self.dwf.FDwfDeviceClose(self.hdwf)
        
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

        if (
            mode == "read"
            and not minimum_buffer.value <= number_of_samples <= maximum_buffer.value
        ):
            return {
                "ready": False,
                "message": (
                    "Selected number of samples is not supported. Select a value "
                    f"between {minimum_buffer.value} and {maximum_buffer.value}."
                ),
            }

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

        time.sleep(2)
        return {
            "ready": True,
            "message": f'The oscilloscope has been set to mode: "{mode.title()}".',
        }

    def read_oscilloscope(self)->OscillsocopeStatus:
        sts = c_byte()

        if self.oscilloscope_mode  not in ["read","record"] or self.measuredSamples is None or self.analogMeasurementChannel is None:
            return {"ready":False,"message":"Oscilloscope not configured yet"}

        if self.oscilloscope_mode == "read":
            # Start the acquisition
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
                time.sleep(0.1)
            print("Acquisition done")

            self._require_dwf(
                self.dwf.FDwfAnalogInStatusData(self.hdwf, self.analogMeasurementChannel, self.measuredSamples, 4000),
                "Reading analog input samples",
            )

            return {"ready": True, "message": f"Oscilloscope data aqcuired"}

        else:
            print("Start Recorder")
            self._require_dwf(
                self.dwf.FDwfAnalogInConfigure(self.hdwf, 0, 1),
                "Starting record acquisition",
            )
            cSamples = 0
            cAvailable = c_int()
            cLost = c_int()
            cCorrupted = c_int()
            fLost = 0
            fCorrupted = 0

            while cSamples < self.nSamples:
                self._require_dwf(
                    self.dwf.FDwfAnalogInStatus(self.hdwf, 1, byref(sts)),
                    "Reading acquisition status",
                )
                if cSamples == 0 and (sts.value == DwfStateConfig.value or sts.value == DwfStatePrefill.value or sts.value == DwfStateArmed.value) :
                    # Acquisition not yet started.
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
                if cAvailable.value==0 :
                    continue
                if cSamples+cAvailable.value > self.nSamples :
                    cAvailable.value = self.nSamples-cSamples

                # Read the first input or Scope1
                print(f"Writing buffer location {cSamples} of {self.nSamples}", end='\n')
                self._require_dwf(
                    self.dwf.FDwfAnalogInStatusData(
                        self.hdwf,
                        self.analogMeasurementChannel,
                        byref(self.measuredSamples, sizeof(c_double)*cSamples),
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

    def generate_wave(self,out_channel:int,center_frequency:float,number_of_cycles:int,voltage_amplitude:float)->OscillsocopeStatus:

        number_of_out_channels=c_int()

        if not self.dwf.FDwfAnalogOutCount(self.hdwf,byref(number_of_out_channels)):
            return {"ready":False,"message":self._get_error()}

        if not 0<out_channel<(number_of_out_channels.value-1):
            return {"ready":False,"message":f"Channel provided falls outside allowed range, pick a value between 0 and {number_of_out_channels.value-1}"}

        channel = c_int(out_channel)
        self.analogOutChannel = c_int(out_channel)
        time,voltage=generate_pulse(center_frequency,number_of_cycles)
        AWG_Function = funcCustom
        points = len(voltage)
        buffer = buffer = (c_double * points)(*[float(value) for value in voltage])

        total_time=time[-1]-time[0]
        samplesMin=c_int()
        samplesmax=c_double()

        if not self.dwf.FDwfAnalogOutNodeDataInfo(self.hdwf,channel,AnalogOutNodeCarrier,byref(samplesMin),byref(samplesmax)):
            return {"ready":False,"message":self._get_error()}

        if not samplesMin.value<= points<=samplesmax.value:
            suggestion = (
            "Increase frequency."
            if points < samplesMin.value
            else "Reduce frequency."
            )

            return {
                "ready": False,
                "message": (
                    "Frequency cannot support the appropriate number of points. "
                    f"{suggestion}"
                ),}

        # Check if frequency within allowed range
        frequencyMin=c_double()
        frequencyMax=c_double()

        if not self.dwf.FDwfAnalogOutNodeFrequencyInfo(self.hdwf,channel,AnalogOutNodeCarrier,byref(frequencyMin),byref(frequencyMax)):
             return {"ready":False,"message":self._get_error()}

        if not frequencyMin.value<=center_frequency<=frequencyMax.value:
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
        
        self.dwf.FDwfAnalogOutNodeEnableSet(self.hdwf, channel, AnalogOutNodeCarrier, c_int(1))
        # set function type
        self.dwf.FDwfAnalogOutNodeFunctionSet(self.hdwf, channel, AnalogOutNodeCarrier, AWG_Function)
        # Set data for custom function
        self.dwf.FDwfAnalogOutNodeDataSet(self.hdwf, channel, AnalogOutNodeCarrier, buffer, points)
        # set frequency
        self.dwf.FDwfAnalogOutNodeFrequencySet(self.hdwf, channel, AnalogOutNodeCarrier, c_double(center_frequency))
        # set amplitude
        self.dwf.FDwfAnalogOutNodeAmplitudeSet(self.hdwf, channel, AnalogOutNodeCarrier, c_double(voltage_amplitude))
        # set offset, always centered around 0
        self.dwf.FDwfAnalogOutNodeOffsetSet(self.hdwf, channel, AnalogOutNodeCarrier, c_double(0))
        # set symmetry to 50%
        self.dwf.FDwfAnalogOutNodeSymmetrySet(self.hdwf, channel, AnalogOutNodeCarrier, c_double(50))

        # set running time limit
        self.dwf.FDwfAnalogOutRunSet(self.hdwf, channel, c_double(total_time)) # one buffer
        # set wait time before start
        self.dwf.FDwfAnalogOutWaitSet(self.hdwf, channel, c_double(0))
        # set number of repeating cycles
        self.dwf.FDwfAnalogOutRepeatSet(self.hdwf, channel, 1)
        # start
        self.dwf.FDwfAnalogOutConfigure(self.hdwf, channel, c_bool(True))

        while True:
            sts=
    
            

        