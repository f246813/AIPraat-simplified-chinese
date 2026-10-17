# VowSpace Lobanov adaptation

Upstream: https://github.com/alicagank/VowSpace
Commit: `3143f636c56cb34e26cc83da0f425494bb3f9c94`
Original file: `vowspace/core/normalization.py`, `lobanov_normalization` (lines 11–19).
License: GNU GPL version 3; upstream's complete, unchanged `LICENSE.md` is alongside this file.
Upstream project author: Ali Çağan Kaya (alicagank; per upstream pyproject.toml). No additional copyright
notice occurs in the selected source; its original attribution comment is:

> Cite: Remirez, Emily. 2022, October 20. Vowel plotting in Python. Linguistics Methods Hub. (https://lingmethodshub.github.io/content/python/vowel-plotting-py). doi: 10.5281/zenodo.7232005

## Local changes

- Adapt the grouped pandas `(x - x.mean()) / x.std()` to NumPy arrays.
- Separate fitting (`ddof=1`, sample SD) from frozen-parameter transformation.
- Reject insufficient/nonfinite fitting samples and zero/nonfinite SD.
- Do not import pandas, PyQt5 or the complete VowSpace application at runtime.
- Selection of independent materials, balanced vowel tokens, median windows,
  missing-frame masks, paired applicability, and task snapshots are local work
  in `praat_ai/normalization.py`, not capabilities attributed to VowSpace.
- Applying vowel-calibration parameters to approximants is a local experimental
  extension, not an upstream-validated pronunciation classifier.

The unchanged upstream source used for development comparison is preserved at
`ai/third_party/normalization-reference/vowspace-normalization.py`.
