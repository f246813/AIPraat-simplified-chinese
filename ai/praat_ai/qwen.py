from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from .config import QwenConfig


class QwenError(RuntimeError):
    pass


class TruncatedResponseError(QwenError):
    """The token limit ended generation before a complete answer/action."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a model request/Authorization to another URL.


def _open_no_redirect(request, *, timeout):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


#: 规划接口的取值（见 :func:`planner_mode`）。
TOOLS_MODE = "tools"
JSON_MODE = "json"
AUTO_MODE = "auto"

#: ``QwenConfig.provider`` 的取值：``llama.cpp``（本地 llama-server）/
#: ``api``（云端 OpenAI 兼容服务，见 :class:`praat_ai.config.ApiConfig`）。
LOCAL_PROVIDER = "llama.cpp"
API_PROVIDER = "api"

#: 切换规划接口的环境变量：``tools`` / ``json`` / ``auto``（默认）。
PLANNER_MODE_ENV = "PRAAT_AI_PLANNER"


def planner_mode() -> str:
    """这次用哪种规划接口。

    - ``tools``：把工具的 JSON Schema 放进 ``tools``，读模型返回的 ``tool_calls``
      （llama-server 原生 function calling，实测本机 Qwen3.5-2B 支持，见
      ai/docs/adr/ADR-003）。
    - ``json``：老的「把工具清单和十几条规则写进提示词，让模型自己拼 JSON」。
    - ``auto``（默认）：先走 ``tools``；模型既没给工具调用、也没给任何正文时退回
      ``json``。换成不支持工具调用的服务端时，直接设 ``PRAAT_AI_PLANNER=json``
      可以省掉这一趟。
    """

    value = os.getenv(PLANNER_MODE_ENV, "").strip().casefold()
    if value in {"json", "prompt", "legacy"}:
        return JSON_MODE
    if value in {"tools", "tool", "function_calling", "fc"}:
        return TOOLS_MODE
    return AUTO_MODE


#: 原生 function calling 用的系统提示：只写业务规则和现场信息，
#: 工具的说明/参数/必填项都在 JSON Schema 里（不再靠提示词列清单）。
TOOL_PLANNER_INSTRUCTIONS = """
你是 Praat（中文版，Windows）桌面助手的操作规划器。用户用自然语言提出操作要求，你通过工具调用来完成，不要输出 Markdown。

规则：
1. 能用一个工具完成就不要写自定义脚本；只有所有工具都做不到时才用 custom_script。
2. custom_script 里必须是 Praat 英文脚本，绝对不能是 Python：不要出现 import、def、print、numpy、parselmouth、os。
3. Praat 脚本里字符串一律用双引号；单引号在 Praat 中是变量插值，会直接报错。
4. 对象只能使用对象列表里真实存在的 id 或名称，不得编造。
5. 用户明确说了对象编号（例如「3 号对象」「第二个声音」）时，object / object2 必须填那个对象。
6. 用户说「这个声音」「当前对象」时，指的是下面标着「当前选中」的那一个，它往往不是 1 号对象，别习惯性写 1。
7. 用户说法里带对象类型时按类型选：说「这个 TextGrid」就用 TextGrid，不要因为别的类型是当前选中就写错。
8. 没有对应工具的测量（例如 CPP）不许拿别的量代替；这时在回复里直说做不到，并说明还缺什么。
9. 请求能从当前选中对象直接完成时就直接调用工具，不要反问；只有完全无法执行时才在回复里提问。
10. 回复里只能用工具结果里出现过的数字、名称和文件名，一个都不要自己编造或推算；
    结果里没有的信息就直说没有。
11. 只做用户要求的那件事：不要顺手多做别的操作，也不要重复调用同一个工具；
   上一步的结果已经够回答时，直接回答，不要再调工具。
12. 回复里必须有一句能独立读懂的话：结论、测到的数值，或者「为什么做不到、还缺什么」。
    绝对不许只写「已完成」「好的」「已处理」这种空话——用户看不到任何信息。
""".strip()

#: API 的流程规则与本地小模型分开；关闭一般知识不等于关闭多步分析。
CLOUD_EVIDENCE_INSTRUCTIONS = """
回答时区分证据来源，按需要清楚标出以下类别，不必每次机械地写四个标题：
- 观测：本次工具成功返回的测量、用户提供的信息、图片中可见的内容。录音测量数字
  不得伪造，不得把计划、工具失败、示例、历史里未经核实的描述当作新测量。
- 透明推导：可以用已知输入计算差值、比例、归一化结果等；说明输入来源、公式或
  计算方法、单位和适用条件，明确这是推导而非新增实测。输入缺失时不要补造数字。
- 一般知识：能否用于解释与比较由下面的知识模式决定；它不能充当本次录音的证据。
- 假设：把可能原因和待验证判断写成假设，说明不确定性及怎样验证，不作确定结论。
未收到音频时，不得宣称直接听过录音或获得听感；对象列表、测量表、波形图片均不等于
音频输入。即使收到图像，也只能把图中可见内容当作图像证据。保留工具报告的未定义值、
错误和限制，不得修改既有分数或冒充已完成尚未执行的操作。
""".strip()

CLOUD_WORKFLOW_INSTRUCTIONS = """
你是 Praat（中文版，Windows）桌面助手。根据用户目标灵活规划、执行和解释；
可以使用 Markdown、列表、表格和适当详细的文字，让测量、依据与结论容易理解。

完整语音分析、比较和练习建议可以包含多步关联测量、分段分析和必要的对象准备。
按问题选择基频、强度、时长、共振峰等有意义的测量，不设固定的工具种类或数量限制；
不要求一次回答只解决一个子问题。先完成相关测量，再综合解释。已有结果足够时就回答；
避免无关操作和没有新依据的重复调用。同一工具可以用于不同对象、区间、参数或有依据的重试。
独立的操作可以一并调用；后续步骤依赖新对象 id、时长或上一步结果时，先等待观察结果，
再根据最新对象列表和选择状态继续。必要的分析准备属于用户目标，删除、覆盖文件或其他
无关改动不能因为要分析就擅自添加；遵守前端已有的校验与操作权限。

