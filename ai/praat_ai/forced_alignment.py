from __future__ import annotations

import importlib.util
import inspect
import math
import re
import shutil
import threading
import subprocess
import tempfile
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .audio import read_wav
from .config import AlignmentConfig, MfaAlignmentConfig, Wav2Vec2AlignmentConfig
from .models import AlignedPhone, AlignmentResult, PhoneSpec
from .mfa_g2p import run_command as run_mfa_command


class AlignmentError(RuntimeError):
    pass


class AlignmentCancelled(RuntimeError):
    """用户取消导致对齐（或依赖对齐的分析）中止。

    它**不是**对齐失败：调用方不得把它当成「MFA 没跑成」而回退比例对齐——那等于用
    猜测的边界冒充实测，用户看到的会是一份「已完成」的报告。调用方应尽快结束，并如实
    说明哪些结果已经拿到、哪些没做。
    """

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
        *,
        cancel: threading.Event | None = None,
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
    raw_labels: bool = False,
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
    expected = [phone.label if raw_labels else phone.ipa for phone in phones]
    labels = [item[2].strip() for item in spoken]
    if raw_labels and labels != expected:
        raise AlignmentError('MFA 原始音位序列与本次选定读音不一致，不能按近似序列配对。')
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

    def __init__(self, config: MfaAlignmentConfig, *, raw_labels: bool = False):
        self.config = config
        self.raw_labels = raw_labels

    def available(self) -> bool:
        if self.config.conda_executable and self.config.conda_environment:
            launcher_available = (
                shutil.which(self.config.conda_executable) is not None
                or Path(self.config.conda_executable).is_file()
            )
        else:
            launcher_available = (shutil.which(self.config.executable) is not None
                                  or Path(self.config.executable).is_file())
        return (
            self.config.enabled
            and launcher_available
            and bool(self.config.acoustic_model)
        )

    def _launcher(self) -> list[str]:
        if not self.raw_labels:
            if self.config.conda_executable and self.config.conda_environment:
                return [self.config.conda_executable, 'run', '-n', self.config.conda_environment, 'mfa']
            return [self.config.executable]
        from .mfa_g2p import launcher_command
        return launcher_command(dict(executable=self.config.executable,
                                     conda_executable=self.config.conda_executable,
                                     conda_environment=self.config.conda_environment))

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

    def _timeout(self) -> float:
        """等待上限（秒）。配置里写了非数字或非正值时不静默变成「无超时」，而是回到默认值。"""

        try:
            timeout = float(self.config.timeout_sec)
        except (TypeError, ValueError):
            return 1800.0
        return timeout if timeout > 0 else 1800.0

    def _alignment_error(self, message: str, timeout: float) -> RuntimeError:
        """把 ``mfa_g2p.run_command`` 的失败翻成对齐层的错误。

        取消要区分出来（它不是对齐失败，不能触发比例回退）；超时按失败报告，但把等待
        上限写进原因，用户才知道是自己设的时限还是对齐真的挂了。
        """

        if message.startswith('已取消'):
            return AlignmentCancelled('已取消 MFA 对齐：对齐进程已终止。')
        if '超时' in message:
            return AlignmentError(f'MFA 对齐超时（>{timeout:g} 秒）：{message}')
        return AlignmentError(f'MFA alignment failed: {message}')

    def align(
        self,
        audio_path: str | Path,
        phones: list[PhoneSpec],
        language: str,
        transcript: str = "",
        *,
        cancel: threading.Event | None = None,
    ) -> AlignmentResult:
        del language
        if not phones:
            return AlignmentResult([], self.name, 0.0)
        if not self.available():
            raise AlignmentError("MFA is not configured or not installed.")

        if self.raw_labels:
            from .mfa_g2p import english_words
            transcript = ' '.join(english_words(transcript))
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
            timeout = self._timeout()
            try:
                # 复用 mfa_g2p 已有的进程树管理：超时/取消都会杀掉整棵树（含 conda 子进程），
                # 而不是像旧的 subprocess.run 那样一直等下去。这里不传 cwd：参数字里都是绝对
                # 路径，工作目录保持与旧实现一致，免得相对的可执行文件路径失效。
                run_mfa_command(command, cancel, timeout)
            except ValueError as error:
                raise self._alignment_error(str(error), timeout) from error
            textgrid = output / "learner.TextGrid"
            if not textgrid.is_file():
                candidates = list(output.glob("*.TextGrid"))
                if not candidates:
                    raise AlignmentError("MFA did not produce a TextGrid.")
                textgrid = candidates[0]
            return parse_mfa_textgrid(textgrid, phones, source=self.name, raw_labels=self.raw_labels)


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


