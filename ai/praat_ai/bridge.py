from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PraatBridgeError(RuntimeError):
    pass


@dataclass(slots=True)
class SelectedObject:
    id: int
    name: str
    class_name: str
    file: Path
    source: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_praat(cls, value: dict[str, Any]) -> "SelectedObject":
        return cls(
            id=int(value.get("id", 0)),
            name=str(value.get("name", "")),
            class_name=str(value.get("class", "")),
            file=Path(str(value.get("file", ""))),
            source={key: value[key] for key in ('original_sound_domain', 'wav_to_sound_offset_sec',
                'sample_rate_hz', 'channels', 'export_format') if key in value},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "class": self.class_name,
            "file": str(self.file),
            **self.source,
        }


class PraatBridge:
    def __init__(self, helper: Any):
        self.helper = helper

    @classmethod
    def from_praat(cls) -> "PraatBridge":
        try:
            import praat as helper  # type: ignore[import-not-found]
        except ImportError as error:
            raise PraatBridgeError(
                "This script must run inside Praat's Python editor or another "
                "process where the generated `praat` helper module is available."
            ) from error
        return cls(helper)

    def selected_objects(self) -> list[SelectedObject]:
        values = self.helper.get_selected()
        if not isinstance(values, list):
            raise PraatBridgeError("praat.get_selected() did not return a list.")
        return [SelectedObject.from_praat(value) for value in values]

    def resolve_sound(self, reference: int | str) -> SelectedObject:
        objects = self.selected_objects()
        for item in objects:
            if item.class_name != "Sound":
                continue
            if isinstance(reference, int) and item.id == reference:
                return item
            if isinstance(reference, str) and item.name == reference:
                return item
        raise PraatBridgeError(f"Selected Sound object not found: {reference!r}")

    def output_dir(self) -> Path:
        return Path(self.helper.get_output_dir()).resolve()

    def call(self, command: str, *arguments: Any) -> None:
        self.helper.call(command, *arguments)

    def run_praat_script(self, script: str) -> None:
        self.helper.run_praat_script(script)

    def select(self, object_id: int) -> None:
        self.helper.select(object_id)

    def plus_select(self, object_id: int) -> None:
        self.helper.plus_select(object_id)


def object_payload(objects: list[SelectedObject]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in objects]