对象只能使用当前上下文或执行结果中已确认真实存在的 id 或名称，不得编造对象。
用户指定编号时明确填 object / object2；“当前对象”按最新选择状态判断；用户指定
Sound、Pitch、Formant、TextGrid 等对象类型时，按工具要求匹配合理的对象类型，
不要把新生成的 Pitch 或 Formant 错当作原始 Sound。有多个合理候选而缺少依据时说明歧义。
整段、整个、完整声音的请求必须使用 from=0，终点使用该目标对象实际 duration；
需要 start/end 等参数时采用对应的完整范围。时长未知则先查时长再填实际终点，
不能静默使用编辑器旧选区，不能凭空猜 duration，也不要把 to=0 当作明确的整段终点。
分段请求尊重用户区间，说明实际测量范围；不要把单个中点的测量说成整段统计。

工具报错后先读错误、检查对象类型与最新对象列表/选择状态；若上下文不足先获取或请求
必要状态，再调整为有区别的对象、区间或参数进行有依据的重试。不得重复原来的失败调用。
缺少测量能力时，不能拿另一种量替代，也不能用一般知识冒充该指标；说明缺什么和可行下一步。
优先使用已有工具；现有工具组合仍不能满足目标时，可以使用 custom_script 编写合法的
Praat 英文脚本，绝对不能写 Python。Praat 字符串用双引号，单引号仅作合法变量插值，
不要误用为字符串引号；遵守脚本安全校验和指定结果/完成标记路径。需要数值结果时使用
appendFileLine 写到指定结果文件。只有用户提供新名字时才设置 name / new_name。

