# PanPhon

Upstream: https://github.com/dmort27/panphon
Pinned revision: `a49c242d3307864312a2701055faefa4e548501c`.
License: MIT, copyright Carnegie Mellon University; see `LICENSE`.

`ipa_all.csv` is copied verbatim from `panphon/data/ipa_all.csv` (line endings normalized).
`__init__.py` copies FeatureTable's `normalize`, `_build_seg_trie`,
`longest_one_seg_prefix` and `_segs` from `panphon/featuretable.py`.
The constructor replaces pandas/Segment with stdlib CSV and raw feature dictionaries.
The small `segments` adapter rejects unknown symbols instead of dropping them.
No phonological feature inventory is invented by AIPraat.

The language-specific acoustic rule selection and relative duration adapter are
AIPraat additions. PanPhon supplies phonetic features, not pronunciation error
thresholds or evidence that these rules are clinically/pedagogically validated.

OpenPronounce `74bc17ea406e6f057f15eea0e70951e1931af868` was inspected:
https://github.com/Halleck45/OpenPronounce/blob/74bc17ea406e6f057f15eea0e70951e1931af868/openpronounce/phones.py
Its recognizer/sequence comparison was not copied in this increment. Its global
length removal and English-specific mergers are unsuitable for Japanese length
contrasts; both length and devoicing markers are retained here.
