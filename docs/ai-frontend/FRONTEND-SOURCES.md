# Modern frontend sources and adaptation boundaries

UI maintenance starts with applicable public Codex / PI-Desktop code and existing dependencies. Record copied files, extracted fragments and project-local interaction ports separately. Public protocol source does not establish access to unpublished desktop renderer source.

## Actual dependencies

Versions are pinned in [`ai/frontend/package.json`](../../ai/frontend/package.json) and its `package-lock.json` (resolved URLs and integrity hashes). Host dependencies are in [`ai/requirements.txt`](../../ai/requirements.txt).

| Capability | Mature implementation / version | Project adaptation | License / source |
| --- | --- | --- | --- |
| Chat runtime, message list, composer state, attachments, scroll following | `@assistant-ui/react` 0.15.23 | `Chat.tsx` external-store runtime; `store.ts` owns host state; no second Agent runtime | MIT; https://github.com/assistant-ui/assistant-ui |
| UI | React / React DOM 19.1.1 | Session sidebar, settings dashboard and task-specific actions | MIT; https://github.com/facebook/react |
| Rich editor / IME / document model | Tiptap core/pm 3.31.4, react/starter-kit 3.6.6; ProseMirror Markdown 1.13.2 | `Composer.tsx`, `editor.ts`: Markdown serialization, IME commit guard, shortcuts, input history, attachment tags | MIT; https://github.com/ueberdosis/tiptap and https://github.com/ProseMirror/prosemirror-markdown |
| Markdown / tables / math | react-markdown 10.1.0, remark-gfm 4.0.1, remark-math 6.0.0 | Safe host-mediated links; external images and raw HTML blocked | MIT; https://github.com/remarkjs/react-markdown |
| Sanitization | rehype-sanitize 6.0.0, DOMPurify 3.2.7 | Sanitize Markdown tree and Mermaid SVG; no executable attachments | MIT (rehype); Apache-2.0 OR MPL-2.0 (DOMPurify); https://github.com/cure53/DOMPurify |
| Math / code | KaTeX 0.16.22, rehype-katex 7.0.1; highlight.js 11.11.1, rehype-highlight 7.0.2 | Offline fonts/styles; trust disabled; copy actions | MIT (KaTeX/rehype); BSD-3-Clause (highlight.js) |
| Diagrams | Mermaid 11.12.0 | Copy official browser ESM and lazy chunks into `public/vendor/mermaid`; load from host origin; strict mode and SVG sanitization | MIT; https://github.com/mermaid-js/mermaid |
| Icons | lucide-react 0.468.0 | Local SVG icons | ISC; https://github.com/lucide-icons/lucide |
| History dropdown submenus | `@radix-ui/react-dropdown-menu` 2.1.24, pinned directly | Official Sub/Portal/RadioGroup composition; existing Pi menu styles; host sort/group preferences | MIT; https://www.radix-ui.com/primitives/docs/components/dropdown-menu |
| Desktop shell / .NET bridge | pywebview 6.2.1; pythonnet 3.2.0 | WebView2, loopback assets, source-checked single RPC facade | BSD-3-Clause (pywebview); MIT (pythonnet) |
| Build / tests | Vite 6.3.6, TypeScript 5.9.3, Vitest 3.2.4, Playwright 1.56.1 | Offline production output; bounded fixtures and desktop acceptance | MIT (Vite/Vitest); Apache-2.0 (TypeScript/Playwright) |

The dependency notices generator preserves package-root LICENSE/NOTICE/COPYING texts and KaTeX font notices in `ai/frontend/public/THIRD-PARTY-NOTICES.txt`, also shipped in `dist`. It includes installed non-development dependencies, not a precise tree-shaken bundle inventory. Packages lacking a root license text are explicitly identified with their source URLs; it does not claim to resolve all redistribution obligations automatically.

## Windows desktop publication

Windows desktop publication must satisfy the [integrity design](windows-integrity-design.md): the final EXE's security label and the launched process token are part of runtime correctness. Rebuilding React assets and replacing a desktop EXE are distinct deployment steps.

## Fixed source provenance

