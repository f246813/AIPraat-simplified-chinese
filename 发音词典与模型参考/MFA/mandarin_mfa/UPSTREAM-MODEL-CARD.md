---
language:
- zh
pipeline_tag: other
library_name: montreal-forced-aligner
tags:
- montreal-forced-aligner
- forced-alignment
license: cc-by-4.0
---
# Model Card for mandarin_mfa

<!-- Provide a quick summary of what the model is/does. -->

This MFA model is for aligning Mandarin speech.

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
- **Language(s) (NLP):** Mandarin
- **License:** cc-by-4.0

## Uses

<!-- Address questions around how the model is intended to be used, including the foreseeable users of the model and those affected by the model. -->

### Direct Use

<!-- This section is for the model use without fine-tuning or plugging into a larger ecosystem/app. -->

This model is intended to be used for forced alignment of Mandarin speech.  Please see https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/troubleshooting.html for details on common fixes. If using Pinyin transcripts, use the G2P models either directly or via the `--use_g2p --language unknown` flags.  Otherwise, MFA will assume Chinese characters and use a tokenizer to split up words and generate pronunciations for unknown words based on their Pinyin conversion.

### Out-of-Scope Use

<!-- This section addresses misuse, malicious use, and uses that the model will not work well for. -->

This model cannot provide accurate assessments of goodness of pronunciations or provide transcripts as it is trained to be accepting of variation in pronunciation to provide a reasonable alignment for Mandarin speech.

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

#### Details for mandarin_taiwan_mfa dictionary and G2P model

- **Source:** wikipron
- **Orthography:** Hanzi and Pinyin
- **Phone set:** MFA
- **Words:** 156,093
* **Phones:** 51
* **Graphemes:** 18,170

##### IPA chart

###### Consonants

| Manner | Labial | Labiodental | Alveolar | Retroflex | Palatal | Velar | Glottal |
| :----: | :----: | :----: | :----: | :----: | :----: | :----: | :----: |
| **Nasal** | m |  | n n̩ |  | ɲ | ŋ ŋ̍ | |
| Palatalized | mʲ |  |  |  |  |  | |
| **Stop** | p |  | t |  |  | k | ʔ|
| Aspirated | pʰ |  | tʰ |  |  | kʰ | |
| Labialized | pʷ |  | tʷ |  |  | kʷ | |
| Palatalized | pʲ |  | tʲ |  |  |  | |
| **Affricate** |  |  | ts | ʈʂ | tɕ |  | |
| Aspirated |  |  | tsʰ | ʈʂʰ | tɕʰ |  | |
| Labialized |  |  |  |  | tɕʷ |  | |
| **Sibilant** |  |  | s     z̩ | ʂ     ʐ ʐ̩ | ɕ |  | |
| Labialized |  |  |  |  | ɕʷ |  | |
| **Fricative** |  | f |  |  |  |  | |
| **Approximant** | w |  |  | ɻ | j ɥ |  | |
| **Lateral** |  |  | l |  | ʎ |  |  |

###### Vowels

|  | Front | Near-Front | Central | Near-Back | Back |
| :----: | :----: | :----: | :----: | :----: | :----: |
| **Close** | i     y |  |  |  | u|
|  |  |  |  |  | |
| **Close-Mid** | e ej |  |  |  | o ow|
|  |  |  | ə |  | |
| **Open-Mid** |  |  |  |  | |
|  |  |  |  |  | |
| **Open** |  |  | a |  |  |

##### Diphthongs
* aw
* aj
* ej
* ow


#### Details for mandarin_china_mfa dictionary and G2P model

- **Source:** wikipron
- **Orthography:** Hanzi and Pinyin
- **Phone set:** MFA
- **Words:** 286,218
* **Phones:** 52
* **Graphemes:** 18,043

##### IPA chart

###### Consonants

| Manner | Labial | Labiodental | Alveolar | Retroflex | Palatal | Velar | Glottal |
| :----: | :----: | :----: | :----: | :----: | :----: | :----: | :----: |
| **Nasal** | m m̩ |  | n n̩ |  | ɲ | ŋ ŋ̍ | |
| Palatalized | mʲ |  |  |  |  |  | |
| **Stop** | p |  | t |  |  | k | ʔ|
| Aspirated | pʰ |  | tʰ |  |  | kʰ | |
| Labialized | pʷ |  | tʷ |  |  | kʷ | |
| Palatalized | pʲ |  | tʲ |  |  |  | |
| **Affricate** |  |  | ts | ʈʂ | tɕ |  | |
| Aspirated |  |  | tsʰ | ʈʂʰ | tɕʰ |  | |
| Labialized |  |  |  |  | tɕʷ |  | |
| **Sibilant** |  |  | s     z̩ | ʂ     ʐ ʐ̩ | ɕ |  | |
| Labialized |  |  |  |  | ɕʷ |  | |
| **Fricative** |  | f |  |  |  |  | |
| **Approximant** | w |  |  | ɻ | j ɥ |  | |
| **Lateral** |  |  | l |  | ʎ |  |  |

