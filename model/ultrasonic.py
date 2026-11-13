import numpy as np
from scipy import signal
from typing import Literal

def get_max_band(array:np.ndarray,percentage:float)->tuple[int,int]:
    if array.ndim != 1 or array.size == 0:
        raise ValueError("Signal must be a non-empty one-dimensional array")
    if not 0 < percentage <= 1:
        raise ValueError("Cutoff percentage must be greater than 0 and at most 1")

    cuttoff_value=np.max(array)*percentage
    max_idx=np.argmax(array)

    idxs_greater_than_cutoff=np.where(array>cuttoff_value)[0]
    if idxs_greater_than_cutoff.size == 0:
        raise ValueError("No signal band exceeds the cutoff")
    if idxs_greater_than_cutoff.size == 1:
        index = int(idxs_greater_than_cutoff[0])
        return index, index
    difference_between_idxs=np.diff(idxs_greater_than_cutoff)

    is_one = difference_between_idxs == 1

    # False boundaries ensure runs touching either edge are detected.
    changes = np.diff(np.concatenate(([False], is_one, [False])).astype(int))


    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)

    global_start=0
    global_end=0

    for start, end in zip(starts, ends):
        band_start = idxs_greater_than_cutoff[start]
        band_end = idxs_greater_than_cutoff[end]
    
        if band_start <= max_idx <= band_end:
            global_start = int(band_start)
            global_end = int(band_end)
    
            break

    return global_start, global_end
       
def get_frequency_spectra_range(time:np.ndarray,y:np.ndarray,cutoff_percentage:float,cutoff_expander:int,min_cutoff_frequency:float=0.00)->tuple[float,float,float]:
    """
    Gets the min and max effective frequency range of a broadband pulse

    time: Time values (numpy array expected)
    y: Corresponding values for the time series (numpy array expected)
    cutoff_percentage: This perecntage of the maximum amplitude of the signal will determine the cutoff frequencies, anything below it will be treated as noise and removed.
    cutoff_expander: This integer value will expand the tip and tail of the band by increasing the width of the band
    min_cutoff_frequency: Initial cutoff to remove noise that is present at for example 0Hz (steady state noise) 

    Returns:
    Tuple[min_cutoff, max_cutoff, sample_spacing]

    Note: This will only work for a broadband pulse with one continious range, if seperate ranges are contained within the signal it will fail
    """
    # Assumes time_s is in seconds and samples are uniformly spaced
    if time.size != y.size or time.size < 2:
        raise ValueError("Time and signal arrays must have equal lengths of at least 2")
    sample_spacing = np.median(np.diff(time))
    if not np.isfinite(sample_spacing) or sample_spacing <= 0:
        raise ValueError("Time values must be finite and strictly increasing")

    # Convert time domain -> frequency domain
    spectrum = np.fft.rfft(y)
    frequencies = np.fft.rfftfreq(len(y), d=sample_spacing)

    # Band-pass limits in Hz
    low_cutoff_hz = min_cutoff_frequency

    # Keep only frequencies inside the pass band
    passband = (
        (frequencies > low_cutoff_hz)
    )

    filtered_spectrum = spectrum.copy()
    filtered_spectrum[~passband] = 0
    
    min_cutoff_idx,_=get_max_band(np.abs(filtered_spectrum),cutoff_percentage)
    max_idx=np.argmax(np.abs(filtered_spectrum))

    min_cutoff_idx = max(min_cutoff_idx - cutoff_expander, 0)
    max_cutoff_idx = min(
        max_idx + (max_idx - min_cutoff_idx),
        len(frequencies) - 1,
    )
    min_cutoff = frequencies[min_cutoff_idx]
    max_cutoff = frequencies[max_cutoff_idx]

    return min_cutoff, max_cutoff, sample_spacing


def band_pass_filter(y:np.ndarray,low_pass_frequency:float, high_pass_frequency:float, sample_spacing:float)->np.ndarray: 
     # Convert time domain -> frequency domain
    spectrum = np.fft.rfft(y)
    frequencies = np.fft.rfftfreq(len(y), d=sample_spacing)

    
    inside_band = (
        (frequencies >= low_pass_frequency)
        & (frequencies <= high_pass_frequency)
    )

    

    spectrum[~inside_band] = 0

    filtered_signal= np.fft.irfft(
        spectrum,
        n=len(y)
    )

    return filtered_signal


