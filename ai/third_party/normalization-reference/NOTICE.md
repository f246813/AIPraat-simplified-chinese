# Development-only normalization references

These files are not imported by the application, not part of the pip runtime
requirements, and are not included by the installer resource list.

- `vowspace-normalization.py`: unchanged `vowspace/core/normalization.py` from
  VowSpace commit `3143f636c56cb34e26cc83da0f425494bb3f9c94` (Ali Çağan Kaya).
  GPL-3.0; full upstream license at
  `../../praat_ai/_vendor/vowspace/LICENSE.md`. Adaptation notes in its NOTICE.md.
- `phonR.R`: unchanged `R/phonR.R` from Daniel R. McCloy's phonR commit
  `c6d419f187f25d2be315cc4cdc35b7ad2b8ce6ee`. `phonR-DESCRIPTION` is the unchanged
  package metadata declaring GPL-3. The same GPL v3 full text above applies.
  Only `normLobanov` (R `scale`) is used for numerical comparison. No R runtime
  is installed or required by the application.

`ai/tests/verify_normalization_upstream.py` executes the actual VowSpace
function if development pandas is present, and actual phonR `normLobanov` if
Rscript is present. It explicitly reports unavailable reference runtimes;
Python invariance tests are not represented as an actual upstream R execution.
