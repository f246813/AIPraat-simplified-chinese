"""表格驱动的声学测量参数（B1）。

出处：chengafni/praat 的 ``plugin_CompleteAnalysis`` 把「生成哪些对象」
(``objects.txt``)、「每个参数怎么查」(``queries.txt``)、「分析设置」
(``settings.txt``) 写成三张表，加一个参数只要加一行。这里沿用那个思路，
但**脚本仍然由 Python 生成**：他们那套是在 Praat 里把命令拼成字符串再
``'queryCommand$'`` 动态求值（Praat 6.0 时代可行，7.0 上很脆），我们只把表
当数据，命令字面量直接写进脚本（见 ``tools._build_measure`` 与 guide §8.4）。

和参考实现的两处**故意不同**：

1. 他们是**编辑器视角**（Zoom to selection、当前 tier、visible contour），
   我们是**对象列表视角**（选中对象 → ``To Pitch`` / ``To Intensity``）。
   所以表里的 ``source`` 写的是「要哪个派生对象」，而不是编辑器显示项；
   ``queries.txt`` 里那几条只能用编辑器查的（Voice report 的 mean
   autocorrelation）单列在 ``@@ editor_only``，reply 里用中文说明。
2. 表里没有 ``Get``/``runScript`` 字符串拼接，只有命令字面量与 ``{setting}``
   占位符；占位符的值来自 ``@@ settings``，可以用同名工具参数覆盖。

``measure`` 工具的 JSON Schema、catalog 文字和真机用例都是按这张表生成的，
所以「加一个参数」= 加一行（另见 ai/tests/test_measures.py）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


TABLE_PATH = Path(__file__).with_name("measures.tsv")

#: 用户/模型常见参数名 → 表里的规范名。只收无歧义的写法：
#: 「共振峰」既可能指 F1 也可能指全部，不进入该表，仍按未知参数报错。
PARAMETER_ALIASES: dict[str, str] = {
    "f0": "mean_pitch", "基频": "mean_pitch", "平均基频": "mean_pitch",
    "pitch": "mean_pitch", "meanpitch": "mean_pitch",
    "最低基频": "minimum_pitch", "最小基频": "minimum_pitch", "minpitch": "minimum_pitch",
    "最高基频": "maximum_pitch", "最大基频": "maximum_pitch", "maxpitch": "maximum_pitch",
    "基频中位数": "median_pitch", "中位基频": "median_pitch",
    "基频标准差": "sd_pitch", "基频标准偏差": "sd_pitch",
    "基频斜率": "pitch_slope", "音高斜率": "pitch_slope",
    "平均强度": "mean_intensity", "强度": "mean_intensity",
    "最低强度": "minimum_intensity", "最高强度": "maximum_intensity",
    "强度中位数": "median_intensity", "强度标准差": "sd_intensity",
    "谐噪比": "hnr", "信噪比": "hnr", "harmonicity": "hnr",
    "jitter": "local_jitter", "jitterlocal": "local_jitter", "localjitter": "local_jitter",
    "局部基频微扰": "local_jitter", "基频微扰": "local_jitter",
    "shimmer": "local_shimmer_percent", "shimmerlocal": "local_shimmer_percent",
    "局部振幅微扰": "local_shimmer_percent", "振幅微扰": "local_shimmer_percent",
    "shimmerapq3": "apq3_shimmer", "apq3": "apq3_shimmer",
    "cpp": "cpps", "倒谱峰突出": "cpps", "倒谱峰值": "cpps",
    "有效值": "rms", "均方根": "rms",
    "峰值幅度": "peak_amplitude", "平均幅度": "mean_amplitude",
    "第一共振峰": "f1", "第1共振峰": "f1", "第二共振峰": "f2", "第2共振峰": "f2",
    "第三共振峰": "f3", "第3共振峰": "f3",
    "第一共振峰带宽": "b1", "第二共振峰带宽": "b2", "第三共振峰带宽": "b3",
    "频谱重心": "centre_of_gravity", "重心": "centre_of_gravity",
    "偏度": "skewness", "峰度": "kurtosis",
    "高强度频率比": "hl_ratio", "hl": "hl_ratio",
    "hammarberg": "hammarberg_index", "hammarberg指数": "hammarberg_index",
    "频谱强调": "spectral_emphasis", "谱强调": "spectral_emphasis",
    "基频峰值延迟": "pitch_peak_latency", "峰值因子": "peak_to_average_amplitude",
    "强度斜率": "intensity_slope_global", "局部强度斜率": "intensity_slope_local",
}


def canonical_parameter(parameter: str) -> str:
    """把常见写法归一到表里的规范名；不认识就原样返回（由调用方报错并列出可用参数）。"""

    raw = (parameter or "").strip()
    if not raw:
        return raw
    key = re.sub(r"[\s_\-]+", "", raw).casefold()
    return PARAMETER_ALIASES.get(key, raw)



COLUMNS: dict[str, tuple[str, ...]] = {
    "settings": ("key", "value", "min", "max", "kind", "note"),
    "derivations": ("key", "source", "command", "note"),
    "queries": (
        "parameter",
        "label",
        "unit",
        "decimals",
        "source",
        "command",
        "note",
    ),
    "dedicated": ("parameter", "label", "tool", "arguments", "note"),
    "editor_only": ("parameter", "label", "command", "note"),
}


class TableError(ValueError):
    """表格本身写错了（列名不对、缺列、重复参数名……）。"""


@dataclass(frozen=True, slots=True)
class Setting:
    """一条分析设置；``command`` 里的 ``{key}`` 会被替换成 ``value``。"""

    key: str
    value: str
    minimum: str
    maximum: str
    kind: str
    note: str = ""

    @property
    def is_integer(self) -> bool:
        return self.kind.strip().casefold() in {"integer", "int"}


@dataclass(frozen=True, slots=True)
class Derivation:
    """从基础对象（或另一个派生对象）生成中间对象的一条规则。"""

    key: str
    source: str
    command: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class Entry:
    """一行参数：``kind`` 是 query / dedicated / editor。"""

    parameter: str
    label: str
    kind: str
    note: str = ""
    source: str = ""
    command: str = ""
    unit: str = ""
    decimals: str = "3"
    tool: str = ""
    arguments: str = "{}"


@dataclass(frozen=True, slots=True)
class Table:
    settings: dict[str, Setting]
    derivations: dict[str, Derivation]
    entries: tuple[Entry, ...]

    def parameters(self) -> list[str]:
        """所有参数名，按表里的顺序（schema 的 enum 和真机用例都用它）。"""

        return [entry.parameter for entry in self.entries]

    def find(self, parameter: str) -> Entry:
        name = canonical_parameter(parameter)
        for entry in self.entries:
            if entry.parameter == name or entry.parameter.casefold() == name.casefold():
                return entry
        raise KeyError(name)

    def canonical(self, parameter: str) -> str:
        """规范名（解析不了就抛 ``KeyError``）——给「别名表是否生效」这类断言用。"""

        return self.find(parameter).parameter

    def queries(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries if entry.kind == "query")


def source_keys(source: str) -> list[str]:
    """``pointProcess+sound`` → ``["pointProcess", "sound"]``。

    用 ``+`` 连起来的两个对象是 Praat 的「双对象命令」（``plusObject:``），
    例如 ``Get shimmer (local)`` 需要同时选中 PointProcess 和 Sound。
    """

    return [piece.strip() for piece in (source or "").split("+") if piece.strip()]


def _read_rows(path: Path) -> dict[str, list[dict[str, str]]]:
    """把 TSV 读成 ``{小节: [行, ...]}``，行是「列名 → 值」。"""

    sections: dict[str, list[dict[str, str]]] = {name: [] for name in COLUMNS}
    headers: dict[str, list[str]] = {}
    current: str | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("@@"):
            name = stripped[2:].strip()
            if name not in COLUMNS:
                raise TableError(f"{path}:{number} 未知的小节 @@ {name}")
            current = name
            headers.pop(name, None)
            continue
        if current is None:
            raise TableError(f"{path}:{number} 出现在任何 @@ 小节之前")
        if current not in headers:
            headers[current] = [piece.strip() for piece in line.split("\t")]
            expected = COLUMNS[current]
            if tuple(headers[current]) != expected:
                raise TableError(
                    f"{path}:{number} @@ {current} 的列名是 {headers[current]}，"
                    f"应该是 {list(expected)}"
                )
            continue
        values = [piece.strip() for piece in line.split("\t")]
        if len(values) < len(headers[current]):
            values += [""] * (len(headers[current]) - len(values))
        sections[current].append(dict(zip(headers[current], values)))
    return sections


def _build_table(sections: dict[str, list[dict[str, str]]]) -> Table:
    settings: dict[str, Setting] = {}
    for row in sections["settings"]:
        key = row["key"].strip()
        if not key:
            raise TableError("settings 里有一行没有 key")
        settings[key] = Setting(
            key=key,
            value=row["value"],
            minimum=row["min"],
            maximum=row["max"],
            kind=row["kind"] or "number",
            note=row["note"],
        )
    derivations: dict[str, Derivation] = {}
    for row in sections["derivations"]:
        key = row["key"].strip()
        if not key or not row["command"].strip():
            raise TableError("derivations 里有一行缺 key 或 command")
        derivations[key] = Derivation(
            key=key,
            source=row["source"].strip(),
            command=row["command"],
            note=row["note"],
        )
    entries: list[Entry] = []
    seen: set[str] = set()
    for kind, rows in (
        ("query", sections["queries"]),
        ("dedicated", sections["dedicated"]),
        ("editor", sections["editor_only"]),
    ):
        for row in rows:
            parameter = row["parameter"].strip()
            if not parameter:
                raise TableError(f"{kind} 里有一行没有 parameter")
            if parameter in seen:
                raise TableError(f"参数名重复：{parameter}")
            seen.add(parameter)
            entries.append(
                Entry(
                    parameter=parameter,
                    label=row["label"],
                    kind=kind,
                    note=row["note"],
                    source=row.get("source", ""),
                    command=row.get("command", ""),
                    unit=row.get("unit", ""),
                    decimals=row.get("decimals", "") or "3",
                    tool=row.get("tool", ""),
                    arguments=row.get("arguments", "") or "{}",
                )
            )
    return Table(settings=settings, derivations=derivations, entries=tuple(entries))


_CACHE: dict[Path, Table] = {}


def load_table(path: Path | str | None = None) -> Table:
    """读表（进程内缓存一份）。"""

    target = Path(path) if path is not None else TABLE_PATH
    try:
        cached = _CACHE.get(target)
    except TypeError:   # pragma: no cover - 只在不传 path 时命中缓存
        cached = None
    if cached is not None:
        return cached
    table = _build_table(_read_rows(target))
    _CACHE[target] = table
    return table


def reset_cache() -> None:
    """测试用：丢掉缓存，改了表之后重新读。"""

    _CACHE.clear()
