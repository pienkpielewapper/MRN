import numpy as np
from numpy import ndarray
import matplotlib.pyplot as plt

def generate_pulse(center_frequency: float, number_of_cycles: int) -> tuple[np.ndarray, np.ndarray]:
    # --- Pulse parameters ---
    fc = center_frequency              # center frequency (10 MHz probe)
    cycles = number_of_cycles          # number of cycles in the pulse
    fs = fc*20             # sampling rate (500 MHz)
    dt = 1/fs

    # --- Time vector ---
    pulse_length = cycles / fc
    t = np.arange(0, 4*pulse_length, dt)

    # --- Gaussian envelope ---
    sigma = pulse_length / number_of_cycles
    envelope = np.exp(-((t - pulse_length)**2) / (2 * sigma**2))

    # --- Broadband pulse ---
    pulse = envelope * np.sin(2 * np.pi * fc * t)

    return t, pulse

    
