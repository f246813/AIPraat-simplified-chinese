"""Count-only final HTTP observation; SDK normalizes provider usage.

No credentials, request prose or audio bytes are retained. ContextVar isolates
requests on reused clients and separate session loops.
"""
from __future__ import annotations
import hashlib
import json
from contextvars import ContextVar
from pydantic_ai.usage import RequestUsage

current_request = ContextVar('aipraat_request_metrics', default=None)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def wire_fingerprint(payload):
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != 'cache_control'}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    messages = strip(payload.get('messages', []))
    static = {k: strip(payload[k]) for k in ('model', 'tools', 'response_format') if k in payload}
    static['system'] = [m for m in messages if m.get('role') in {'system', 'developer'}]
    return dict(static=fingerprint(static), messages=[fingerprint(m) for m in messages],
                message_count=len(messages), request_bytes=len(json.dumps(payload).encode()),
                cache_mode='implicit')


async def observe_request(request):
    timing = current_request.get()
    if timing is not None:
        payload = json.loads(request.content)
        timing['wire'] = wire_fingerprint(payload)
        timing['requested_output_tokens'] = payload.get('max_completion_tokens', payload.get('max_tokens'))


def observe_usage(raw):
    timing = current_request.get()
    if timing is not None and raw is not None:
        # Only usage counters, never provider arbitrary string extras.
        def counts(value):
            if isinstance(value, dict):
                return {k: counts(v) for k, v in value.items() if isinstance(v, (int, dict))}
            return value
        timing['raw_usage'] = counts(raw)


async def observe_response(response):
    timing = current_request.get()
    if timing is not None:
        timing['http_status'] = response.status_code
        if response.headers.get('content-type', '').startswith('application/json'):
            await response.aread()
            try:
                observe_usage(response.json().get('usage'))
            except (ValueError, AttributeError):
                pass


def normalize_usage(raw, *, provider='openai', provider_url='https://api.openai.com/v1', exclusive=False):
    raw = raw or {}
    usage = RequestUsage.extract(dict(usage=raw), provider=provider, provider_url=provider_url,
                                 provider_fallback='openai', api_flavor='chat') if all(k in raw for k in ('prompt_tokens','completion_tokens')) else RequestUsage()
    nested = raw.get('prompt_tokens_details') or raw.get('input_tokens_details') or {}
    read = next((v for v in (nested.get('cached_tokens'), raw.get('prompt_cache_hit_tokens'),
                            raw.get('cache_read_input_tokens')) if isinstance(v, int)), None)
    write = next((v for v in (nested.get('cache_write_tokens'), nested.get('cache_creation_input_tokens'),
                             raw.get('cache_creation_input_tokens'))
                  if isinstance(v, int)), None)
    total = raw.get('prompt_tokens', raw.get('input_tokens'))
    if exclusive and isinstance(total, int):
        total += (read or 0) + (write or 0)
    elif 'prompt_tokens' in raw:
        total = usage.input_tokens or total
    return dict(input_tokens_total=total, cache_read_tokens=read, cache_write_tokens=write,
                output_tokens=raw.get('completion_tokens', raw.get('output_tokens')),
                reasoning_tokens=(raw.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                input_semantics='uncached_plus_cache' if exclusive else 'total_includes_cache',
                usage_source=sorted(raw))


def aggregate(timings):
    phases = {}
    for item in timings:
        phase = phases.setdefault(item['phase'], dict(requests=0, retries=0, elapsed_seconds=0,
                                                    input_tokens_total=None, cache_read_tokens=None,
                                                    cache_write_tokens=None, observable_requests=0))
        phase['requests'] += 1
        phase['retries'] += int(item.get('retry', False))
        phase['elapsed_seconds'] += item['elapsed_seconds']
        for k in ('input_tokens_total', 'cache_read_tokens', 'cache_write_tokens'):
            value = item.get(k)
            if value is not None:
                phase[k] = (phase[k] or 0) + value
        if item.get('cache_read_tokens') is not None:
            phase['observable_requests'] += 1
    for phase in phases.values():
        phase['cache_coverage'] = (phase['cache_read_tokens'] / phase['input_tokens_total']
                                  if phase['cache_read_tokens'] is not None and phase['input_tokens_total'] else None)
    return phases