回答要交付用户请求的实际结论、测量范围、解释或下一步；只回一句「已完成」等于没做。
回答长度按问题需要决定，失败时明确说明尚未完成的部分及限制。
""".strip()

#: API 开放知识模式下的解释规则，不再叠加本地的数字/单操作限制。
WORLD_KNOWLEDGE_INSTRUCTIONS = """
知识模式：开放。可以使用自己的语言学/语音学知识解释原理、比较典型特征、举例和
给出练习建议，不必为概念问题强行调用工具。对比东京标准音、普通话母语者等目标时，
明确参照是什么、它来自一般知识还是实际参考录音，再把相关观测和比较结论写出来。
录音测量数字只能来自实际工具结果；透明推导必须说明已知输入和计算方法。
引用“文献里的常见范围”时，明确这是一般知识而非本次测量，并说明语言、音素、说话者、
测量方法等适用条件；范围没有可靠把握就使用定性描述，不能编造文献、来源或统一标准值。
单凭少量声学指标不能确定发音是否标准、音素身份或健康状况；解释证据能支持什么、
还有什么不确定，针对用户目标给出具体可实行的练习及验证方式。
""".strip()

CLOUD_STRICT_KNOWLEDGE_INSTRUCTIONS = """
知识模式：严格。只依据工具观测、用户提供的参照和这些输入的透明推导进行解释与比较。
禁止无依据的一般知识对比，不得补充自带的标准音特征、常见范围、文献数字或练习处方。
没有参照时说明还缺哪些证据，可以建议怎样取得或测量这些证据。此模式仍允许完整分析、
多步关联测量、透明计算和清晰的 Markdown 回答；不要退回本地模型的单操作限制。
""".strip()


def identity_instructions(config: QwenConfig | None) -> str:
    """身份规则：问「你是谁」时报**自己的模型名**，不自称「Praat 语音助手」。

    模型名从配置里取（云端是 `deepseek-chat` 这类名字，本地是 gguf 文件名），
    所以这段提示必须按当前配置生成，不能写死。
    """

    model = str(getattr(config, "model", "") or "").strip() or "（未配置的模型）"
    if str(getattr(config, "provider", LOCAL_PROVIDER)) == API_PROVIDER:
        return (
            f"当前配置的模型身份是 {model}；涉及模型身份时如实说明这个配置名称，"
            "不要猜测服务端实际模型、厂商或声称已独立验证身份。\n"
            f"可以自然地回答，例如「我是 {model}，我被设置成 Praat 的前端。」；"
            "无需固定句式。用户问「我是谁」是询问用户身份，不能当成模型自我介绍；"
            "没有用户身份信息时如实说明。"
        )
    return (
        f"你的身份：你现在跑在模型 {model} 上（用户在前端里选的）。\n"
        "用户问「你是谁」「我是谁」「你是什么模型」时，**必须**照下面这个格式简短回答：\n"
        f"「我是 {model}，我被设置成 Praat 的前端。」\n"
        "上面第一行说的「Praat 桌面助手的操作规划器」只是分工说明，不是你自我介绍用的"
        "名字；不要自称「Praat 语音助手」或别的名字，不要列功能清单，也不要长篇自我介绍。"
    )


def knowledge_is_open(config: QwenConfig | None) -> bool:
    """这个配置允许模型发挥自己的世界知识吗（云端默认开、本地默认关）。"""

    return str(getattr(config, "knowledge_mode", "strict") or "strict") == "open"


def planner_instructions(config: QwenConfig | None = None) -> str:
    """按 provider 选择流程，知识模式仅决定 API 可以引用哪些解释依据。"""

    if str(getattr(config, "provider", LOCAL_PROVIDER)) == API_PROVIDER:
        return CLOUD_WORKFLOW_INSTRUCTIONS + "\n\n" + analysis_instructions(config)
    return TOOL_PLANNER_INSTRUCTIONS + "\n\n" + identity_instructions(config)


def analysis_instructions(config: QwenConfig) -> str:
    """API 的所有解释路径共享证据、身份和知识规则，不掺入操作传输格式。"""

    knowledge = (
        WORLD_KNOWLEDGE_INSTRUCTIONS
        if knowledge_is_open(config)
        else CLOUD_STRICT_KNOWLEDGE_INSTRUCTIONS
    )
    return "\n\n".join([CLOUD_EVIDENCE_INSTRUCTIONS, knowledge, identity_instructions(config)])


def message_text(message: Mapping[str, Any] | None) -> str:
    """从响应 message 中取得可见正文。content 为空时只使用 reasoning_content 中明确标出的最终答案，不能把未完成思考当回答。"""

    if not isinstance(message, Mapping):
        return ""
    content = message.get("content") or ""
    if isinstance(content, str) and content.strip():
        return content
    reasoning = message.get("reasoning_content") or ""
    if isinstance(reasoning, str) and reasoning.strip():
        return _final_reasoning_fallback(reasoning)
    return ""


#: 哪些服务端不认哪些「可选字段」：``{base_url: {字段名}}``。
#: 按服务端记——换一个网关（DeepSeek 换成 OpenAI）不该被上一个的结论连累。
REASONING_FIELDS_REJECTED: dict[str, set[str]] = {}


def _rejected_key(base_url: str) -> str:
    return (base_url or "").rstrip("/")


def rejected_thinking_fields(base_url: str) -> set[str]:
    """这个服务端已知不认的可选字段（空集合 = 都可以试）。"""

    return set(REASONING_FIELDS_REJECTED.get(_rejected_key(base_url), set()))


def remember_rejected_thinking_field(base_url: str, field: str) -> None:
    REASONING_FIELDS_REJECTED.setdefault(_rejected_key(base_url), set()).add(field)


def forget_rejected_thinking_fields() -> None:
    """清掉记忆（测试用，也可以在换服务商时调）。"""

    REASONING_FIELDS_REJECTED.clear()


#: 这些状态码通常表示「请求形状不对」（多半是不认某个字段），可以去掉可选字段
#: 重试一次。401/403/429/5xx 不是这一类，该报错就报错，别偷偷重发。
REQUEST_SHAPE_ERROR_CODES = frozenset({400, 422})


def _is_connection_refused(error: BaseException) -> bool:
    """连接被拒绝（本机上没人听这个端口）——不是超时、不是 DNS、不是 4xx。"""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ConnectionRefusedError):
            return True
        if getattr(current, "winerror", None) == 10061:
            return True
        text = str(current).casefold()
        if "10061" in text or "refused" in text or "积极拒绝" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _is_local_endpoint(base_url: str) -> bool:
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(base_url).hostname or "").casefold()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"} or host.startswith(
        "127."
    )


def connection_hint(base_url: str, error: BaseException) -> str:
    """把本地服务拒绝连接转换成可操作提示；其他错误返回空串。"""

    if not _is_local_endpoint(base_url) or not _is_connection_refused(error):
        return ""
    return (
        f"本机模型服务没在跑（{base_url.rstrip('/')} 拒绝了连接）："
        "在 Praat 菜单里点「前端 → 启动前端」，或者在对话窗口重新应用一次模型预设。"
    )


def _is_client_error(detail: str) -> bool:
    """``QwenError`` 的文案里是不是「请求形状不对」那个状态码
    （``HTTP 400 Bad Request：…``）。"""

    match = re.search(r"HTTP\s+(\d{3})", detail or "")
    return bool(match) and int(match.group(1)) in REQUEST_SHAPE_ERROR_CODES


def thinking_request_fields(config: QwenConfig) -> dict[str, Any]:
    """思考档位这次请求要多带哪些字段（纯函数，方便单测）。

    - 云端（``api``）：``reasoning_effort = low/medium/high``；``off``/``auto``
      不带（``auto`` = 交给服务端默认）。服务端报错不认这个字段时由
      :meth:`QwenClient.chat_message` 去掉重试，并记进
      :data:`REASONING_FIELDS_REJECTED`。
    - 本地（``llama.cpp``）：``chat_template_kwargs.enable_thinking``。``auto``
      沿用 ``qwen.enable_thinking``；``off`` 关；``low/medium/high`` 开
      （llama.cpp 没有更细的档位）。
    """

    from .config import normalize_thinking_level

    level = normalize_thinking_level(getattr(config, "thinking_level", "auto"))
    if str(getattr(config, "provider", LOCAL_PROVIDER)) == API_PROVIDER:
        from urllib.parse import urlsplit
        host = (urlsplit(config.base_url).hostname or '').casefold()
        official_qwen = (host in {'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com'}
                         or host.endswith('.maas.aliyuncs.com'))
        model = config.model.casefold()
        hybrid = model.startswith('qwen3') and not any(word in model for word in ('thinking', 'instruct'))
        if official_qwen and hybrid and level != 'auto':
            fields = {'enable_thinking': level != 'off'}
            if level in {'low', 'medium'}:
                fields['thinking_budget'] = {'low':1024, 'medium':4096}[level]
            return fields
        if level in {"low", "medium", "high"}:
            return {"reasoning_effort": level}
        return {}
    if level == "auto":
        enable = bool(getattr(config, "enable_thinking", False))
    else:
        enable = level != "off"
    return {"chat_template_kwargs": {"enable_thinking": enable}}


def history_messages(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """把对话历史转成 messages（接口不认的字段一律丢掉）。"""

    messages: list[dict[str, Any]] = []
    for item in history:
        role = str(item.get("role", ""))
        content = str(item.get("content", "") or "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages


 # 上下文预算扣除 schema、系统提示与输出预留，裁剪说明返回给调用方。

#: 给服务端留的余量（消息包装、工具调用回灌这些零碎开销）。
SAFETY_TOKENS = 200


def estimate_tokens(text: str) -> int:
    """粗略估 token 数：中日韩字符 ≈ 1 字 1 个，其余（ASCII/JSON 结构）≈ 4 字符 1 个。

    只要「够准」就行：用来决定该丢多少历史，不需要和分词器完全一致（真按词表算得
    拉一个 tokenizer，而本地只有 llama-server 的 HTTP 接口）。
    """

    if not text:
        return 0
    wide = 0
    narrow = 0
    for character in text:
        if ord(character) >= 0x2E80:   # CJK 部首/汉字/全角标点/假名/谚文…
            wide += 1
        else:
            narrow += 1
    return wide + (narrow + 3) // 4


def history_budget(
    max_context_tokens: int,
    *,
    instructions: str = "",
    tool_schemas: Any = (),
    user_text: str = "",
    response_tokens: int = 700,
) -> int:
    """这次请求还能给对话历史留多少 token。

    ``max_context_tokens`` 是配置里的上下文窗口；工具 schema、系统提示、用户这句话
    和留给模型的回答都要先扣掉，剩下的才是历史能用的。
    """

    overhead = (
        estimate_tokens(instructions or "")
        + estimate_tokens(user_text or "")
        + int(response_tokens)
        + SAFETY_TOKENS
    )
    schemas = list(tool_schemas or ())
    if schemas:
        overhead += estimate_tokens(json.dumps(schemas, ensure_ascii=False))
    return max(0, int(max_context_tokens) - overhead)


def trim_history(
    history: list[dict[str, str]], *, max_tokens: int
) -> tuple[list[dict[str, Any]], int]:
    """按预算从**最近**往回留对话历史，返回 ``(messages, 丢掉的条数)``。

    只留「从某句用户消息开始」的完整后段：如果留下来的第一句是 assistant 的回答，
    说明它对应的那句用户消息被截掉了，把它也丢掉——宁可少带一轮，也别给模型半截
    上下文。
    """

    messages: list[dict[str, Any]] = []
    used = 0
    for item in reversed(list(history or [])):
        role = str(item.get("role", ""))
        content = str(item.get("content", "") or "")
        if role not in {"user", "assistant"} or not content:
            continue
        cost = estimate_tokens(content) + 4
        if used + cost > max_tokens:
            break
        messages.append({"role": role, "content": content})
        used += cost
    messages.reverse()
    if messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages, max(0, len(history or []) - len(messages))


def trim_object_context(context_text: str, *, max_tokens: int) -> tuple[str, int]:
    """对象列表太长时只列一部分，并写清楚省掉了多少个（返回 ``(文本, 省掉的行数)``）。

    表头和**当前选中**那一行一定留着：提示词第 6 条（「当前对象往往不是 1 号」）就
    靠这一行；如果选中对象排得很靠后，会把它单独补进来。
    """

    lines = [line for line in (context_text or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return context_text, 0
    header, rows = lines[0], lines[1:]
    if estimate_tokens(context_text) <= max_tokens:
        return context_text, 0

    kept: list[str] = []
    used = estimate_tokens(header) + 4
    for row in rows:
        cost = estimate_tokens(row) + 1
        if kept and used + cost > max_tokens:
            break
        kept.append(row)
        used += cost

    selected = ""
    for row in rows:
        cells = row.split("\t")
        if len(cells) >= 4 and cells[3] == "1":
            selected = f"{cells[0]} 号「{cells[2]}」"
            if row not in kept:
                kept.append(row)
            break
    hidden = len(rows) - len(kept)
    note = (
        f"（对象列表一共 {len(rows)} 个，这里只列了 {len(kept)} 个；"
        f"还有 {hidden} 个没列出"
    )
    note += f"。当前选中是 {selected}）" if selected else "）"
    return "\n".join([header, *kept, note]), hidden


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """只接受完整 JSON 对象；损坏参数不能降级成默认当前对象操作。"""

    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise QwenError("工具参数缺失或为空，必须提供完整 JSON 对象（无参数时使用 {}）。")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise QwenError("工具参数不是完整合法的 JSON；未执行，请重新生成参数。") from error
    if not isinstance(value, dict):
        raise QwenError("工具参数必须是 JSON 对象，不能是数组、标量或 null。")
    return value


def extract_tool_actions(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把一次响应里的 ``tool_calls`` 变成 ``[{"tool": ..., "arguments": {...}}]``。

    模型可能一次给多个调用（实测同一句话里问两个时刻，它会给两个 ``pitch`` 调用），
    顺序就是它想执行的顺序。``id`` 也带上：回灌结果时要按 ``tool_call_id`` 对上。
    """

    calls = message.get("tool_calls") or []
    actions: list[dict[str, Any]] = []
    if not isinstance(calls, list):
        return actions
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        if not isinstance(function, Mapping):
            continue
        name = str(function.get("name", "") or "").strip()
        if not name:
            continue
        action: dict[str, Any] = {
            "tool": name,
            "arguments": {},
            "id": str(call.get("id", "") or f"call-{index + 1}"),
        }
        try:
            action["arguments"] = _parse_tool_arguments(function.get("arguments"))
        except QwenError as error:
            # 保留调用 id，让执行层拒绝并回灌失败，而非默默丢掉调用或执行 {}。
            action["invalid_arguments"] = str(error)
        actions.append(action)
    return actions


