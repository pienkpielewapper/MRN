from ctypes import *
from digilent.dwfconstants import *
import math
import time
import matplotlib.pyplot as plt
import sys
import numpy

print("Start")
if sys.platform.startswith("win"):
    dwf = cdll.dwf
elif sys.platform.startswith("darwin"):
    dwf = cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
else:
    dwf = cdll.LoadLibrary("libdwf.so")

print(type(dwf))

hdwf = c_int()
szerr = create_string_buffer(512)
print("Opening first device")
dwf.FDwfDeviceOpen(c_int(-1), byref(hdwf))
 
if hdwf.value == hdwfNone.value:
    dwf.FDwfGetLastErrorMsg(szerr)
    print(str(szerr.value))
    print("failed to open device")
    quit()

# Set oscilloscope:
#-------------------------
# Disable auto-update on Set commands. Updates will occur only when FDwfAnalogInConfigure is called.
dwf.FDwfDeviceAutoConfigureSet(hdwf, c_int(0))
# Enable all the analog input channels
dwf.FDwfAnalogInChannelEnableSet(hdwf, c_int(-1), c_int(1))
# Set the sample rate
dwf.FDwfAnalogInFrequencySet(hdwf, c_double(20000000.0))
# Set the input voltage range
dwf.FDwfAnalogInChannelRangeSet(hdwf, c_int(-1), c_double(5))
# Set the buffer size
dwf.FDwfAnalogInBufferSizeSet(hdwf, c_int(4000))
# Select a filtering scheme
dwf.FDwfAnalogInChannelFilterSet(hdwf, c_int(-1), filterDecimate)
# Apply the configuration. 
dwf.FDwfAnalogInConfigure(hdwf, c_int(1), c_int(0))
# wait at least 2 seconds for the offset to stabilize
time.sleep(2)

# Start the acquisition
dwf.FDwfAnalogInConfigure(hdwf, c_int(1), c_int(1))
 
# Wait for the acquisition to complete before reading the buffer.
while True:
    dwf.FDwfAnalogInStatus(hdwf, c_int(1), byref(sts))
    if sts.value == DwfStateDone.value :
        break
    time.sleep(0.1)
print("Acquisition done")
 
# Fetch the data from the buffer. 
rgdSamples0 = (c_double*4000)()
rgdSamples1 = (c_double*4000)()
dwf.FDwfAnalogInStatusData(hdwf, 0, rgdSamples, 4000) # get channel 1 data
dwf.FDwfAnalogInStatusData(hdwf, 1, rgdSamples, 4000) # get channel 2 data
dwf.FDwfDeviceCloseAll()
 
# Plot the data using MatPlotLib
plt.plot(numpy.fromiter(rgdSamples0, dtype = float))
plt.plot(numpy.fromiter(rgdSamples1, dtype = float))
plt.show()