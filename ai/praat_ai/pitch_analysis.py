"""Choose Praat analysis methods by acoustic purpose, independently of F0 bounds."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PitchMethod:
    name: str
    purpose: str
    silence_threshold: float


SPEECH = PitchMethod('filtered_autocorrelation', 'speech_f0', .09)
PREVOICING = PitchMethod('filtered_autocorrelation', 'stop_voicing', .01)
PERIODICITY = PitchMethod('raw_autocorrelation', 'mathematical_periodicity', .03)
VOICE = PitchMethod('raw_cross_correlation', 'voice_analysis', .03)
METHODS = {m.purpose: m for m in (SPEECH, PREVOICING, PERIODICITY, VOICE)}


def settings(floor, ceiling, *, purpose='speech_f0'):
    method = METHODS[purpose]
    return dict(method=method.name, purpose=purpose, floor_hz=float(floor),
                ceiling_hz=float(ceiling), silence_threshold=method.silence_threshold)


def command(floor, ceiling, *, purpose='speech_f0', step=0):
    method = METHODS[purpose]
    if method.name == 'filtered_autocorrelation':
        return (f'To Pitch (filtered autocorrelation): {step:g}, {floor:.6f}, {ceiling:.6f}, '
                f'15, "no", 0.03, {method.silence_threshold:g}, 0.50, 0.055, 0.35, 0.14')
    name = 'raw autocorrelation' if method is PERIODICITY else 'raw cross-correlation'
    return (f'To Pitch ({name}): {step:g}, {floor:.6f}, {ceiling:.6f}, 15, "no", '
            f'{method.silence_threshold:g}, 0.45, 0.01, 0.35, 0.14')


def parselmouth_pitch(sound, floor, ceiling, *, step=.01, purpose='speech_f0'):
    """Apply Praat's Gaussian prefilter with older embedded Praat engines.

    The filter follows fon/Sound_to_Pitch.cpp::Sound_to_Pitch_filteredAc:
    attenuation(f) = attenuation_at_top ** (f / pitch_top) ** 2.
    Each channel is filtered independently, preserving the Sound time domain.
    """
    import numpy as np
    import parselmouth
    method = METHODS[purpose]
    if method is PERIODICITY or method is VOICE:
        converter = sound.to_pitch_ac if method is PERIODICITY else sound.to_pitch_cc
        return converter(time_step=step, pitch_floor=floor, pitch_ceiling=ceiling,
            max_number_of_candidates=15, very_accurate=False,
            silence_threshold=method.silence_threshold, voicing_threshold=.45,
            octave_cost=.01, octave_jump_cost=.35, voiced_unvoiced_cost=.14)
    filtered = sound.copy()
    for index in range(sound.n_channels):
        channel = sound.extract_channel(index + 1)
        spectrum = parselmouth.praat.call(channel, 'To Spectrum', 'yes')
        spectrum.values[:] *= np.exp(np.log(.03) * (spectrum.xs() / ceiling) ** 2)
        restored = parselmouth.praat.call(spectrum, 'To Sound')
        filtered.values[index, :] = restored.values[0, :sound.n_samples]
    return filtered.to_pitch_ac(time_step=step, pitch_floor=floor, pitch_ceiling=ceiling,
        max_number_of_candidates=15, very_accurate=False, silence_threshold=method.silence_threshold,
        voicing_threshold=.50, octave_cost=.055, octave_jump_cost=.35, voiced_unvoiced_cost=.14)