#: 模型写在正文里的工具调用（Qwen 的文本格式）。
_TEXT_TOOL_CALL = re.compile(r"<tool_call\s*>(.*?)</tool_call\s*>", re.DOTALL | re.IGNORECASE)
_TEXT_FUNCTION = re.compile(r"<function\s*=\s*([\w.\-]+)\s*>", re.IGNORECASE)
_TEXT_PARAMETER = re.compile(r"<parameter\s*=\s*([\w.\-]+)\s*>", re.IGNORECASE)


def parse_text_tool_calls(content: str) -> tuple[list[dict[str, Any]], str]:
    """解析正文中的 XML 工具调用，参数值按字符串传递，XML 外的文字保留为正文；截断调用按当前解析规则处理。"""

    text = content or ""
    actions: list[dict[str, Any]] = []
    blocks = _TEXT_TOOL_CALL.findall(text)
    if not blocks:
        first = _TEXT_FUNCTION.search(text)
        if first:
            # 模型被 max_tokens 截断，只有开头：把 <function=…> 之后的部分当一个块。
            blocks = [text[first.start() :]]
    for index, block in enumerate(blocks):
        functions = list(_TEXT_FUNCTION.finditer(block))
        for order, function in enumerate(functions):
            # 一个块里可能有多个 <function=…>，各自取到下一个函数标签为止。
            tail_start = (
                functions[order + 1].start() if order + 1 < len(functions) else len(block)
            )
            body = block[function.end() : tail_start]
            arguments: dict[str, Any] = {}
            for parameter in _TEXT_PARAMETER.finditer(body):
                key = parameter.group(1)
                tail = body[parameter.end() :]
                end = re.search(r"</parameter\s*>", tail, re.IGNORECASE)
                raw = tail[: end.start()] if end else tail
                arguments[key] = raw.strip()
            actions.append(
                {
                    "tool": function.group(1),
                    "arguments": arguments,
                    "id": f"text-{index + 1}-{order + 1}",
                }
            )
    if not actions:
        return [], text.strip()

    leftover = _TEXT_TOOL_CALL.sub("", text)
    if _TEXT_FUNCTION.search(leftover):
        # 没有闭合标签（模型被 max_tokens 截断）：从 <tool_call> / <function= 起全不要。
        leftover = re.split(r"<tool_call\s*>|<function\s*=", leftover, maxsplit=1, flags=re.IGNORECASE)[0]
    return actions, leftover.strip()


