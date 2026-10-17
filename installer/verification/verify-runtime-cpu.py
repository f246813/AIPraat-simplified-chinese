"""Actual project wav2vec2 CPU inference, using a locally generated tiny model."""
from pathlib import Path
import json
import os
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"ai"))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
import torch
from transformers import Wav2Vec2Config, Wav2Vec2ForCTC, Wav2Vec2CTCTokenizer, Wav2Vec2FeatureExtractor, Wav2Vec2Processor
from praat_ai.config import Wav2Vec2AlignmentConfig
from praat_ai.forced_alignment import Wav2Vec2Aligner

with tempfile.TemporaryDirectory(prefix="AIPraat-cpu-model-") as directory:
    folder=Path(directory)
    (folder/"vocab.json").write_text(json.dumps({"<pad>":0,"<unk>":1,"|":2,"A":3,"B":4}),encoding="utf-8")
    tokenizer=Wav2Vec2CTCTokenizer(str(folder/"vocab.json"),unk_token="<unk>",pad_token="<pad>",word_delimiter_token="|")
    feature=Wav2Vec2FeatureExtractor(feature_size=1,sampling_rate=16000,padding_value=0.0,do_normalize=True,return_attention_mask=False)
    Wav2Vec2Processor(feature,tokenizer).save_pretrained(directory)
    config=Wav2Vec2Config(vocab_size=5,hidden_size=8,num_hidden_layers=1,num_attention_heads=2,intermediate_size=16,
        conv_dim=(8,8,8),conv_stride=(5,2,2),conv_kernel=(10,3,3),num_conv_pos_embedding_groups=2,num_conv_pos_embeddings=8,mask_time_prob=0.0)
    Wav2Vec2ForCTC(config).save_pretrained(directory)
    runtime=Wav2Vec2AlignmentConfig(enabled=True,model=directory)
    assert runtime.device=="cpu"
    aligner=Wav2Vec2Aligner(runtime)
    assert aligner.available()
    aligner._load()
    with torch.inference_mode():
        logits=aligner._model(torch.zeros((1,1600))).logits
    assert logits.device.type=="cpu" and logits.shape[-1]==5 and torch.isfinite(logits).all()
    print("PASS project Wav2Vec2Aligner CPU inference; logits",tuple(logits.shape),"; no model download")