def get_gain_factor(solve_for:Literal["in","out"],db_gain:float)->float:

    if solve_for=="out":
        return 10**(db_gain/20)

    else :
        return 1/(10**(db_gain/20))


def get_ultrasonic_characteristics(time:np.ndarray,y1:np.ndarray,y2:np.ndarray,f_min:float,f_max:float,sample_spacing:float)->tuple[float,float]:
    """
    Returns the Tof between the first refercnce and measured pulse, also returns the attenuation coefficicient data inside the given frequency range
    
    # Params:
    time: Time array
    y1: Refernce signal
    y2: Measured signal
    f_min: min cut off frequency
    f_max: max cut off frequency
    sample_spacing: spacing between values in time domain
    distance: distance traveled by wave
    
    Returns:
    ToF: float
    Attenuation coeffcient: attenuation at maximum frequency of y1  (still needs to be divided by distance traveled)
    """
    
    # First ToF
    # Get max idxs of each wave
    ref_max_idx=np.argmax(y1)
    measured_max_idx=np.argmax(y2)
    

    if measured_max_idx<ref_max_idx:
        raise ValueError("Measured signal max occurs before refernce wave, cannot compute")

    ToF= time[measured_max_idx]-time[ref_max_idx]
    spectrum_y1 = np.fft.rfft(y1)
    spectrum_y2= np.fft.rfft(y2)
    frequencies = np.fft.rfftfreq(len(y1), d=sample_spacing)

    frequencies_idxs=(
        np.where((frequencies>=f_min) & (frequencies<=f_max))[0]
    )
    if frequencies_idxs.size == 0:
        raise ValueError("No frequency bins fall inside the selected analysis band")

    filtered_spectra_y1=np.abs(np.take(spectrum_y1,frequencies_idxs))
    filtered_spectra_y2=np.abs(np.take(spectrum_y2,frequencies_idxs))

    max_idx=np.argmax(filtered_spectra_y1)
    reference_amplitude = filtered_spectra_y1[max_idx]
    measured_amplitude = filtered_spectra_y2[max_idx]
    if reference_amplitude <= 0 or measured_amplitude <= 0:
        raise ValueError("Peak spectral amplitudes must be greater than zero")
    attenuation=-20*np.log10(measured_amplitude/reference_amplitude)

    return ToF,attenuation

def SNR(array:np.ndarray,start:int,end:int)->float:
    if not 0 <= start < end <= len(array):
        raise ValueError("Pulse bounds must define a non-empty range inside the signal")

    outside_pulse_array = np.concat([array[:start],array[end:]])
    if outside_pulse_array.size == 0:
        raise ValueError("Signal must include samples outside the detected pulse")
    
    N_rms=np.sqrt(np.mean(np.square(outside_pulse_array)))

    pulse=array[start:end]
    array_Pulse_Amplitude = (np.max(pulse)-np.min(pulse))/2

    if N_rms <= 0 or array_Pulse_Amplitude <= 0:
        raise ValueError("Pulse amplitude and noise level must be greater than zero")
    SNR=20*np.log10(array_Pulse_Amplitude/N_rms)

    return SNR

