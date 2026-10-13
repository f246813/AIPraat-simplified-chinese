from __future__ import annotations

import re
from dataclasses import dataclass

from .audio import read_wav
from .bridge import SelectedObject
from .models import AnalysisRequest, PhoneSpec
from .pronunciation_rules import PROFILES
from . import ui_theme, ui_widgets, ui_windows


@dataclass(slots=True)
class TutorFormValues:
    request: AnalysisRequest | None


def _parse_phonemes(text: str) -> list[str]:
    return [
        item
        for item in re.split(r"[\s,，;；]+", text.strip())
        if item
    ]


def _equal_segments(phonemes: list[str], duration: float) -> list[PhoneSpec]:
    if not phonemes:
        return []
    step = duration / len(phonemes)
    return [
        PhoneSpec(
            ipa=phoneme,
            reference_start=index * step,
            reference_end=(index + 1) * step,
            alignment_source="ui_equal_estimate",
        )
        for index, phoneme in enumerate(phonemes)
    ]


def show_tutor_form(sound_objects: list[SelectedObject]) -> TutorFormValues:
    import tkinter as tk
    from tkinter import messagebox, ttk

    result = TutorFormValues(request=None)
    if len(sound_objects) < 2:
        raise ValueError("AI 纠音需要至少两个已选中的 Sound 对象。")

    root = ui_windows.create_root()
    theme = ui_theme.Theme(root)
    root.title("Praat 本地 AI 纠音")
    root.resizable(False, False)
    root.configure(background=theme.color("canvas"))
    style = ttk.Style(root)
    if "clam" in style.theme_names() and style.theme_use() != "clam":
        style.theme_use("clam")
    ui_widgets.configure_ttk(style, theme)

    names = [f"{item.id}: {item.name}" for item in sound_objects]
    reference_name = tk.StringVar(value=names[0])
    learner_name = tk.StringVar(value=names[1])
    language = tk.StringVar(value="cmn")
    phoneme_text = tk.StringVar(value="m a n")
    transcript_text = tk.StringVar(value="")
    threshold = tk.StringVar(value="1.25")
    legacy_label = "旧式声学对比"
    rule_profiles = {f"{profile.name}音位判据（实验性）": profile for profile in PROFILES}
    rule_choice = tk.StringVar(value=legacy_label)
    qwen_explain = tk.BooleanVar(value=True)
    qwen_vision = tk.BooleanVar(value=False)

    frame = tk.Frame(root, background=theme.color("canvas"))
    frame.pack(fill="both", expand=True, padx=14, pady=14)
    card = ui_widgets.Card(frame, theme, padding=(14, 12, 14, 12), radius=12)
    card.pack(fill="both", expand=True)
    body = card.body
    body.columnconfigure(1, weight=1)

    def add_label(text: str, *, row: int, muted: bool = False, column: int = 0) -> None:
        tk.Label(
            body,
            text=text,
            anchor="w",
            background=theme.color("surface"),
            foreground=theme.color("textMuted" if muted else "text"),
            font=theme.font("small" if muted else "body"),
        ).grid(
            row=row,
            column=column,
            columnspan=2 if muted else 1,
            sticky="w",
            padx=(0, 8) if column == 0 else (0, 0),
            pady=(0, 6) if muted else 5,
        )

    def add_combo(variable, row: int) -> None:
        ttk.Combobox(
            body,
            textvariable=variable,
            values=names,
            state="readonly",
            width=46,
            font=theme.font("body"),
        ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)

    def add_entry(variable, row: int, *, width: int = 46, columnspan: int = 2):
        shell = ui_widgets.FieldCard(body, theme, background="surface")
        field = ttk.Entry(shell, textvariable=variable, width=width, font=theme.font("body"))
        shell.attach(field)
        shell.grid(row=row, column=1, columnspan=columnspan, sticky="ew", pady=5)
        return field

    add_label("标准音范本", row=0)
    add_combo(reference_name, 0)
    add_label("学习者录音", row=1)
    add_combo(learner_name, 1)
    add_label("目标语言", row=2)
    add_entry(language, 2)
    add_label("目标音位", row=3)
    add_entry(phoneme_text, 3)
    add_label("用空格、逗号或分号分隔 IPA 音位", row=4, muted=True, column=1)
    add_label("参考文本", row=5)
    add_entry(transcript_text, 5)
    add_label("使用 MFA 词典时填写，例如 hello world", row=6, muted=True, column=1)
    add_label("旧式对比阈值", row=7)
    threshold_entry = add_entry(threshold, 7, width=12, columnspan=1)

    ttk.Checkbutton(
        body,
        text="使用 Qwen 生成解释",
        variable=qwen_explain,
    ).grid(row=8, column=1, sticky="w", pady=4)
    ttk.Checkbutton(
        body,
        text="把对比图交给 Qwen 视觉模型解释",
        variable=qwen_vision,
    ).grid(row=9, column=1, sticky="w", pady=4)

    add_label("音位判据", row=10)
    rule_combo = ttk.Combobox(body, textvariable=rule_choice,
        values=[legacy_label, *rule_profiles], state="readonly", width=46, font=theme.font("body"))
    rule_combo.grid(row=10, column=1, columnspan=2, sticky="ew", pady=5)
    rule_note = tk.StringVar(value="旧请求沿用声学对比；英语/日语可选择音位判据。")
    tk.Label(body, textvariable=rule_note, background=theme.color("surface"),
        foreground=theme.color("textMuted"), font=theme.font("small"), wraplength=420, justify="left"
    ).grid(row=11, column=0, columnspan=3, sticky="w", pady=5)

    def choose_rules(event=None):
        profile = rule_profiles.get(rule_choice.get())
        threshold_entry.configure(state="disabled" if profile else "normal")
        if profile:
            language.set(profile.language)
        note = "按音类输出观察；需可靠对齐。未执行独立音素识别，判错阈值未校准。"
        if profile and profile.language == "ja":
            note += "日语拍结构：未评估。"
        rule_note.set(note if profile else "旧请求沿用声学对比；英语/日语可选择音位判据。")

    rule_combo.bind("<<ComboboxSelected>>", choose_rules)

    def analyze() -> None:
        by_label = {f"{item.id}: {item.name}": item for item in sound_objects}
        reference = by_label[reference_name.get()]
        learner = by_label[learner_name.get()]
        phonemes = _parse_phonemes(phoneme_text.get())
        if not phonemes:
            messagebox.showerror("错误", "至少输入一个 IPA 音位。")
            return
        selected_profile = rule_profiles.get(rule_choice.get())
        try:
            error_threshold = 1.25 if selected_profile else float(threshold.get())
        except ValueError:
            messagebox.showerror("错误", "错误阈值必须是数字。")
            return
        if selected_profile and language.get().strip() != selected_profile.language:
            messagebox.showerror("错误", "目标语言与所选音位判据不一致。")
            return

        reference_audio = read_wav(reference.file)
        result.request = AnalysisRequest(
            reference_object=reference.id,
            learner_object=learner.id,
            language=language.get().strip(),
            phonemes=_equal_segments(phonemes, reference_audio.duration),
            transcript=transcript_text.get().strip(),
            error_threshold=error_threshold,
            qwen_explain=qwen_explain.get(),
            qwen_vision=qwen_vision.get(),
            profile_id=selected_profile.id if selected_profile else None,
        )
        root.destroy()

    button_row = tk.Frame(body, background=theme.color("surface"))
    button_row.grid(row=12, column=0, columnspan=3, pady=(16, 0), sticky="e")
    ui_widgets.RoundedButton(
        button_row, theme, "取消", root.destroy, kind="text", background="surface"
    ).pack(side="right", padx=(8, 0))
    ui_widgets.RoundedButton(
        button_row, theme, "开始分析", analyze, kind="filled", background="surface"
    ).pack(side="right")

    root.mainloop()
    return result
