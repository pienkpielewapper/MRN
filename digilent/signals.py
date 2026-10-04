import numpy as np
from numpy import ndarray
import matplotlib.pyplot as plt
import time
def generate_pulse(center_frequency: float,
                   number_of_cycles: int) -> tuple[np.ndarray, np.ndarray, float]:

    fc = center_frequency
    cycles = number_of_cycles

    fs = fc * 20
    dt = 1 / fs

    pulse_length = cycles / fc

    # Centre the Gaussian in the waveform
    t = np.arange(0, 2 * pulse_length, dt)
    t0 = pulse_length

    # Gaussian envelope
    sigma = pulse_length / 3
    envelope = np.exp(-((t - t0) ** 2) / (2 * sigma ** 2))

    # Carrier
    pulse = envelope * np.sin(2 * np.pi * fc * t)

    waveform_frequency = fs / len(t)

    return t, pulse, waveform_frequency

    
