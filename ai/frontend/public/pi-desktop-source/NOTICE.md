# PI-Desktop renderer excerpts / LGPL-3.0

This distribution uses renderer-only excerpts from **PI-Desktop**, by its upstream authors/contributors, licensed under GNU LGPL version 3. The excerpts and our modifications remain under LGPL-3.0. See `LICENSE` (LGPL-3.0) and `COPYING` (GPL-3.0, incorporated by LGPL).

Source: https://github.com/vastsa/PI-Desktop
Fixed revision: `0d47d26769ecbeca1c3ab56fa83b58a91de8190e` (research checkout, not a claim to match the user's installed Pi version).

Original files are preserved in `upstream/`:
- `apps/desktop/src/components/ContextMenu.tsx`
- `apps/desktop/src/lib/context-menu.ts`
- `apps/desktop/src/lib/scrollbar-reveal.ts`
- `apps/desktop/src/styles/base.css` (scrollbar rules, lines 126–161)
- `apps/desktop/src/styles/ui-kit.css` (context menu, lines 256–340)

Local modifications:
- ContextMenu: direct React body portal; remove transcript/TeX selection integrations; explicit sidebar point/trigger; preserve viewport placement, dismissal, focus restoration and keyboard navigation; prevent the trigger's pointerdown from defeating click-to-toggle; avoid refocusing on metadata renders.
- Placement: retain Pi's viewport clamp/rounding, expose only renderer types needed here.
- Scrollbar reveal: logic unchanged, comments/formatting shortened; capture passive scroll and 300ms hold, including timer/attribute cleanup.
- CSS: map Pi theme variables to this project's tokens; reset local button defaults; restore `scrollbar-width/color: auto` to prevent older project rules from overriding the 6px WebKit scrollbar. Quiet transparent track/thumb; hover/focus/scroll reveal.

Project-local `SessionRow` and `sessions.pin` implement dialogue operations using the Praat host, not Pi's Sidebar/store/IPC/Agent runtime. Composer status is project-local code.

## Conversation sidebar extraction

The complete upstream `Sidebar.tsx` and its sidebar dependencies are preserved
unchanged in `upstream/sidebar/`, at the same fixed revision above. That directory
includes `SessionHoverCard.tsx`, `useSessionHoverCard.ts`, the session grouping,
preferences, resizing, status and armed-delete helpers, and the original sidebar
styles. Files there are reference/replacement source, not all executed imports.

The used renderer source is `ai/frontend/src/pi/sidebar/` (also shipped in
`modified/sidebar/`):
- `sidebar-session-groups.ts`: original logic; type imports point to the Praat
  session projection, with an SPDX header added.
- `useSessionHoverCard.ts`: original hook and timings, redirected session type
  import and SPDX header. The adapted hook cancels pending dismissal before the
  same-target early return, so reentering a row keeps its preview visible.
- `use-armed-delete.ts`: original timed confirmation helper.
- `preview.ts`: upstream preview truncation and viewport placement logic,
  extracted from the original component.
- `upstream.css`: original sidebar-thread rules and sidebar-specific session
  rules, extracted with their selectors and tokens retained.
- `Sidebar.tsx`, `SessionHoverCard.tsx`, `types.ts`, and `styles.css`: adapted
  conversation-only renderer/controller and Praat data projection. Pi's compact
  rows, pin area, time groups, hover timings, modifier/range selection, menus,
  sorting and resizing are connected to the existing Praat ChatStore/RPC.
  Hover preview loads without selecting, and retains upstream pointer/focus
  handling. Name/date sorting persists via a validated host preference. Group
  labels are translated; historical time groups can collapse independently.
  Single deletion retains the existing confirmation dialog; multiple deletion
  uses Pi's timed arm, bound to the selected ID set, with readonly/running guards.
  Sidebar Ctrl/Cmd+B ignores editable controls. Existing sidebar resizing remains
  connected to the Praat host's width preference and keyboard controls.
- Usability adaptation: the native history scrollbar stays at the inside frame
  edge with a proportional thumb, but has a 14px pointer lane instead of Pi's
  6px lane. Its quiet thumb is 6px and grows to 10px on direct hover/press. The
  resize handle sits outside this lane. Pi's reveal/hold behavior is retained.
  The sidebar style sets the sidebar thumb's minimum height to
  68px (the measured 17px original native minimum plus three times that length).

The existing `SessionRow.tsx` consumes these LGPL renderer interactions and is
supplied in `rebuild/src/`: 120ms prefetch, preview hooks, compact rows, modifier
selection and the extracted context menu. The App/sidebar integration, host
validation and complete build inputs are likewise supplied in `rebuild/`.
No Pi Agent kernel, global Pi store, Electron IPC, project-management or
notification subsystem is bundled into the application runtime. Conversation archive operations are implemented by this project's own host.

## Codex history organization integration

`ContextMenu.tsx` additionally supports submenu/back markers and ArrowRight /
ArrowLeft navigation. `Sidebar.tsx` integrates local stable section headers,
per-section menus, nullable single membership, private conversation drag payloads,
ordered drop-before and archive actions linked to the settings archive manager. Existing Pi preview, modifiers,
focus navigation, sorting and resizing remain connected to the local host.
The scrollbar lane/minimum thumb settings above are unchanged.
`SessionRow.tsx` adds move/fork/archive actions; `HistoryOrganization.tsx` is a
project-local renderer for public Codex section semantics, not copied Codex
desktop React code. Source/Apache notices are separately in `codex-source/`.

## Search input integration

`upstream/search/` preserves original `SearchDialog.tsx`,
`features/app/useAppShellRuntime.tsx` and `styles/overlays.css`, verified against
the same fixed Git revision (`SOURCE.json` records blob/SHA-256 identities).
The search input's native composition guards (`isComposing` / keyCode 229),
plain text input and spelling/correction/capitalization attributes are reused
in `SearchBar.tsx` and the sidebar search. App/sidebar shortcuts use the same
upstream composition bypass. The upstream `outline: none` rule is applied to
the two search inputs with focus selectors strong enough to override this
project's global focus outline. Keyboard focus on other controls remains.

Only the renderer input/event/style parts are connected to the current host;
Pi's app-store, command runner, project/search IPC and Agent runtime are not
imported. The existing Custom Highlight search implementation is retained.
All used local source is provided in `rebuild/src/`; this section records the
actual copied interaction fragments rather than claiming the full SearchDialog
and its separate search service execute here.

## Sidebar options / origin grouping

`upstream/sidebar-options/` additionally retains the unchanged
`apps/desktop/src/lib/project-name.ts`, with Git blob/SHA-256 provenance at the
same fixed revision. `modified/sidebar/project-name.ts` extracts its exact
`folderNameFromPath` function with an SPDX/source header; origin grouping also
reuses the previously copied `normalizeProjectPath` helper.
`Sidebar.tsx` delegates the toolbar to the Radix dropdown submenu component,
groups ordinary history by recorded project/connection or a single list, and
preserves the existing custom sections, pinned area, time grouping, previews,
selection, scroll and resizing. The original four sort choices are retained.
Menu source/host adapters ship in `rebuild/`; Radix retains its own MIT license.
No Pi project manager or remote execution runtime is imported.

Collapsed rail: `Sidebar.tsx` supplies a settings shortcut. Its gear delegates to the existing host settings
dashboard with the initial conversation/storage category. The existing model
settings entry retains the model category. No Pi navigation/runtime is added;
the current App/Settings source and unchanged expanded search are supplied in
`rebuild/`.

## Final neutral WebView appearance

`sidebar/sidebar-layout.css` and the adapted `Sidebar.tsx` keep the above interactions while moving options into the Praat brand dropdown, exposing search on demand, placing collapse arrows after labels, and retaining an independently collapsible default conversation section. `rebuild/src/composer-layout.ts` uses the content-height measurement idea from upstream `Composer.tsx` at the fixed revision above, with a project-local inert probe measured at the compact width to avoid expand/shrink oscillation. The transcript-only mask in `rebuild/src/styles.css` follows `chat-shell.css` at that same baseline, with local 14px/18px fade lengths. These local adaptations and their full frontend rebuild inputs are supplied in `rebuild/`; no Codex desktop renderer is claimed or shipped.

## Replace / rebuild

Production assets include this notice, both licenses, original excerpts, `modified/` (the exact used LGPL source), and `rebuild/` (frontend source/build inputs). The full project also includes `ai/frontend` and its pinned npm lockfile. You may modify/replace these LGPL renderer portions and reverse engineer the combined work for debugging those modifications; this distribution imposes no contrary restriction.

With Node/npm available, in `rebuild/` (or the full project's `ai/frontend`):
1. `npm ci`
2. `node scripts/offline-assets.mjs`
3. `npx tsc -b && npx vite build`
4. Replace the full project's `ai/frontend/dist` with the rebuilt `dist`, preserving these notices/licenses/source; close and reopen the frontend normally from Praat's menu.

The standalone rebuild recipe skips the full project's prebuild notice-copy step because notices/source are supplied beside `rebuild/`. The full project may use `npm run build` instead. No signature, proprietary loader or Pi IPC is required. The local deliverable is an unpacked source project, not a newly released installer. Any future installer must retain these notices and replacement/rebuild capability.