def ctc_span_quality(token_frames, frame_count, duration):
    """Observable timing facts of a CTC alignment. Not a pronunciation judgement.

    A path that spends a single frame on half of the phones carries no duration
    information at all: every phone would report the same length, so callers must
    not treat it as a usable phone alignment even when the frame probabilities are
    high. Measured on a real degenerate run: five phones, one frame each (0.0203 s
    per phone, ~10 % of the analysed audio), reported confidence 0.71-0.78.
    """
    counts = [len(frames) for frames in token_frames]
    if not counts or frame_count <= 0:
        return dict(phones=len(counts), median_frames=0.0, covered_seconds=0.0,
                    covered_fraction=0.0, degenerate=True)
    frames = sum(counts)
    median = float(np.median(counts))
    return dict(phones=len(counts), median_frames=median,
                covered_seconds=frames / frame_count * duration,
                covered_fraction=frames / frame_count,
                degenerate=median <= 1.0)


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
        *,
        cancel: threading.Event | None = None,
    ) -> AlignmentResult:
        del language, transcript
        if not phones:
            return AlignmentResult([], self.name, 0.0)
        if cancel is not None and cancel.is_set():
            raise AlignmentCancelled('已取消声学模型对齐：未开始加载模型。')
        if not self.available():
            raise AlignmentError("wav2vec2 is not configured or not installed.")
        self._load()
        if cancel is not None and cancel.is_set():
            raise AlignmentCancelled('已取消声学模型对齐：模型已加载，未开始推理。')
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
        quality = ctc_span_quality(token_frames, len(log_probabilities), sound.duration)
        if quality["degenerate"]:
            # Reject instead of trusting it: the composite aligner then falls through to
            # the next backend (or to proportional timing, which the rules already treat
            # as insufficient evidence) and the report keeps this reason visible.
            raise AlignmentError(
                "wav2vec2 对齐退化：{phones} 个音素中位 {median:g} 帧/音素，"
                "覆盖 {covered:.3f}/{duration:.3f} 秒（{fraction:.1%}）；"
                "该结果没有可用时长信息，已拒绝".format(
                    phones=quality["phones"], median=quality["median_frames"],
                    covered=quality["covered_seconds"], duration=sound.duration,
                    fraction=quality["covered_fraction"]))
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
        *,
        cancel: threading.Event | None = None,
    ) -> AlignmentResult:
        del language, transcript
        if cancel is not None and cancel.is_set():
            raise AlignmentCancelled('已取消对齐：未生成比例回退。')
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

def supports_cancel(backend: Any) -> bool:
    """这个对齐后端接受 ``cancel`` 参数吗？

    项目自己的后端都有；老式/第三方对象可能没有。看签名决定怎么调用，别等 TypeError
    才知道——那会把后端内部真的抛出的 TypeError 也误判成「签名不匹配」。
    """

    try:
        signature = inspect.signature(backend.align)
    except (TypeError, ValueError):
        return False
    if "cancel" in signature.parameters:
        return True
    return any(parameter.kind is inspect.Parameter.VAR_KEYWORD
               for parameter in signature.parameters.values())


class CompositeAligner:
    def __init__(
        self,
        backends: list[AlignmentBackend],
        *,
        agreement_threshold_sec: float = 0.04,
        minimum_confidence: float = 0.45,
        strict: bool = False,
    ):
        self.backends = backends
        self.agreement_threshold_sec = agreement_threshold_sec
        self.minimum_confidence = minimum_confidence
        self.strict = strict

    def align(
        self,
        audio_path: str | Path,
        phones: list[PhoneSpec],
        language: str,
        transcript: str = "",
        *,
        cancel: threading.Event | None = None,
    ) -> AlignmentResult:
        results: list[AlignmentResult] = []
        warnings: list[str] = []
        for backend in self.backends:
            if cancel is not None and cancel.is_set():
                # 取消不是「对齐失败」：绝不落到比例回退，直接把取消往上抛。
                raise AlignmentCancelled('已取消对齐：未生成比例回退。')
            if not backend.available():
                continue
            if cancel is not None and not supports_cancel(backend):
                # 老式/第三方后端没有 cancel 参数：还能在步骤之间取消，但要说明白，
                # 不能让调用方以为「你按了停止就一定停得住正在跑的那一步」。
                warnings.append(
                    f"{backend.name}: 该对齐后端不支持取消参数，只能在步骤之间取消。")
            try:
                if supports_cancel(backend):
                    results.append(
                        backend.align(audio_path, phones, language, transcript, cancel=cancel))
                else:
                    results.append(backend.align(audio_path, phones, language, transcript))
            except AlignmentError as error:
                warnings.append(f"{backend.name}: {error}")
        if not results:
            if self.strict:
                raise AlignmentError('配套 MFA 对齐失败，未执行比例回退：' + '; '.join(warnings or ['MFA 不可用']))
            fallback = ProportionalAligner().align(
                audio_path,
                phones,
                language,
                transcript,
                cancel=cancel,
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
    if backend in {"auto", "mfa", "mfa-strict"}:
        backends.append(MfaAligner(config.mfa, raw_labels=backend == 'mfa-strict'))
    if backend in {"auto", "wav2vec2", "ctc"}:
        backends.append(Wav2Vec2Aligner(config.wav2vec2))
    return CompositeAligner(
        backends,
        agreement_threshold_sec=config.agreement_threshold_sec,
        minimum_confidence=config.minimum_confidence,
        strict=backend == 'mfa-strict',
    )