###### Vowels

|  | Front | Near-Front | Central | Near-Back | Back |
| :----: | :----: | :----: | :----: | :----: | :----: |
| **Close** | i     y |  |  |  | u|
|  |  |  |  |  | |
| **Close-Mid** | e ej |  |  |  | o ow|
|  |  |  | ə |  | |
| **Open-Mid** |  |  |  |  | |
|  |  |  |  |  | |
| **Open** |  |  | a |  |  |

##### Diphthongs
* aw
* aj
* ej
* ow


## Training Details

### Training Data

<!-- This should link to a Dataset Card, perhaps with a short stub of information on what the training data is all about as well as documentation related to data pre-processing or additional filtering. -->

#### AI-DataTang Corpus

- **Source:** https://openslr.org/62/
- **License:** [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/)
- **Dialects:** china, erhua
- **Number of hours:** 200.38
- **Number of utterances:** 237,264
- **Number of speakers:** 600
  - **Female speakers:** 0
  - **Male speakers:** 0
  - **Unknown speakers:** 420

#### Common Voice Chinese (China)

- **Source:** https://voice.mozilla.org/en/datasets
- **License:** [CC-0](https://creativecommons.org/publicdomain/zero/1.0/)
- **Dialects:** china, erhua
- **Number of hours:** 238.57
- **Number of utterances:** 169,114
- **Number of speakers:** 4,462
  - **Female speakers:** 152
  - **Male speakers:** 748
  - **Unknown speakers:** 3,562

#### Common Voice Chinese (Taiwan)

- **Source:** https://voice.mozilla.org/en/datasets
- **License:** [CC-0](https://creativecommons.org/publicdomain/zero/1.0/)
- **Dialects:** taiwan
- **Number of hours:** 72.75
- **Number of utterances:** 82,137
- **Number of speakers:** 1,706
  - **Female speakers:** 191
  - **Male speakers:** 458
  - **Unknown speakers:** 1,054

#### AISHELL-3

- **Source:** https://openslr.org/93/
- **License:** [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- **Dialects:** china, erhua
- **Number of hours:** 160.88
- **Number of utterances:** 127,274
- **Number of speakers:** 360
  - **Female speakers:** 0
  - **Male speakers:** 0
  - **Unknown speakers:** 360

#### THCHS-30

- **Source:** https://openslr.org/18/
- **License:** [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- **Dialects:** china, erhua
- **Number of hours:** 34.16
- **Number of utterances:** 13,387
- **Number of speakers:** 60
  - **Female speakers:** 0
  - **Male speakers:** 0
  - **Unknown speakers:** 60

#### GlobalPhone Chinese-Mandarin

- **Source:** https://catalogue.elra.info/en-us/repository/browse/ELRA-S0193/
- **License:** [ELRA](https://www.elra.info/en/services-around-lrs/distribution/licensing/)
- **Dialects:** china, erhua
- **Number of hours:** 31.09
- **Number of utterances:** 10,225
- **Number of speakers:** 132
  - **Female speakers:** 68
  - **Male speakers:** 64
  - **Unknown speakers:** 0

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
@techreport{mfa_mandarin_mfa_acoustic_2026,
	author={McAuliffe, Michael and Sonderegger, Morgan},
	title={Mandarin MFA acoustic model v3.3.0},
	address={\url{https://huggingface.co/MontrealCorpusTools/mandarin_mfa}},
	year={2026},
	month={Jun},
}
```

**APA:**

```
McAuliffe, M. & Sonderegger, M. (2026). Mandarin MFA acoustic model v3.3.0. Available at https://huggingface.co/MontrealCorpusTools/mandarin_mfa.
```

## Contact

For questions and issues, please file an issue either for this model at https://huggingface.co/MontrealCorpusTools/mandarin_mfa/discussions or for larger MFA issues at https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner/issues.