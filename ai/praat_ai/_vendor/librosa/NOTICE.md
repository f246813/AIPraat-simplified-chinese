# librosa Hz-to-semitone adaptation

Upstream https://github.com/librosa/librosa, commit
`08e2c0a20d650fe00f10aaf3a1eaa9698ac19641`,
`librosa/core/convert.py`, `hz_to_midi`.

The unchanged upstream ISC license/copyright text is in LICENSE.md.
Only the two-line numeric implementation is retained; annotations, overloads,
documentation, and all other librosa functionality/dependencies are omitted.

Baseline selection, missing/out-of-range/octave-jump masking, subtraction of
the frozen MIDI baseline, and experimental observations are this project's
work, not a speaker-calibration facility provided by librosa.
