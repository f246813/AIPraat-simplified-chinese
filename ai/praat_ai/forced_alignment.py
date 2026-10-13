from __future__ import annotations

import importlib.util
import math
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .audio import read_wav
from .config import AlignmentConfig, MfaAlignmentConfig, Wav2Vec2AlignmentConfig
from .models import AlignedPhone, AlignmentResult, PhoneSpec


class AlignmentError(RuntimeError):
    pass


class AlignmentBackend(ABC):
    name = "base"

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def align(
        self,
        audio_path: str | Path,
        phones: list[PhoneSpec],
        language: str,
        transcript: str = "",
    ) -> AlignmentResult:
        raise NotImplementedError


def _copy_as_wav(source: str | Path, destination: Path) -> None:
    source_path = Path(source)
    if source_path.suffix.lower() == ".wav":
        shutil.copy2(source_path, destination)
        return
    sound = read_wav(source_path)
    import wave

    samples = np.clip(sound.samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2")
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sound.sample_rate)
        handle.writeframes(pcm.tobytes())


def parse_mfa_textgrid(
    path: str | Path,
    phones: list[PhoneSpec],
    source: str = "mfa",
    confidence: float = 0.85,
) -> AlignmentResult:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    name_matches = list(re.finditer(r'name\s*=\s*"([^"]+)"', text))
    phone_tier_start = None
    for index, match in enumerate(name_matches):
        if match.group(1).lower() in {"phones", "phone", "phonemes", "phoneme"}:
            phone_tier_start = match.start()
            next_name = (
                name_matches[index + 1].start()
                if index + 1 < len(name_matches)
                else len(text)
            )
            tier_text = text[phone_tier_start:next_name]
            break
    if phone_tier_start is None:
        raise AlignmentError("MFA TextGrid does not contain a phone tier.")

    interval_pattern = re.compile(
        r"xmin\s*=\s*([0-9.+-eE]+)\s*"
        r"xmax\s*=\s*([0-9.+-eE]+)\s*"
        r'text\s*=\s*"((?:\\.|""|[^"])*)"',
        re.DOTALL,
    )
    intervals = [
        (float(start), float(end), label.replace('""', '"'))
        for start, end, label in interval_pattern.findall(tier_text)
    ]
    ignored = {"", "sp", "sil", "silence", "<eps>", "<unk>"}
    spoken = [item for item in intervals if item[2].strip().lower() not in ignored]
    if len(spoken) < len(phones):
        raise AlignmentError(
            f"MFA returned {len(spoken)} phones, expected {len(phones)}."
        )
    expected = [phone.ipa for phone in phones]
    labels = [item[2].strip() for item in spoken]
    matcher = SequenceMatcher(None, expected, labels, autojunk=False)
    matched = [spoken[block.b + offset] for block in matcher.get_matching_blocks()
               for offset in range(block.size)]
    if len(matched) != len(expected):
        # SequenceMatcher favors contiguous blocks. Repeated tokens may still
        # form a complete ordered subsequence across those blocks.
        matched = []
        for interval in spoken:
            if len(matched) < len(expected) and interval[2].strip() == expected[len(matched)]:
                matched.append(interval)
    if len(matched) != len(phones):
        raise AlignmentError(
            f"MFA phone sequence does not match the requested sequence "
            f"({len(matched)} ordered matches != {len(phones)} requested)."
        )
    spoken = matched

    aligned = [
        AlignedPhone(
            phone_index=index,
            ipa=phone.ipa,
            start=interval[0],
            end=interval[1],
            confidence=confidence,
            source=source,
        )
        for index, (phone, interval) in enumerate(zip(phones, spoken), start=1)
    ]
    return AlignmentResult(
        phones=aligned,
        source=source,
        confidence=confidence,
    )


