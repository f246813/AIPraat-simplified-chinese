# Codex public thread-section protocol / Apache-2.0

Conversation disclosures (2026-10-17): `upstream/transcript/` retains complete,
unchanged `exec_cell/compact.rs` and `history_cell/activity_preview.rs` from the
same pinned revision. Its SOURCE.json records verified Git blob identities and
SHA-256 checksums. `src/codex/activity-state.ts` ports the failure / active /
completed precedence from `compact_command_lines`, using host statuses in place
of shell exit codes. The originals retain full command/output behind compact
previews. `Chat.tsx` locally adapts that disclosure pattern to one native HTML
details element per reply, closed by default, with nested step/tool records;
the aggregate disclosure and its CSS are local implementations. User-requested
avatar/name removal, compact typography and borderless message bubbles are also
local React/CSS changes. Public Codex does not provide the desktop React chat
renderer; no desktop UI source is claimed as copied. Human-readable saved
measurements remain visible, including after cancellation. Complete modified
Chat.tsx, SearchBar.tsx and styles.css ship in codex-source/modified/transcript/;
the full rebuild inputs also ship in pi-desktop-source/rebuild/.

Upstream: https://github.com/openai/codex
Fixed revision: `ab45264919aaeb8a421cc156f1a5459ef9d60b72`.
Copyright and author notice: see `UPSTREAM-NOTICE`. License: Apache-2.0 (`LICENSE`).

`upstream/` preserves the listed original files unchanged. `SOURCE.json` records the exact revision, Git blob identities and SHA-256 checksums, verified against that Git tree. The two generated TypeScript files `ThreadSection.ts` and `ThreadSectionAppearance.ts` are copied byte-for-byte into `ai/frontend/src/codex/protocol/` and included in `modified/protocol/` in built assets. The generated headers are retained.

Local adaptation:
- `HistoryOrganization.tsx` and `organization.css` render section headers, menus, editing, move selection and drag targets using this project's existing React/Pi renderer. The public repository does not supply the Codex desktop React sidebar/menu source. These are local rendering, not copied Codex desktop components.
- `modern_organization.py` implements stable section identity, nullable single membership, append/insert-before ordering with a 1,000,000 position gap, protected built-in pinned section, trimmed nonempty names, 64-byte icon/color limits, and detach-on-section-delete from the public protocol/SQLite/thread-store behavior. UUID generation uses the existing local host UUID4 helper instead of upstream UUID7; section names are additionally limited to 100 characters. Python/storage names and transaction plumbing are adapted to Praat's existing store.
- Public `thread/fork` establishes the independent history-fork operation. The local host snapshots full conversation history into new session/message/activity identities, preserves compressed context and historical evidence, resets draft/reading state, marks live fragments interrupted, and records parent provenance. Optional cutoff-turn and Codex process/worktree options are not implemented in this conversation-menu scope.
- Per-section archive/restore is a local aggregation of conversation archive operations, not a claimed upstream `sections.archive` API. Archiving retains membership and the section; deleting a section detaches conversations without deleting history. Legacy records use modern sidecar metadata; the old database remains read-only.

Sidebar options: `upstream/sidebar-options/` retains
`ThreadSortKey.ts`, `SortDirection.ts` and `ThreadListParams.ts` unchanged, with
Git blob/SHA-256 provenance. The first two are copied byte-for-byte into
`src/codex/protocol/` and consumed by the existing history comparator.
`SidebarOptions.tsx` renders a local menu
hierarchy: organize by project/remote connection/list, and a sort submenu with
the four existing project choices (including exactly one recent-update choice).
The public repository has no desktop React menu/grouping renderer to copy.
Submenu mechanics reuse installed Radix Dropdown Menu 2.1.24 (MIT), its official
Sub/Portal/RadioGroup composition, and existing Pi context-menu styles; the
hierarchy and host preference wiring are local adapters.
`sidebar-origins.ts` uses Pi's existing path normalization and exact
`folderNameFromPath` excerpt to group recorded project/connection origins while
leaving pinned/custom sections intact. The host adds origin columns to its own
SQLite database and validates/persists the grouping preference. This desktop
host currently records its real local workspace; preserved imported remote
metadata can be grouped without starting a remote connector or agent. Missing
legacy project paths remain unknown. Model API endpoints are not connections.

No Codex app-server/CLI/agent/model runtime or Pi Agent kernel is imported or started. The frontend continues to use assistant-ui and explicit Praat host RPC through pywebview/WebView2.

Search adapters: four complete original Rust files are retained unchanged in
`upstream/search/`, verified by its `SOURCE.json` against pinned Git blobs.
`src/codex/useSessionSearch.ts` translates `tui/src/task_mentions.rs::spawn_search`:
100ms debounce, generation checks before dispatch/after response, host search and title matches.
The existing sidebar consumes compact match IDs rather than fetching every full conversation.
`modern_search.py` translates the escaped case-insensitive literal matcher and user/assistant
text selection in `rollout/src/search.rs`, and matching-results transport in
`thread-store/src/local/search_threads.rs`. Storage adapts JSONL/ripgrep to existing SQLite
JSON rows; no additional engine, index or background service is installed.
Python `re.IGNORECASE` supplies host Unicode matching; this is a Python port, not the Rust regex binary.
`src/codex/literal-matcher.ts` translates `LiteralMatcher::find_ranges` in
`thread-store/src/local/thread_history/search.rs`, retaining linear two-pointer mapping while
adapting byte offsets to DOM UTF-16 offsets (with a length-preserving fast path).
`search.ts` maps these offsets to existing Pi-style DOM highlights; `SearchBar.tsx` reuses
matches while navigating. Mounted-message counting/window-reveal behavior is preserved.
Complete modified frontend/host source ships with originals and notices. These are source
ports with explicit language/storage adaptations, not private Codex desktop React UI.

Search result invalidation uses a stable key derived from session identity,
title, persisted update time and archive state, independent of array order.
Reading-position, draft and preview changes retain results; history changes
invalidate them. This belongs to the local React/host adapter.

Archive settings use complete public ThreadUnarchiveParams, ThreadDeleteParams,
ThreadSourceKind and ThreadListParams types retained in upstream/archived-settings/.
The local ArchivedSessions.tsx renderer adapts public settings documentation and
layout references; public Codex does not publish its desktop React renderer.
The host stores legacy deletion state in modern metadata and keeps the original
legacy database read-only. Modern deletion uses existing history/evidence cascades.
Archive/restore and grouped operations call the project's own host methods.

Built assets distribute this notice, original notice/license, pinned source excerpts and modified renderer source in `codex-source/`. Complete frontend build inputs are distributed in `pi-desktop-source/rebuild/` because the used Pi renderer remains under LGPL-3.0; see its own notice for replacement/rebuild instructions. The unpacked project also provides the Python host adaptations and tests. This record makes no claim that inaccessible desktop UI source was copied or that the current checkout is a release/installer.