def review_ultrasonic_signals(
    time: np.ndarray,
    reference: np.ndarray,
    measured: np.ndarray,
    cutoff_percentage: float = 0.05,
    frequency_cutoff_expander: int = 10,
    time_cutoff_expander:int=10,
    min_cutoff_frequency: float = 0.0,
    ref_db_gain:float=0.0,
    measured_db_gain:float=0.0,
    apply_gain: bool = False,
    
) -> tuple[float, float, tuple[float, float], tuple[float, float], np.ndarray, np.ndarray, np.ndarray, float, float]:
    """
    Determine Signal Properties

    Params:
    - time : time array (preferrably in seconds)
    - reference : reference pulse
    - measured: measured pulse
    - cutoff_percentage: percentage of max value to use to detect start of pulse/ frequency spectra
    - cutoff_expander: shfts the frequency domain cutoff idxs (min-expander),(max+expander)
    - db Gain reference: gain applied to signal in dB
    - db Gain measured: gain applied to signal in dB
    - apply_gain: include gain-adjusted signals in attenuation calculation
    - density of specimen in kg/m3


    Returns:
    - Time of Flight (same unit as time array)
    - Attenuation (dB)
    - Reference Pulse Max Point (coordinate [time,value])
    - Measured Pulse Max point (coordinate [time,value])
    - Time array : sliced time to start at the refernce pulse
    - Reference: sliced and cleaned reference wave
    - Measured: sliced ad cleaned measured wave
    - SNR Before: SNR of raw wave
    - SNR After: SNR of clean wave
    """

    if time.ndim != 1 or reference.ndim != 1 or measured.ndim != 1:
        raise ValueError("Time, reference, and measured signals must be one-dimensional")
    if not (len(time) == len(reference) == len(measured)) or len(time) < 2:
        raise ValueError(
            "Time, reference, and measured signals must have equal lengths of at least 2"
        )
    if not (
        np.all(np.isfinite(time))
        and np.all(np.isfinite(reference))
        and np.all(np.isfinite(measured))
    ):
        raise ValueError("Time and signal arrays must contain only finite values")
    
    # Get pulse starts
    hilbert_ref = np.abs(signal.hilbert(reference))#type: ignore
    hilbert_measured = np.abs(signal.hilbert(measured)) #type: ignore
    
    ref_start,_=get_max_band(hilbert_ref,cutoff_percentage)
    measured_start,_=get_max_band(hilbert_measured,cutoff_percentage)

    ref_start = max(ref_start - time_cutoff_expander, 0)
    measured_start = max(measured_start - time_cutoff_expander, 0)

    measured_dist=np.argmax(measured)-measured_start

    # # Slice everything to start at ref start
    # time=time[ref_start:]
    # reference=reference[ref_start:]
    # measured=measured[ref_start:]

    # Reset start idxs, start with measured before resetting ref to 0, everything shifts left on time axis
    measured_start=measured_start-ref_start
    ref_start=0
    if measured_start < 0:
        raise ValueError("Measured pulse begins before the reference pulse")

    measured_end = min(int(np.argmax(measured)+measured_dist), len(measured))
    if measured_end <= measured_start:
        raise ValueError("Unable to determine valid measured pulse bounds")

    minimum_frequency, max_frequency, spacing = get_frequency_spectra_range(
        time, reference, cutoff_percentage, frequency_cutoff_expander, min_cutoff_frequency
    )

    SNR_before=SNR(measured,start=measured_start,end=int(measured_end))

    filtered_reference = band_pass_filter(reference, minimum_frequency, max_frequency, spacing)
    filtered_measured = band_pass_filter(measured, minimum_frequency, max_frequency, spacing)

    SNR_after=SNR(filtered_measured,start=measured_start,end=int(measured_end))

    ref_gain_factor = get_gain_factor("out", ref_db_gain)
    measured_gain_factor = get_gain_factor("out", measured_db_gain)
    gained_reference = filtered_reference * ref_gain_factor
    gained_measured = filtered_measured * measured_gain_factor

    attenuation_reference = gained_reference if apply_gain else filtered_reference
    attenuation_measured = gained_measured if apply_gain else filtered_measured
    
    tof, attenuation = get_ultrasonic_characteristics(
        time,
        attenuation_reference,
        attenuation_measured,
        minimum_frequency,
        max_frequency,
        spacing,
    )

    ref_peak_idx=np.argmax(gained_reference)
    measured_peak_idx=np.argmax(gained_measured)

    # Get Peak Coords
    ref_peak_coord=tuple([time[ref_peak_idx],gained_reference[ref_peak_idx]])
    measured_peak_coord=tuple([time[measured_peak_idx],gained_measured[measured_peak_idx]])

    if tof == 0:
        raise ValueError("Time of Flight measured as 0, this will result in zero division, ensure refernce and measured wave are not the same")
    
    return float(tof), float(attenuation), ref_peak_coord, measured_peak_coord, time, gained_reference, gained_measured, SNR_before, SNR_after