PI-Desktop baseline: [0d47d26769ecbeca1c3ab56fa83b58a91de8190e](https://github.com/vastsa/PI-Desktop/tree/0d47d26769ecbeca1c3ab56fa83b58a91de8190e), desktop package 0.16.0, LGPL-3.0. Exact originals and adaptation/rebuild boundaries are retained in [Pi NOTICE](../../ai/third_party/pi-desktop/NOTICE.md). ContextMenu, menu positioning, scrollbar reveal, sidebar grouping/hover/preview and relevant CSS are extracted or adapted under `src/pi/`. Search composition-key bypasses and input styling come from `SearchDialog.tsx`, `useAppShellRuntime.tsx` and `styles/overlays.css`; originals are under `upstream/search/`.

The finalized neutral WebView appearance is implemented in the production styles and components, following [the accepted design](WEBVIEW-UI-DESIGN.zh-CN.md). `src/composer-layout.ts` adapts the measured-content idea from PI-Desktop `Composer.tsx` at the fixed LGPL-3.0 baseline above; its inert fixed-compact-width probe and expand/shrink policy are local additions. The transcript-only 14px/18px fade adapts `chat-shell.css` from the same baseline. Single-layer composer blur was informed by [Codex-Dream-Skin windows-shell.css](https://github.com/Fei-Away/Codex-Dream-Skin/blob/6fc42df48a6a5365011f55cc349e20c038e56d1d/tools/windows-shell.css); no code from that project is bundled. Shadows and final visual values are local. No preview wallpaper, transform plugin, demo adapter or fixture was added to the production path. The visual target is not a claim of access to the unpublished Codex desktop renderer.

Window sizing, disclosure anchoring, transcript menu vocabulary, slash completion, mount windows, reading anchors and minimap use project-local implementations informed by Pi interaction references. ComposerStatus is project-local. Reading anchors and layout persistence use host RPC and SQLite/preferences; WebView2 private mode does not provide durable browser storage. No Pi application store, Electron IPC, plugin system, work panel or Agent runtime is bundled.

The fixed Pi baseline's [`ContextUsageInspector.tsx`](https://github.com/vastsa/PI-Desktop/blob/0d47d26769ecbeca1c3ab56fa83b58a91de8190e/apps/desktop/src/components/ContextUsageInspector.tsx) displays usage from the last model request, as described by [ADR 0193](https://github.com/vastsa/PI-Desktop/blob/0d47d26769ecbeca1c3ab56fa83b58a91de8190e/docs/adr/0193-last-request-context-occupancy.md). It does not subscribe to composer text. This project's `ComposerStatus.tsx` instead shows a host estimate for the next turn, including the draft and completed text attachments. Same-session edits retain the last valid estimate during the 350ms debounce and pending RPC; session/configuration changes invalidate it, and a failed refresh displays the error. This distinction is a local adaptation, not missing upstream draft-refresh code.

Codex baseline: [ab45264919aaeb8a421cc156f1a5459ef9d60b72](https://github.com/openai/codex/tree/ab45264919aaeb8a421cc156f1a5459ef9d60b72), Apache-2.0. Exact originals, Git blob identities, SHA-256 and adaptations are retained in [Codex NOTICE](../../ai/third_party/codex/NOTICE.md). Thread section/appearance, sorting and archive/delete/source protocols are copied unchanged where identified in that notice. Host section membership, fork and archive aggregation adapt public semantics to the existing SQLite service. Renderer menus and archive pages use this project's assistant-ui/Pi/Radix components; public Codex source does not include these desktop React pages.

Search adapts Codex `tui/src/task_mentions.rs`, `rollout/src/search.rs`, `thread-store/src/local/search_threads.rs` and `thread-store/src/local/thread_history/search.rs` to React/Python/SQLite. The sidebar receives compact session IDs; the transcript uses a UTF-16 literal matcher and CSS highlights. Search limits and stability status are recorded in the module comparison scripts under `ai/tests`.

Production source distributions are generated under `dist/pi-desktop-source/` and `dist/codex-source/` with originals, modified files, license/notice materials and frontend rebuild inputs as defined by build scripts. Source checks should compare these outputs to their recorded fixed origins.

SillyTavern (AGPL-3.0) is an interaction reference for numeric sampling/token controls; its runtime and styles are not bundled.

## Model metadata

Four OpenAI model records and local budget handling use the fixed `@mariozechner/pi-ai` 0.73.1 publication. Exact publication hash, gitHead, MIT text provenance and endpoint restrictions are in [model sources](PI-MODEL-SOURCES.md). Python Pydantic AI/Graph and the local client remain the executors; same-named gateway models do not inherit official endpoint metadata.

Current module limits and host implementation parameters are described in [desktop host](CURRENT-HOST.zh-CN.md); they do not establish additional product policy.
