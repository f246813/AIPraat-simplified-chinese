"""Small ISC-licensed numerical adaptation; see NOTICE.md and LICENSE.md."""
import numpy as np


def hz_to_midi(frequencies):
    midi = 12 * (np.log2(np.asanyarray(frequencies)) - np.log2(440.0)) + 69
    return midi[()]
