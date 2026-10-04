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
        self.oscilloscope_mode:Literal["read","record"]|None=None
        self.rgdSamples=None
        self.nSamples=0

    def _get_error(self):
        "Returns digilent waveforms sdk error"
        szerr = create_string_buffer(512)
        self.dwf.FDwfGetLastErrorMsg(szerr)
        error=szerr.value.decode("utf-8", errors="replace")
        print(error)
        return error

    def _save_samples(self) -> Path:
        if self.rgdSamples is None or self.oscilloscope_mode is None:
            raise ValueError("No oscilloscope samples are available")

        app_data = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        if not app_data:
            raise OSError("No writable application data directory is available")

        recordings_directory = Path(app_data) / "recordings"
        recordings_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        recording_path = recordings_directory / f"oscilloscope_{self.oscilloscope_mode}_{timestamp}.csv"

        with recording_path.open("w", newline="", encoding="utf-8") as recording_file:
            writer = csv.writer(recording_file)
            writer.writerow(["sample", "voltage"])
            writer.writerows(enumerate(self.rgdSamples))

        return recording_path

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
            if(serialnum.value == b'SN:210415BD66A3'):
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
        self.dwf.FDwfDeviceCloseAll()

        return available_devices


    def connect_to_device(self,device_idx)->str|None:
            connect_error=create_string_buffer(512)
            self.dwf.FDwfDeviceOpen(c_int(device_idx), byref(self.hdwf))

            if self.hdwf.value == 0:
                self.dwf.FDwfGetLastErrorMsg(connect_error)
                return connect_error.value.decode("utf-8", errors="replace")

    def configure_oscilloscope(self,mode:Literal["read","record"],sample_rate:float,voltage_range:float,filtering_scheme:Literal["filterDecimate","filterAverage","filterMinMax","filterAverageFit"],number_of_saamples:int)->OscillsocopeStatus:
        """
        Configures oscilloscope
        
        Args*:
        - Mode: determines mode, either read or record
        - Sample rate: sets the sample frequency in Hz
        - Voltage range: sets the voltage range in volt units
        - Number of samples: number of samples to be collected when in record mode
        """

        # Set number of samples
        self.nSamples=number_of_saamples
        if mode not in ["read","record"]:
            return {"ready":False,"message":"Invalid method selected, either choose read or record."}
        
        if self.hdwf.value == hdwfNone.value:
             return {"ready":False,"message":"No device is connected. Please connect to a device before attempting to set oscilloscope."}

        minimum = c_double()
        maximum = c_double()
        minimum_buffer = c_int()
        maximum_buffer=c_int()

        if not self.dwf.FDwfAnalogInFrequencyInfo(
            self.hdwf,
            byref(minimum),
            byref(maximum),
        ):
            frequency_info_error=self._get_error()
            return {"ready":False,"message":frequency_info_error}

        if not minimum.value <= sample_rate <= maximum.value:
             return {"ready":False,"message":f"selected sample rate is not supported, ensure you enter a value between {minimum.value:g} and {maximum.value:g} Hz."}

        if not self.dwf.FDwfAnalogInBufferSizeInfo(self.hdwf,byref(minimum_buffer),byref(maximum_buffer)):
            buffer_error=self._get_error()
            return {"ready":False,"message":buffer_error}

        if minimum_buffer.value <=number_of_saamples<=maximum_buffer.value:
            return {"ready":False,"message":f"Selected number of samples is not supported, ensure that you set a value between {minimum_buffer.value:g} and {maximum_buffer.value}"}
        
        filtering= filterDecimate

        if filtering_scheme=="filterAverage":
            filtering=filterAverage

        elif filtering_scheme=="filterAverageFit":
            filtering=filterAverageFit

        elif filtering_scheme == "filterMinMax":
            filtering=filterMinMax

        self.rgdSamples = (c_double*number_of_saamples)()
        # Disable auto-update on Set commands. Updates will occur only when FDwfAnalogInConfigure is called.
        self.dwf.FDwfDeviceAutoConfigureSet(self.hdwf, c_int(0))

        if mode =="read":

            # Set oscilloscope mode
            self.oscilloscope_mode="read"
            # Enable all the analog input channels
            # TODO: Check if the device only has one input channel
            self.dwf.FDwfAnalogInChannelEnableSet(self.hdwf, c_int(-1), c_int(1))
            # Set the sample rate
            self.dwf.FDwfAnalogInFrequencySet(self.hdwf, c_double(sample_rate))
            # Set the input voltage range
            self.dwf.FDwfAnalogInChannelRangeSet(self.hdwf, c_int(-1), c_double(voltage_range))
            # Set the buffer size
            self.dwf.FDwfAnalogInBufferSizeSet(self.hdwf, c_int(number_of_saamples))
            # Select a filtering scheme
            self.dwf.FDwfAnalogInChannelFilterSet(self.hdwf, c_int(-1), filtering)
            # Apply the configuration. 
            self.dwf.FDwfAnalogInConfigure(self.hdwf, c_int(1), c_int(0))
            # wait at least 2 seconds for the offset to stabilize
            time.sleep(2)


            return {"ready":True,"message":f"The oscilloscope has been set to mode :\"Read\" "}

        else:
            self.oscilloscope_mode="record"

            # Enable the input channels
            self.dwf.FDwfAnalogInChannelEnableSet(self.hdwf, c_int(-1), c_int(1))
            # Set the input range
            self.dwf.FDwfAnalogInChannelRangeSet(self.hdwf, 0, c_double(5))
            # Set the acquisition mode to Record
            self.dwf.FDwfAnalogInAcquisitionModeSet(self.hdwf, acqmodeRecord)
            # Set the sample rate
            self.dwf.FDwfAnalogInFrequencySet(self.hdwf, c_double(sample_rate))
            # Set the input voltage range
            self.dwf.FDwfAnalogInChannelRangeSet(self.hdwf, c_int(-1), c_double(voltage_range))
            # Set the recording length to required time
            self.dwf.FDwfAnalogInRecordLengthSet(self.hdwf, c_double(number_of_saamples/sample_rate)) 
            # Apply the settings
            self.dwf.FDwfAnalogInConfigure(self.hdwf, 1, 0)

            #wait at least 2 seconds for the offset to stabilize
            time.sleep(2)
            return {"ready":True,"message":f"The oscilloscope has been set to mode :\"Record\""}

    def read_oscilloscope(self)->OscillsocopeStatus:
        sts = c_byte()

        if self.oscilloscope_mode  not in ["read","record"] or self.rgdSamples is None:
            return {"ready":False,"message":"Oscilloscope not configured yet"}

        if self.oscilloscope_mode == "read":
            # Start the acquisition
            self.dwf.FDwfAnalogInConfigure(self.hdwf, c_int(1), c_int(1))

            # Wait for the acquisition to complete before reading the buffer.
            while True:
                self.dwf.FDwfAnalogInStatus(self.hdwf, c_int(1), byref(sts))
                if sts.value == DwfStateDone.value :
                    break
                time.sleep(0.1)
            print("Acquisition done")

            self.dwf.FDwfAnalogInStatusData(self.hdwf, 0, self.rgdSamples, 4000) # get channel 1 data
            # self.dwf.FDwfAnalogInStatusData(self.hdwf, 1, rgdSamples, 4000) # get channel 2 data

            try:
                recording_path = self._save_samples()
            except OSError as error:
                return {"ready": False, "message": f"Oscilloscope data retrieved but could not be saved: {error}"}

            return {"ready": True, "message": f"Oscilloscope data saved to {recording_path}"}

        else:
            print("Start Recorder")
            self.dwf.FDwfAnalogInConfigure(self.hdwf, 0, 1)
            cSamples = 0
            cAvailable = c_int()
            cLost = c_int()
            cCorrupted = c_int()
            fLost = 0
            fCorrupted = 0

            while cSamples < self.nSamples:
                self.dwf.FDwfAnalogInStatus(self.hdwf, 1, byref(sts))
                if cSamples == 0 and (sts.value == DwfStateConfig.value or sts.value == DwfStatePrefill.value or sts.value == DwfStateArmed.value) :
                    # Acquisition not yet started.
                    continue
                # check for lost samples or corrupt data
                self.dwf.FDwfAnalogInStatusRecord(self.hdwf, byref(cAvailable), byref(cLost), byref(cCorrupted))
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
                self.dwf.FDwfAnalogInStatusData(self.hdwf, 0, byref(self.rgdSamples, sizeof(c_double)*cSamples), cAvailable) # get channel 1 data
                cSamples += cAvailable.value

            self.dwf.FDwfAnalogOutReset(self.hdwf, 0)
            warnings = []
            if fLost:
                warnings.append("samples were lost")
            if fCorrupted:
                warnings.append("samples may be corrupted")

            message = "Oscilloscope data retrieved"
            if warnings:
                message += "; " + " and ".join(warnings)

            try:
                recording_path = self._save_samples()
            except OSError as error:
                return {"ready": False, "message": f"{message}; data could not be saved: {error}"}

            return {"ready": True, "message": f"{message}; saved to {recording_path}"}

    
            

        