def assistant_tool_message(
    message: Mapping[str, Any],
    actions: list[dict[str, Any]] | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    """把模型那次「工具调用」的响应整理成可以回传的 assistant 消息。

    只留 ``role`` / ``content`` / ``tool_calls``：``reasoning_content`` 这类字段
    各家服务端的接受度不一样，回灌时不需要它（推理内容不参与下一轮对话）。

    ``actions`` 由调用方给出（正文里那串文本形式的调用也要按同样的顺序回传，
    否则后面每条 ``role: tool`` 的 id 就对不上了）。
    """

    parsed = extract_tool_actions(message) if actions is None else actions
    calls: list[dict[str, Any]] = []
    for action in parsed:
        calls.append(
            {
                "id": str(action.get("id", "") or "call-1"),
                "type": "function",
                "function": {
                    "name": action["tool"],
                    "arguments": json.dumps(action["arguments"], ensure_ascii=False),
                },
            }
        )
    if content is None:
        raw = message.get("content") or ""
        content = raw if isinstance(raw, str) else ""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": calls,
    }


def tool_result_message(call_id: str, content: str) -> dict[str, Any]:
    """一次工具执行的观察结果，按 OpenAI 的 ``role: tool`` 形状回灌。"""

    return {"role": "tool", "tool_call_id": call_id, "content": content}


def selected_object_hint(object_context: str) -> str:
    """把「当前选中哪个对象」单独写一句，模型不会误挑同名列表里的第一条。"""

    for line in (object_context or "").splitlines():
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) >= 4 and parts[0].isdigit() and parts[3] == "1":
            return f"当前选中：id {parts[0]}（{parts[1]} {parts[2]}）"
    return "当前没有选中任何对象。"


