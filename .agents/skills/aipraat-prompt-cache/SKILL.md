---
name: aipraat-prompt-cache
description: Audit AIPraat cloud prompt prefixes and cache usage when changing message history, skills, compaction, phase schemas or cloud runtime lifecycle.
---

Use the current context and cache interface reference in docs/CURRENT-IMPLEMENTATION.zh-CN.md. This is a development skill; AIPraat never loads this directory at runtime.

1. Inspect cloud_agent, session_context, cloud_metrics and the existing phase regressions before editing. Reuse Pydantic AI's public message_history, ModelMessagesTypeAdapter and RequestUsage extraction, and the existing CloudRuntime/SSE adapter. Keep Python and the pinned SDK.
2. Check the **final HTTP payload**, after SDK/provider translation. Base system, output schema and ordered tool declarations must stay constant within a phase/configuration. Goals, material metadata, selected skill bodies and new evidence append at the tail. Task guidance uses labelled user parts; inserting a new system part can be merged into the leading system by the provider.
3. Test three consecutive turns. The previous request's messages must be an exact prefix of the next request. Compare dialogue/planner/report separately. The task shares request/tool limits, failed-problem/stagnation counts, original target and delivery/unknown facts across every phase, skill and summary.
4. Inspect request_timings and phases, with cache_read_tokens/cache_write_tokens and input_tokens_total. Missing fields remain null. OpenAI prompt_tokens already includes cached tokens; do not add them twice. Anthropic-exclusive input counts need explicit normalization. Deduplicate responses by actual request/attempt, not repeated all_messages reads.
5. A moving last-message cache marker can be a valid write/read ladder: request N writes what N+1 reads. Do not remove markers based only on location. Test at least three requests and distinguish TTL/configuration/compaction epochs from unstable content. Implicit cache hits are observations, not guarantees.
6. Run: `$env:PYTHONPATH='ai;ai/tests'; python -m unittest test_phase_context test_cloud_protocol test_dialogue_protocol test_cloud_escape test_report_guards test_tool_guards test_delivery_state -q`. Use the project benchmark for live A/B only when authorized; preserve the same model, thinking, output limits, recordings and evidence. Report input reduction, cache counters, requests/retries and wall time separately. Exclude pilot samples with different settings. Never infer latency gains from cache coverage alone.

The upstream replay utility is preserved at ai/third_party/prompt-cache-skills/check_cache.py. Its generic hit_rate calculation adds cached tokens to input even for inclusive providers, and treats missing counters as zero; use cloud_metrics normalization for AIPraat statistics. Short requests/implicit misses do not prove caching is broken. Do not send prewarm/keepalive requests.

Sources: OnlyTerp/prompt-cache-skills docs/verification.md and tools/check_cache.py at 5b58ae26bd446bfb2eee97e8dad076ffb46a6715, adapted under its MIT option (license at ai/third_party/prompt-cache-skills/LICENSE). Pi compaction/agent-loop at 28dcce2ba45ce4a9efeb0f5b686f0be830fd89b9 (MIT; ai/third_party/pi-context/LICENSE). This project's instructions override harness-specific Cline/Roo patches.