class MfaAligner(AlignmentBackend):
    name = "mfa"

    def __init__(self, config: MfaAlignmentConfig):
        self.config = config

    def available(self) -> bool:
        if self.config.conda_executable and self.config.conda_environment:
            launcher_available = (
                shutil.which(self.config.conda_executable) is not None
                or Path(self.config.conda_executable).is_file()
            )
        else:
            launcher_available = shutil.which(self.config.executable) is not None
        return (
            self.config.enabled
            and launcher_available
            and bool(self.config.acoustic_model)
        )

    def _launcher(self) -> list[str]:
        if self.config.conda_executable and self.config.conda_environment:
            return [
                self.config.conda_executable,
                "run",
                "-n",
                self.config.conda_environment,
                "mfa",
            ]
        return [self.config.executable]

    def build_command(
        self,
        corpus_dir: Path,
        dictionary_path: Path,
        output_dir: Path,
    ) -> list[str]:
        return self._launcher() + [
            "align",
            str(corpus_dir),
            str(dictionary_path),
            self.config.acoustic_model,
            str(output_dir),
            "--output_format",
            "long_textgrid",
            "--single_speaker",
            "--clean",
            "--beam",
            str(self.config.beam),
            "--retry_beam",
            str(self.config.retry_beam),
        ]

    def align(
        self,
        audio_path: str | Path,
        phones: list[PhoneSpec],
        language: str,
        transcript: str = "",
    ) -> AlignmentResult:
        del language
        if not phones:
            return AlignmentResult([], self.name, 0.0)
        if not self.available():
            raise AlignmentError("MFA is not configured or not installed.")

        with tempfile.TemporaryDirectory(prefix="praat_mfa_") as directory:
            root = Path(directory)
            corpus = root / "corpus"
            output = root / "output"
            corpus.mkdir()
            output.mkdir()
            audio = corpus / "learner.wav"
            _copy_as_wav(audio_path, audio)
            if transcript and self.config.dictionary_path:
                dictionary = Path(self.config.dictionary_path)
                if not dictionary.is_file():
                    raise AlignmentError(
                        f"MFA dictionary not found: {dictionary}"
                    )
                (corpus / "learner.txt").write_text(
                    transcript.strip() + "\n",
                    encoding="utf-8",
                )
            else:
                (corpus / "learner.txt").write_text("UTT\n", encoding="utf-8")
                dictionary = root / "dictionary.txt"
                dictionary.write_text(
                    "UTT " + " ".join(phone.ipa for phone in phones) + "\n",
                    encoding="utf-8",
                )
            command = self.build_command(corpus, dictionary, output)
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip()
                raise AlignmentError(f"MFA alignment failed: {message}")
            textgrid = output / "learner.TextGrid"
            if not textgrid.is_file():
                candidates = list(output.glob("*.TextGrid"))
                if not candidates:
                    raise AlignmentError("MFA did not produce a TextGrid.")
                textgrid = candidates[0]
            return parse_mfa_textgrid(textgrid, phones, source=self.name)


def _ctc_forced_align(
    log_probabilities: np.ndarray,
    targets: list[int],
    blank_token_id: int,
) -> tuple[list[list[int]], float]:
    if log_probabilities.ndim != 2:
        raise AlignmentError("CTC log probabilities must be a 2-D array.")
    if not targets:
        return [], 0.0

    states: list[int] = [blank_token_id]
    for target in targets:
        states.extend([target, blank_token_id])
    state_count = len(states)
    frames = log_probabilities.shape[0]
    negative_infinity = -1e30
    scores = np.full((frames, state_count), negative_infinity, dtype=np.float64)
    backpointers = np.full((frames, state_count), -1, dtype=np.int32)

    scores[0, 0] = log_probabilities[0, blank_token_id]
    scores[0, 1] = log_probabilities[0, targets[0]]
    for frame in range(1, frames):
        for state in range(state_count):
            candidates = [(scores[frame - 1, state], state)]
            if state - 1 >= 0:
                candidates.append((scores[frame - 1, state - 1], state - 1))
            if (
                state - 2 >= 0
                and states[state] != blank_token_id
                and states[state] != states[state - 2]
            ):
                candidates.append((scores[frame - 1, state - 2], state - 2))
            best_score, best_state = max(candidates)
            scores[frame, state] = (
                best_score + log_probabilities[frame, states[state]]
            )
            backpointers[frame, state] = best_state

    final_state = state_count - 1
    if (
        state_count > 1
        and scores[-1, state_count - 2] > scores[-1, state_count - 1]
    ):
        final_state = state_count - 2
    path = [0] * frames
    state = final_state
    for frame in range(frames - 1, -1, -1):
        path[frame] = state
        if frame > 0:
            state = int(backpointers[frame, state])

    token_frames: list[list[int]] = [[] for _ in targets]
    for frame, state in enumerate(path):
        target_index = (state - 1) // 2
        if state % 2 == 1 and 0 <= target_index < len(targets):
            token_frames[target_index].append(frame)
    confidence_values = [
        float(np.exp(log_probabilities[frame, states[state]]))
        for frame, state in zip(range(frames), path)
        if state % 2 == 1
    ]
    confidence = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else 0.0
    )
    return token_frames, confidence