class QwenClient:
    def __init__(self, config: QwenConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    def available(self) -> bool:
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return 200 <= response.status < 300
        except (OSError, urllib.error.URLError):
            return False

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        message = self.chat_message(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            tools=tools,
        )
        return message_text(message)

    def chat_message(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        """发一次请求，返回原始的 ``message`` 对象（含 ``tool_calls``）。"""

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": self.config.top_p,
            "presence_penalty": self.config.presence_penalty,
        }
        if (self.config.provider == API_PROVIDER
                and self.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
                and self.config.model.startswith("gemini-3")):
            # Gemini 3 uses its own sampling defaults; these OpenAI fields can
            # degrade or reject requests on Google's compatibility endpoint.
            for field in ("temperature", "top_p", "presence_penalty"):
                payload.pop(field)
        if self.config.limit_tokens:
            payload["max_tokens"] = max_tokens
        # 思考档位：云端是 reasoning_effort，本地是 chat_template_kwargs
        # （llama.cpp 专有：用模型自带的 chat 模板，多轮里把工具结果作为
        # role=tool 回灌，没有这一条小模型会跑偏，见 ADR-004）。
        fields = thinking_request_fields(self.config)
        rejected = rejected_thinking_fields(self.base_url)
        fields = {
            key: value
            for key, value in fields.items()
            if key not in rejected
        }
        payload.update(fields)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        try:
            result = self._post(payload)
        except QwenError as error:
            detail = str(error)
            # 服务端不认思考档位时去掉它再发一次，并记住别再发（下次直接不带，
            # 不白挨一遍报错）。判定放宽到「任何 4xx」：有的服务端只说
            # 「invalid request」不点名，有的用 422，点名和状态码都不能指望。
            if "reasoning_effort" in payload and (
                "reasoning_effort" in detail or _is_client_error(detail)
            ):
                remember_rejected_thinking_field(self.base_url, "reasoning_effort")
                payload.pop("reasoning_effort")
                result = self._post(payload)
            # 新一点的云端模型不认 max_tokens（要 max_completion_tokens），
            # 报错里提到它时换个字段名再试一次，别让用户自己去猜。
            elif "max_tokens" not in payload or "max_completion_tokens" not in detail:
                raise
            else:
                payload.pop("max_tokens", None)
                payload["max_completion_tokens"] = max_tokens
                result = self._post(payload)

        try:
            choice = result["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise QwenError("Qwen returned an unexpected response.") from error
        if not isinstance(message, dict):
            raise QwenError("Qwen returned an unexpected response.")
        if choice.get("finish_reason") == "length":
            raise TruncatedResponseError(
                "模型输出达到长度上限，回答或工具调用被截断；未执行这次不完整的调用。"
                "可降低思考强度或提高输出上限后重试。"
            )
        return message

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发一次 ``/chat/completions``，返回解析后的 JSON。"""
        if self.config.token_mode is not None:
            from .modern_budget import model_settings
            completion_field = 'max_completion_tokens' in payload
            for key in ('max_tokens', 'max_completion_tokens', 'temperature', 'top_p', 'presence_penalty'):
                payload.pop(key, None)
            payload.update(model_settings(self.config, input_cost=estimate_tokens(
                json.dumps(payload.get('messages', []), ensure_ascii=False))))
            if completion_field and 'max_tokens' in payload:
                payload['max_completion_tokens'] = payload.pop('max_tokens')

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            opener = _open_no_redirect if self.config.token_mode is not None else urllib.request.urlopen
            with opener(
                request,
                timeout=self.config.request_timeout_sec,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", errors="replace")[:300]
            except Exception:   # noqa: BLE001 - 读不到就算了
                detail = ""
            raise QwenError(
                f"HTTP {error.code} {error.reason}：{detail or error}"
            ) from error
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            detail = f"Qwen request failed: {error}"
            # 本机 llama-server 没在跑时给一句能照着做的提示（WinError 10061）。
            hint = connection_hint(self.base_url, error)
            if hint:
                detail = f"{hint}（{detail}）"
            raise QwenError(detail) from error

    def parse_analysis_request(
        self,
        user_text: str,
        selected_objects: list[dict[str, Any]],
    ) -> dict[str, Any]:
        schema = {
            "reference_object": "selected object id or name",
            "learner_object": "selected object id or name",
            "language": "BCP-47 or language name",
            "phonemes": [
                {
                    "ipa": "IPA symbol",
                    "label": "optional display label",
                    "reference_start": 0.0,
                    "reference_end": 0.1,
                }
            ],
            "error_threshold": 1.25,
            "qwen_explain": True,
            "qwen_vision": False,
        }
        system_prompt = (
            "你是 Praat 纠音助手的请求解析器。"
            "只输出一个 JSON 对象，不输出 Markdown。"
            "不得编造不存在的对象 ID；示例中的时间、音素和阈值只是格式示意，"
            "不能当作用户提供的标注或本次测量。"
        )
        if self.config.provider == API_PROVIDER:
            system_prompt = analysis_instructions(self.config) + "\n\n" + system_prompt
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    f"可用对象：{json.dumps(selected_objects, ensure_ascii=False)}\n"
                    f"目标 JSON 示例：{json.dumps(schema, ensure_ascii=False)}\n"
                    f"用户请求：{user_text}"
                ),
            },
        ]
        response = self.chat(messages, max_tokens=700, temperature=0.1, json_mode=True)
        return extract_json_object(response)

    def plan_praat_command(
        self,
        user_text: str,
        object_context: str,
        history: list[dict[str, str]],
        *,
        tool_catalog: str,
        result_path: str,
        state_path: str,
        tool_schemas: list[dict[str, Any]] | None = None,
        tool_labels: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """规划一次操作，返回 ``{"reply", "tool", "arguments", "script", "actions"}``。

        默认走原生 function calling：工具的 JSON Schema 交给 llama-server，读回来的
        ``tool_calls`` 直接就是工具名 + 参数，不用模型自己拼 JSON（也不用把十几条
        格式规则写进提示词）。``PRAAT_AI_PLANNER=json`` 可以退回老接口，排障用。
        """

        mode = planner_mode()
        if mode != JSON_MODE and tool_schemas:
            plan = self._plan_with_tools(
                user_text,
                object_context,
                history,
                tool_schemas=tool_schemas,
                tool_labels=dict(tool_labels or {}),
                result_path=result_path,
                state_path=state_path,
            )
            if plan is not None:
                return plan
        return self._plan_with_json(
            user_text,
            object_context,
            history,
            tool_catalog=tool_catalog,
            result_path=result_path,
            state_path=state_path,
        )

    def _plan_with_tools(
        self,
        user_text: str,
        object_context: str,
        history: list[dict[str, str]],
        *,
        tool_schemas: list[dict[str, Any]],
        tool_labels: dict[str, str],
        result_path: str,
        state_path: str,
    ) -> dict[str, Any] | None:
        """原生 function calling 那条路；返回 ``None`` 表示「退回 JSON 接口」。"""

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": planner_instructions(self.config)
                + "\n\n"
                + self._tool_context(result_path, state_path, object_context),
            },
        ]
        messages.extend(history_messages(history))
        messages.append({"role": "user", "content": user_text})
        message = self.chat_message(
            messages,
            tools=tool_schemas,
            max_tokens=self.config.plan_max_tokens,
            temperature=self.config.plan_temperature,
        )
        actions = extract_tool_actions(message)
        reply = message_text(message).strip()
        if not actions:
            if reply:
                # 模型选择只回话、不执行动作（例如「你好」或「这个做不到」）。
                return {"reply": reply, "tool": "", "arguments": {}, "script": "", "actions": []}
            if planner_mode() == TOOLS_MODE:
                raise QwenError("模型没有返回工具调用，也没有给出任何回复。")
            return None
        if not reply:
            # 只给了工具调用、没写回话：用工具说明兜一句，别让对话窗口显示空白。
            reply = tool_labels.get(actions[0]["tool"], "") or f"执行 {actions[0]['tool']}。"
        plan: dict[str, Any] = {
            "reply": reply,
            "actions": actions,
            "tool": actions[0]["tool"],
            "arguments": actions[0]["arguments"],
            "script": "",
        }
        return plan

    def _tool_context(
        self, result_path: str, state_path: str, object_context: str
    ) -> str:
        return "\n".join(
            [
                f"结果文件（脚本把数值结果写到这里）：{result_path}",
                f'完成标记文件（脚本最后一行必须是 appendFileLine: "{state_path}", "done"）：'
                f"{state_path}",
                "",
                selected_object_hint(object_context),
                "",
                "当前 Praat 对象列表（id、类、名称、是否选中）：",
                object_context,
            ]
        )

    def _plan_with_json(
        self,
        user_text: str,
        object_context: str,
        history: list[dict[str, str]],
        *,
        tool_catalog: str,
        result_path: str,
        state_path: str,
    ) -> dict[str, Any]:
        """老的提示词 + JSON 接口（``PRAAT_AI_PLANNER=json`` 或工具接口失败时用）。"""

        system_prompt = """
你是 Praat（中文版，Windows）桌面助手的操作规划器。用户用自然语言提出操作要求，你只输出一个 JSON 对象，不输出 Markdown，不输出多余文字。

JSON 结构：
{"reply": "给用户的中文简短回复", "tool": "工具名", "arguments": {}, "script": ""}

规则：
1. tool 必须从下面的工具列表里选择；能用工具完成时 script 必须是空字符串。
2. 只有所有工具都无法完成请求时，tool 才用 "custom_script"，并在 script 里写完整 Praat 英文脚本。
3. script 必须是 Praat 脚本，绝对不能是 Python：不要出现 import、def、print、numpy、parselmouth、os 等 Python 写法。
4. 脚本里字符串一律用双引号；单引号在 Praat 中是变量插值，会直接报错。
5. 对象只能使用下方对象列表里真实存在的 id 或名称，不得编造。
6. 请求能从当前选中对象直接完成时，直接选工具，不要反问；只有请求完全无法执行时才在 reply 里提问。
7. 需要输出数值时，用 appendFileLine 写到我给出的结果文件路径。
8. 时间单位一律是秒；超出对象时长的时刻会被自动截断到对象末尾。
9. 常见需求都已经有对应工具（新建声音、截取片段、拼接声音、另存 WAV、重采样、复制对象、统计基频/强度、一次查询多条共振峰），优先用工具而不是自己写脚本。
10. 用户在句子里明确说了对象编号（例如「3 号对象」「第二个声音」）时，必须在 arguments 里用 object / object2 指出那个对象，不能留空。
11. 只有用户给出了新名字时才填 name / new_name；「复制一份」这类请求不要抄原来的名字。
12. 用户说「这个声音」「当前对象」时指的就是下面标着「当前选中」的那一个；它往往不是 1 号对象，别习惯性写 1。
13. 用户说法里带对象类型时按类型选：说「这个 TextGrid」就用 TextGrid，说「这个声音」就用 Sound；不要因为另一个类型的对象是当前选中就写错。
14. 没有对应工具的测量（例如 CPP）绝不能拿别的量代替；reply 里直说做不到，并说明还缺什么。
""".strip()
        if self.config.provider == API_PROVIDER:
            system_prompt = planner_instructions(self.config) + "\n\n" + """
本接口的传输格式：只输出一个 JSON 对象，外层不要加 Markdown 围栏或额外文字。
用户可见的 reply 可以包含 Markdown 和完整解释，JSON 字符串须正确转义。
单步结构：{"reply": "回答或本步意图", "tool": "工具名或空字符串", "arguments": {}, "script": ""}
多个独立动作可用 actions 数组：{"reply": "本步意图", "actions": [{"tool": "工具名", "arguments": {}}]}。
tool 只能是工具清单中的名称或 custom_script；自定义脚本放 arguments.script。
没有操作要执行时 tool=""、arguments={}、script=""，也不要输出任何 actions；
依赖前一步结果的操作不要预猜参数，等执行观察后再继续。答复不能把本次计划写成已完成。
""".strip()
        elif knowledge_is_open(self.config):
            system_prompt = (
                system_prompt
                + "\n\n"
                + WORLD_KNOWLEDGE_INSTRUCTIONS
                + "\n\n另外：reply 字段就是给用户看的回答。对比、解释、评价类请求要把"
                "比较和结论写在 reply 里（可以长一点），只写「已完成」等于没做；"
                "reply 里出现的测量数值必须来自工具结果。"
            )
        examples = """
示例：
用户：提取当前语音的第二共振峰带宽
输出：{"reply": "查询中间时刻的第 2 共振峰带宽。", "tool": "formant_bandwidth", "arguments": {"formant": 2}, "script": ""}
用户：查询 0.4 秒处的 F1 和 F2
输出：{"reply": "查询 0.4 秒处的第 1、2 共振峰频率。", "tool": "formant_frequency", "arguments": {"formant": "1,2", "time": 0.4}, "script": ""}
用户：把选中的声音改名为 测试
输出：{"reply": "重命名选中的声音。", "tool": "rename_object", "arguments": {"new_name": "测试"}, "script": ""}
用户：打开这个声音的编辑器
输出：{"reply": "打开编辑器。", "tool": "view_edit", "arguments": {}, "script": ""}
用户：这个声音的基频平均是多少
输出：{"reply": "统计整个声音的基频。", "tool": "pitch_statistics", "arguments": {}, "script": ""}
用户：截取 0.2 到 0.5 秒
输出：{"reply": "截取 0.2–0.5 秒的片段。", "tool": "extract_part", "arguments": {"start": 0.2, "end": 0.5}, "script": ""}
用户：生成一个 1 秒的 220 Hz 正弦音
输出：{"reply": "新建 1 秒的 220 Hz 纯音。", "tool": "create_sound", "arguments": {"duration": 1, "frequency": 220}, "script": ""}
用户：把这个声音保存成 D:/out/a.wav
输出：{"reply": "保存为 WAV 文件。", "tool": "save_sound", "arguments": {"path": "D:/out/a.wav"}, "script": ""}
用户：在 0.28 到 0.45 秒之间找 VOT
输出：{"reply": "在这个范围里估计 VOT。", "tool": "vot", "arguments": {"from": 0.28, "to": 0.45}, "script": ""}
用户：导入 D:/in/a.wav
输出：{"reply": "读取 wav 文件。", "tool": "custom_script", "arguments": {}, "script": "Read from file: \\"D:/in/a.wav\\""}
""".strip()
        if self.config.provider == API_PROVIDER:
            # 云端示例不能用空参数暗示“整段”可以继承编辑器选区。
            examples = """
示例（对象 id、数值和区间仅为格式示意，实际调用必须用当前现场已确认的信息）：
用户：统计整个声音的基频
尚未知时长时：{"reply": "先查询目标声音的实际时长。", "tool": "duration", "arguments": {"object": 7}, "script": ""}
已确认目标 Sound 是 7、实际 duration 为 2.5 秒时：{"reply": "统计整段 0–2.5 秒的基频。", "tool": "pitch_statistics", "arguments": {"object": 7, "from": 0, "to": 2.5}, "script": ""}
用户：查询 0.4 秒处的 F1 和 F2
输出：{"reply": "查询该时刻的第一、第二共振峰。", "tool": "formant_frequency", "arguments": {"object": 7, "formant": "1,2", "time": 0.4}, "script": ""}
用户：导入 D:/in/a.wav
输出：{"reply": "读取声音文件。", "tool": "read_file", "arguments": {"path": "D:/in/a.wav"}, "script": ""}
已有观测足够回答时：{"reply": "在此填写观测、必要的透明推导、解释和限制。", "tool": "", "arguments": {}, "script": ""}
""".strip()
        context_message = "\n".join(
            [
                f"结果文件（脚本把数值结果写到这里）：{result_path}",
                f'完成标记文件（脚本最后一行必须是 appendFileLine: "{state_path}", "done"）：'
                f"{state_path}",
                "",
                "可用工具：",
                tool_catalog,
                "",
                selected_object_hint(object_context),
                "",
                "当前 Praat 对象列表（id、类、名称、是否选中）：",
                object_context,
                "",
                examples,
            ]
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt + "\n\n" + context_message,
            },
        ]
        for item in (history if self.config.token_mode is not None or not self.config.limit_tokens else history[-8:]):
            role = item.get("role", "")
            content = item.get("content", "")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})
        response = self.chat(
            messages,
            max_tokens=self.config.plan_max_tokens,
            temperature=self.config.plan_temperature,
            json_mode=True,
        )
        return extract_json_object(response)

    def explain_errors(self, request: dict[str, Any], result: dict[str, Any]) -> str:
        cloud = self.config.provider == API_PROVIDER
        system_prompt = (
            analysis_instructions(self.config)
            + "\n\n你是发音训练助手。依据给定结果解释偏差，回答用户目标并说明证据和限制；"
            "开放知识模式下可提供原理与可验证的练习建议，不得修改既有分数。"
            if cloud else
            "你是发音训练助手。根据给定数值解释发音偏差，"
            "不得修改分数，不得声称听到了音频。回答控制在 300 字以内。"
        )
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    f"请求：{json.dumps(request, ensure_ascii=False)}\n"
                    f"分析结果：{json.dumps(result, ensure_ascii=False)}"
                    + ("\n音位判据说明：观察不等于发音错误；保持实验性、未评估及证据不足状态，不得推断未执行的音素识别或给出总分。" if result.get("profile_snapshot") else "")
                ),
            },
        ]
        return self.chat(
            messages, max_tokens=self.config.plan_max_tokens if cloud else 500,
            temperature=0.4,
        ).strip()

    def vision_explain(
        self,
        image_path: str | Path,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        path = Path(image_path)
        media_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        cloud = self.config.provider == API_PROVIDER
        system_prompt = (
            analysis_instructions(self.config)
            + "\n\n你是语音学图表解释助手。描述图中可见的曲线、阴影区间和数值，"
            "将图像观测与给定测量结果联系起来解释，标明不能从图中确定的内容；"
            "不得重新计算或修改既有发音分数。"
            if cloud else
            "你是语音学图表解释助手。只描述图中可见的曲线、"
            "阴影区间和数值，不得重新计算发音分数。"
        )
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"请求：{json.dumps(request, ensure_ascii=False)}\n"
                            f"分析结果：{json.dumps(result, ensure_ascii=False)}"
                            + ("\n音位判据说明：图表仅为实验性声学观察，不能判定发音对错；保留未评估与证据不足状态。" if result.get("profile_snapshot") else "")
                        ),
                    },
                ],
            },
        ]
        return self.chat(
            messages, max_tokens=self.config.plan_max_tokens if cloud else 500,
            temperature=0.2,
        ).strip()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise QwenError("The model did not return a JSON object.")


def _final_reasoning_fallback(reasoning: str) -> str:
    markers = (
        "Final Decision:",
        "Final Answer:",
        "最终输出：",
        "最终答案：",
    )
    for marker in markers:
        if marker in reasoning:
            return reasoning.rsplit(marker, 1)[-1].strip()
    return ""


def probe_api(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 20.0,
) -> tuple[bool, str]:
    """试一下这个 API 能不能用（「API 配置」窗口里的「测试连接」）。

    只发一条极小的对话请求：能拿到回复就说明地址、key、模型名三样都对。比只查
    ``/models`` 更靠得住——有些网关的 ``/models`` 是假的或者不列全部模型。
    """

    client = QwenClient(
        QwenConfig(
            base_url=(base_url or "").strip().rstrip("/"),
            model=(model or "").strip(),
            api_key=(api_key or "EMPTY"),
            provider=API_PROVIDER,
            request_timeout_sec=max(5, int(timeout)),
            plan_max_tokens=16,
        )
    )
    try:
        message = client.chat_message(
            [{"role": "user", "content": "ping"}],
            max_tokens=16,
            temperature=0.0,
        )
    except TruncatedResponseError:
        return True, f"连接成功（{model} 的测试回答被截断，但接口是通的）。"
    except QwenError as error:
        return False, str(error)
    text = str(message.get("content") or message.get("reasoning_content") or "").strip()
    if not text:
        return True, f"连接成功（{model} 回了空内容，但接口是通的）。"
    return True, f"连接成功：{model} 回了「{text[:40]}」。"
