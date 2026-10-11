---
language:
- en
pipeline_tag: other
library_name: montreal-forced-aligner
tags:
- montreal-forced-aligner
- forced-alignment
license: cc-by-4.0
---
# Model Card for english_us_arpa

<!-- Provide a quick summary of what the model is/does. -->

This MFA model is for aligning US English using the ARPAbet phone set.

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
- **Language(s) (NLP):** English
- **License:** cc-by-4.0

## Uses

<!-- Address questions around how the model is intended to be used, including the foreseeable users of the model and those affected by the model. -->

### Direct Use

<!-- This section is for the model use without fine-tuning or plugging into a larger ecosystem/app. -->

This model is intended to be used for forced alignment of US English. Other varieties of English (e.g. UK, Indian, Nigerian Englishes) may have inconsistent performance.  Please see https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/troubleshooting.html for details on common fixes.

### Out-of-Scope Use

<!-- This section addresses misuse, malicious use, and uses that the model will not work well for. -->

This model cannot provide accurate assessments of goodness of pronunciations or provide transcripts as it is trained to be accepting of variation in pronunciation to provide a reasonable alignment of US English.

## Bias, Risks, and Limitations

<!-- This section is meant to convey both technical and sociotechnical limitations. -->

This model will perform best on the variety of speech that it was trained on, with a bias towards US English.  The speakers in the training data are all adult speakers, so child speech alignment may not be accurate.

### Recommendations

<!-- This section is meant to convey recommendations with respect to the bias, risk, and technical limitations. -->

When using this model on a variety that it was not trained on, better results can be attained by adapting the model to the data to be aligned first.  See https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/workflows/adapt_acoustic_model.html and https://github.com/mmcauliffe/mfa-adaptation for example usage and scripts.

## How to Get Started with the Model

Use the code below to get started with the model.

To get started, follow the instructions for [installing MFA](https://montreal-forced-aligner.readthedocs.io/en/latest/getting_started.html). To align files using this model, use the [mfa align](https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/workflows/alignment.html) command.

## Dictionary Details

#### Details for english_us_arpa dictionary and G2P model

- **Source:** wikipron
- **Orthography:** Latin
- **Phone set:** ARPA
- **Words:** 266,823
* **Phones:** 39
* **Graphemes:** 35

##### IPA chart

###### Consonants

| Manner | Labial | Labiodental | Dental | Alveolar | Alveopalatal | Palatal | Velar | Glottal |
| :----: | :----: | :----: | :----: | :----: | :----: | :----: | :----: | :----: |
| **Nasal** | M |  |  | N |  |  | NG | |
| **Stop** | P     B |  |  | T     D |  |  | K     G | |
| **Affricate** |  |  |  |  | CH     JH |  |  | |
| **Sibilant** |  |  |  | S     Z | SH     ZH |  |  | |
| **Fricative** |  | F     V | TH     DH |  |  |  |  | HH|
| **Approximant** | W |  |  | R |  | Y |  | |
| **Lateral** |  |  |  | L |  |  |  |  |

###### Vowels

|  | Front | Near-Front | Central | Near-Back | Back |
| :----: | :----: | :----: | :----: | :----: | :----: |
| **Close** | IY |  | UW |  | |
|  |  | IH |  | UH | |
| **Close-Mid** | EY |  |  |  | OW|
|  |  |  | AH |  | |
| **Open-Mid** | EH |  |  |  | |
|  | AE |  | ER |  | |
| **Open** |  |  |  |  | AA     AO |

##### Diphthongs
* OY
* AY
* AW


## Training Details

### Training Data

<!-- This should link to a Dataset Card, perhaps with a short stub of information on what the training data is all about as well as documentation related to data pre-processing or additional filtering. -->

#### LibriSpeech English

- **Source:** https://openslr.org/12/
- **License:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- **Dialects:** us
- **Number of hours:** 982.10
- **Number of utterances:** 292,367
- **Number of speakers:** 2,484
  - **Female speakers:** 1,283
  - **Male speakers:** 1,201
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
@techreport{mfa_english_us_arpa_acoustic_2026,
	author={McAuliffe, Michael and Sonderegger, Morgan},
	title={English (US) ARPA acoustic model v3.3.0},
	address={\url{https://huggingface.co/MontrealCorpusTools/english_us_arpa}},
	year={2026},
	month={Jun},
}
```

**APA:**

```
McAuliffe, M. & Sonderegger, M. (2026). English (US) ARPA acoustic model v3.3.0. Available at https://huggingface.co/MontrealCorpusTools/english_us_arpa.
```

## Contact

For questions and issues, please file an issue either for this model at https://huggingface.co/MontrealCorpusTools/english_us_arpa/discussions or for larger MFA issues at https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner/issues.