class Wav2Vec2Aligner(AlignmentBackend):
    name = "wav2vec2"

    def __init__(self, config: Wav2Vec2AlignmentConfig):
        self.config = config
        self._processor = None
        self._model = None

    def available(self) -> bool:
        return (
            self.config.enabled
            and bool(self.config.model)
            and importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("transformers") is not None
        )

    def _load(self) -> None:
        if self._processor is not None and self._model is not None:
            return
        import torch
        from transformers import AutoModelForCTC, AutoProcessor

        self._processor = AutoProcessor.from_pretrained(
            self.config.model,
            do_phonemize=self.config.do_phonemize,
        )
        self._model = AutoModelForCTC.from_pretrained(self.config.model)
        self._model.eval()
        self._model.to(self.config.device)
        self._torch = torch

    def _target_ids(self, phones: list[PhoneSpec]) -> list[int]:
        tokenizer = self._processor.tokenizer
        vocabulary = tokenizer.get_vocab()
        unknown_id = tokenizer.unk_token_id
        target_ids: list[int] = []
        for phone in phones:
            mapped_token = self.config.token_map.get(phone.ipa, phone.ipa)
            candidates = [mapped_token, mapped_token.strip("/[]"), phone.label]
            token_id = None
            for candidate in candidates:
                if candidate and candidate in vocabulary:
                    token_id = vocabulary[candidate]
                    break
            if token_id is None:
                token_id = tokenizer.convert_tokens_to_ids(mapped_token)
            if token_id is None or token_id == unknown_id:
                raise AlignmentError(
                    f"The wav2vec2 tokenizer has no token for /{phone.ipa}/."
                )
            target_ids.append(int(token_id))
        return target_ids

    def align(
        self,
        audio_path: str | Path,
        phones: list[PhoneSpec],
        language: str,
        transcript: str = "",
    ) -> AlignmentResult:
        del language, transcript
        if not phones:
            return AlignmentResult([], self.name, 0.0)
        if not self.available():
            raise AlignmentError("wav2vec2 is not configured or not installed.")
        self._load()
        sound = read_wav(audio_path)
        samples = sound.samples.astype(np.float32)
        sample_rate = sound.sample_rate
        if sample_rate != self.config.sample_rate:
            duration = sound.duration
            target_count = max(1, int(round(duration * self.config.sample_rate)))
            source_times = np.linspace(
                0.0,
                duration,
                num=len(samples),
                endpoint=False,
            )
            target_times = np.linspace(
                0.0,
                duration,
                num=target_count,
                endpoint=False,
            )
            samples = np.interp(target_times, source_times, samples).astype(
                np.float32
            )
            sample_rate = self.config.sample_rate
        inputs = self._processor(
            samples,
            sampling_rate=sample_rate,
            return_tensors="pt",
        )
        input_values = inputs["input_values"].to(self.config.device)
        with self._torch.inference_mode():
            logits = self._model(input_values).logits
        log_probabilities = (
            self._torch.log_softmax(logits, dim=-1)[0].cpu().numpy()
        )
        target_ids = self._target_ids(phones)
        token_frames, confidence = _ctc_forced_align(
            log_probabilities,
            target_ids,
            self.config.blank_token_id,
        )
        duration = sound.duration
        aligned: list[AlignedPhone] = []
        for index, (phone, frames) in enumerate(zip(phones, token_frames), start=1):
            if frames:
                start = frames[0] / max(len(log_probabilities), 1) * duration
                end = (frames[-1] + 1) / max(len(log_probabilities), 1) * duration
            else:
                start = 0.0
                end = 0.0
            aligned.append(
                AlignedPhone(
                    phone_index=index,
                    ipa=phone.ipa,
                    start=start,
                    end=end,
                    confidence=confidence,
                    source=f"{self.name}:{self.config.model}",
                )
            )
        return AlignmentResult(
            phones=aligned,
            source=f"{self.name}:{self.config.model}",
            confidence=confidence,
        )


class ProportionalAligner(AlignmentBackend):
    name = "proportional"

    def available(self) -> bool:
        return True

    def align(
        self,
        audio_path: str | Path,
        phones: list[PhoneSpec],
        language: str,
        transcript: str = "",
    ) -> AlignmentResult:
        del language, transcript
        if not phones:
            return AlignmentResult([], self.name, 0.25)
        sound = read_wav(audio_path)
        reference_start = min(phone.reference_start for phone in phones)
        reference_end = max(phone.reference_end for phone in phones)
        reference_duration = max(reference_end - reference_start, 1e-9)
        scale = sound.duration / reference_duration
        aligned = [
            AlignedPhone(
                phone_index=index,
                ipa=phone.ipa,
                start=max(
                    0.0,
                    (phone.reference_start - reference_start) * scale,
                ),
                end=min(
                    sound.duration,
                    (phone.reference_end - reference_start) * scale,
                ),
                confidence=0.25,
                source=self.name,
            )
            for index, phone in enumerate(phones, start=1)
        ]
        return AlignmentResult(
            phones=aligned,
            source=self.name,
            confidence=0.25,
            warnings=["No forced aligner was available; timing is approximate."],
        )


