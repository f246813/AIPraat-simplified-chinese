---
language:
- ja
pipeline_tag: other
library_name: montreal-forced-aligner
tags:
- montreal-forced-aligner
- forced-alignment
license: cc-by-4.0
---
# Model Card for japanese_mfa

<!-- Provide a quick summary of what the model is/does. -->

This MFA model is for aligning Japanese speech.

- [Model details](#model-details)
- [Uses](#uses)
- [Performance Factors](#how-to-get-started-with-the-model)
- [Dictionary Details](#dictionary-details)
- [Training Details](#training-details)
- [Evaluation](#evaluation)
- [Contact](#contact)

## Model Details

### Model Description

<!-- Provide a longer summary of what this model is. -->



- **Developed by:** Michael McAuliffe
- **Funded by:** N/A
- **Model type:** Montreal Forced Aligner model
- **Language(s) (NLP):** Japanese
- **License:** cc-by-4.0

## Uses

<!-- Address questions around how the model is intended to be used, including the foreseeable users of the model and those affected by the model. -->

### Direct Use

<!-- This section is for the model use without fine-tuning or plugging into a larger ecosystem/app. -->

This model is intended to be used for forced alignment of Japanese speech.  Please see https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/troubleshooting.html for details on common fixes.

### Out-of-Scope Use

<!-- This section addresses misuse, malicious use, and uses that the model will not work well for. -->

This model cannot provide accurate assessments of goodness of pronunciations or provide transcripts as it is trained to be accepting of variation in pronunciation to provide a reasonable alignment for Japanese speech.

## Bias, Risks, and Limitations

<!-- This section is meant to convey both technical and sociotechnical limitations. -->

This model will perform best on the variety of speech that it was trained on.  The speakers in the training data are all adult speakers, so child speech alignment may not be accurate.

### Recommendations

<!-- This section is meant to convey recommendations with respect to the bias, risk, and technical limitations. -->

When using this model on a variety that it was not trained on, better results can be attained by adapting the model to the data to be aligned first.  See https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/workflows/adapt_acoustic_model.html and https://github.com/mmcauliffe/mfa-adaptation for example usage and scripts.

## How to Get Started with the Model

Use the code below to get started with the model.

To get started, follow the instructions for [installing MFA](https://montreal-forced-aligner.readthedocs.io/en/latest/getting_started.html). To align files using this model, use the [mfa align](https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/workflows/alignment.html) command.

## Dictionary Details

#### Details for japanese_mfa dictionary and G2P model

- **Source:** wikipron
- **Orthography:** Kanji and kana
- **Phone set:** MFA
- **Words:** 2,239,777
* **Phones:** 78
* **Graphemes:** 5,752

##### IPA chart

###### Consonants

| Manner | Labial | Labiodental | Alveolar | Palatal | Velar | Uvular | Glottal |
| :----: | :----: | :----: | :----: | :----: | :----: | :----: | :----: |
| **Nasal** | m mː |  | n nː | ɲ ɲː | ŋ | ɴ ɴː | |
| Palatalized | mʲ mʲː |  |  |  |  |  | |
| **Stop** | p pː     b bː |  | t tː     d dː | c cː     ɟ ɟː | k kː     ɡ ɡː |  | ʔ|
| Palatalized | pʲ pʲː     bʲ |  | tʲ tʲː     dʲ dʲː |  |  |  | |
| **Affricate** |  |  | ts tsː     dz dzː | tɕ tɕː     dʑ dʑː |  |  | |
| **Sibilant** |  |  | s sː     z | ɕ ɕː     ʑ |  |  | |
| **Fricative** | ɸ ɸː | v |  | ç |  |  | h hː|
| Palatalized | ɸʲ ɸʲː | vʲ |  |  |  |  | |
| **Approximant** | w |  |  | j | ɰ̃ |  | |
| **Tap** |  |  | ɾ |  |  |  | |
| Palatalized |  |  | ɾʲ |  |  |  |  |

###### Vowels

|  | Front | Near-Front | Central | Near-Back | Back |
| :----: | :----: | :----: | :----: | :----: | :----: |
| **Close** | i iː i̥ |  | ɨ ɨː ɨ̥ |  | ɯ ɯː ɯ̥|
|  |  |  |  |  | |
| **Close-Mid** | e eː |  |  |  | o oː|
|  |  |  |  |  | |
| **Open-Mid** |  |  |  |  | |
|  |  |  |  |  | |
| **Open** |  |  | a aː |  |  |

## Training Details

### Training Data

<!-- This should link to a Dataset Card, perhaps with a short stub of information on what the training data is all about as well as documentation related to data pre-processing or additional filtering. -->

#### Common Voice Japanese

- **Source:** https://voice.mozilla.org/en/datasets
- **License:** [CC-0](https://creativecommons.org/publicdomain/zero/1.0/)
- **Dialects:** N/A
- **Number of hours:** 71.54
- **Number of utterances:** 56,631
- **Number of speakers:** 1,364
  - **Female speakers:** 199
  - **Male speakers:** 459
  - **Unknown speakers:** 706


#### GlobalPhone Japanese

- **Source:** https://catalogue.elra.info/en-us/repository/browse/ELRA-S0199/
- **License:** [ELRA](https://www.elra.info/en/services-around-lrs/distribution/licensing/)
- **Dialects:** N/A
- **Number of hours:** 33.88
- **Number of utterances:** 13,103
- **Number of speakers:** 144
  - **Female speakers:** 44
  - **Male speakers:** 104
  - **Unknown speakers:** 0


#### Japanese Versatile Speech

- **Source:** https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_corpus
- **License:** [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
- **Dialects:** N/A
- **Number of hours:** 30.25
- **Number of utterances:** 14,994
- **Number of speakers:** 100
  - **Female speakers:** 51
  - **Male speakers:** 49
  - **Unknown speakers:** 100


#### LaboroTV Japanese

- **Source:** https://laboro.ai/activity/column/engineer/eg-laboro-tv-corpus-jp/
- **License:** [LaboroTV Non-commercial](https://laboro.ai/activity/column/engineer/eg-laboro-tv-corpus-jp/)
- **Dialects:** N/A
- **Number of hours:** 1,915.43
- **Number of utterances:** 1,536,227
- **Number of speakers:** 305,246
  - **Female speakers:** 0
  - **Male speakers:** 0
  - **Unknown speakers:** 305,246


#### MagicData ASR-SJapDuSC

- **Source:** https://magichub.com/datasets/japanese-scripted-speech-corpus-daily-use-sentence/
- **License:** CC-BY-NC-ND 4.0
- **Dialects:** N/A
- **Number of hours:** 18.71
- **Number of utterances:** 17,302
- **Number of speakers:** 37
  - **Female speakers:** 19
  - **Male speakers:** 19
  - **Unknown speakers:** 0


#### Microsoft Speech Language Translation Japanese

- **Source:** https://msropendata.com/datasets/54813518-4ea6-4c39-9bb2-b0d1e5f0c187
- **License:** [Microsoft Research Data License](https://msropendata-web-api.azurewebsites.net/licenses/2f933be3-284d-500b-7ea3-2aa2fd0f1bb2/view)
- **Dialects:** N/A
- **Number of hours:** 9.85
- **Number of utterances:** 7,338
- **Number of speakers:** 112
  - **Female speakers:** 0
  - **Male speakers:** 0
  - **Unknown speakers:** 112


#### TEDxJP-10K

- **Source:** https://github.com/laboroai/TEDxJP-10K
- **License:** [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- **Dialects:** N/A
- **Number of hours:** 8.85
- **Number of utterances:** 9,962
- **Number of speakers:** 271
  - **Female speakers:** 0
  - **Male speakers:** 0
  - **Unknown speakers:** 271


### Training Procedure

<!-- This relates heavily to the Technical Specifications. Content here should link to that section when it is relevant to the training procedure. -->

#### Preprocessing

Preprocessing include fixes and orthographic standardization to various corpora.


#### Training Hyperparameters

- **Training regime:** [Training configuration](config.yaml)

## Evaluation

<!-- This section describes the evaluation protocols and provides the results. -->

### Testing Data, Factors & Metrics

#### Testing Data

<!-- This should link to a Dataset Card if possible. -->

N/A

#### Factors

<!-- These are the things the evaluation is disaggregating by, e.g., subpopulations or domains. -->

N/A

#### Metrics

<!-- These are the evaluation metrics being used, ideally with a description of why. -->

N/A

### Results

N/A

#### Summary



## Technical Specifications

### Model Architecture and Objective

HMM-GMM model

#### Software

This model was trained via the [Montreal Forced Aligner](https://montreal-forced-aligner.readthedocs.io/).

## Citation

<!-- If there is a paper or blog post introducing the model, the APA and Bibtex information for that should go in this section. -->

**BibTeX:**

```
@techreport{mfa_japanese_mfa_acoustic_2026,
	author={McAuliffe, Michael and Sonderegger, Morgan},
	title={Japanese MFA acoustic model v3.3.0},
	address={\url{https://huggingface.co/MontrealCorpusTools/japanese_mfa}},
	year={2026},
	month={Jun},
}
```

**APA:**

```
McAuliffe, M. & Sonderegger, M. (2026). Japanese MFA acoustic model v3.3.0. Available at https://huggingface.co/MontrealCorpusTools/japanese_mfa.
```

## Contact

For questions and issues, please file an issue either for this model at https://huggingface.co/MontrealCorpusTools/japanese_mfa/discussions or for larger MFA issues at https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner/issues.