class CompositeAligner:
    def __init__(
        self,
        backends: list[AlignmentBackend],
        *,
        agreement_threshold_sec: float = 0.04,
        minimum_confidence: float = 0.45,
    ):
        self.backends = backends
        self.agreement_threshold_sec = agreement_threshold_sec
        self.minimum_confidence = minimum_confidence

    def align(
        self,
        audio_path: str | Path,
        phones: list[PhoneSpec],
        language: str,
        transcript: str = "",
    ) -> AlignmentResult:
        results: list[AlignmentResult] = []
        warnings: list[str] = []
        for backend in self.backends:
            if not backend.available():
                continue
            try:
                results.append(
                    backend.align(audio_path, phones, language, transcript)
                )
            except AlignmentError as error:
                warnings.append(f"{backend.name}: {error}")

        if not results:
            fallback = ProportionalAligner().align(
                audio_path,
                phones,
                language,
                transcript,
            )
            fallback.warnings.extend(warnings)
            return fallback
        if len(results) == 1:
            results[0].warnings.extend(warnings)
            return results[0]
        return self._merge(results, warnings)

    def _merge(
        self,
        results: list[AlignmentResult],
        warnings: list[str],
    ) -> AlignmentResult:
        primary = max(results, key=lambda item: item.confidence)
        comparable = [
            result
            for result in results
            if len(result.phones) == len(primary.phones)
        ]
        if len(comparable) < 2:
            primary.warnings.extend(warnings)
            primary.warnings.append("Alignment backends produced different phone counts.")
            return primary

        merged_phones: list[AlignedPhone] = []
        minimum_phone_confidence = 1.0
        for index in range(len(primary.phones)):
            candidates = [result.phones[index] for result in comparable]
            total_weight = sum(max(candidate.confidence, 0.01) for candidate in candidates)
            start = sum(
                candidate.start * max(candidate.confidence, 0.01)
                for candidate in candidates
            ) / total_weight
            end = sum(
                candidate.end * max(candidate.confidence, 0.01)
                for candidate in candidates
            ) / total_weight
            disagreement = max(candidate.start for candidate in candidates) - min(
                candidate.start for candidate in candidates
            )
            disagreement += max(candidate.end for candidate in candidates) - min(
                candidate.end for candidate in candidates
            )
            disagreement /= 2.0
            agreement = math.exp(
                -disagreement / max(self.agreement_threshold_sec, 1e-9)
            )
            confidence = (
                sum(
                    candidate.confidence * max(candidate.confidence, 0.01)
                    for candidate in candidates
                )
                / total_weight
                * agreement
            )
            confidence = max(0.0, min(1.0, confidence))
            minimum_phone_confidence = min(minimum_phone_confidence, confidence)
            merged_phones.append(
                AlignedPhone(
                    phone_index=candidates[0].phone_index,
                    ipa=candidates[0].ipa,
                    start=start,
                    end=end,
                    confidence=confidence,
                    source="+".join(candidate.source for candidate in candidates),
                )
            )
        if minimum_phone_confidence < self.minimum_confidence:
            warnings.append(
                "Some phone boundaries have low cross-aligner agreement."
            )
        return AlignmentResult(
            phones=merged_phones,
            source="dual:" + "+".join(result.source for result in comparable),
            confidence=sum(phone.confidence for phone in merged_phones)
            / len(merged_phones),
            warnings=warnings,
        )


def build_aligner(config: AlignmentConfig) -> CompositeAligner:
    backend = config.backend.lower()
    backends: list[AlignmentBackend] = []
    if backend in {"auto", "mfa"}:
        backends.append(MfaAligner(config.mfa))
    if backend in {"auto", "wav2vec2", "ctc"}:
        backends.append(Wav2Vec2Aligner(config.wav2vec2))
    return CompositeAligner(
        backends,
        agreement_threshold_sec=config.agreement_threshold_sec,
        minimum_confidence=config.minimum_confidence,
    )
