# Preview-only UI references

The repository's production files were not edited for this preview. The Vite
plugin transforms components in memory and loads a separate appearance sheet.

- Codex screenshot supplied by the user: header proportions, folder placement,
  neutral compact controls, top/bottom fade appearance. The official public
  https://github.com/openai/codex repository did not expose the matching desktop
  React renderer in the search performed. These details are visual adaptations,
  not copied desktop source.
- PI-Desktop `chat-shell.css`: scroll-port mask-image technique, adapted to
  shorter 14px top / 18px bottom transitions; minimap siblings are not masked.
  https://github.com/vastsa/PI-Desktop/blob/0d47d26769ecbeca1c3ab56fa83b58a91de8190e/apps/desktop/src/styles/chat-shell.css
- PI-Desktop `Composer.tsx`: measuring rendered content and computed line height
  rather than counting characters. The stable compact-width Tiptap probe and
  compact / expanded width switching are local preview additions.
  https://github.com/vastsa/PI-Desktop/blob/0d47d26769ecbeca1c3ab56fa83b58a91de8190e/apps/desktop/src/components/Composer.tsx
  PI-Desktop's existing LGPL-3.0 provenance remains applicable to adaptations.
- Codex-Dream-Skin (unofficial theme), `tools/windows-shell.css`: one outer
  composer blur layer, blur(16px) saturate(108%). Surrounding shadows are locally
  tuned, without an inset border.
  https://github.com/Fei-Away/Codex-Dream-Skin/blob/6fc42df48a6a5365011f55cc349e20c038e56d1d/tools/windows-shell.css

Layout: demo conversation band and compact composer default to 720px. The
composer is wider than the actual reading text. Above three lines its maximum
grows by 96px, limited by available width. Innermost history rows move left 4px.
Header actions gain a faint divider; sidebar toggle gains an explicit panel icon.
Existing conversation-width handles still control the full conversation band.
Whole-window transparency remains a browser simulation of background color;
host/native window transparency has not been changed.

## Accepted style extended to WebView window family

- Chat title: 14px at weight 600, unitless 1.5 line height, 42px header.
- Sidebar header: 18/16/14px padding and 8px between header action buttons.
- Main surface: 95% opacity (previously 97.5%); sidebar: 93% (previously 96%).
- Settings: every existing category keeps its component, fields and callbacks.
- Standalone routes: pronunciation, dictionaries/models and Anki share the
  appearance sheet, as does the MFA resource configuration dialog.
- Settings main/navigation paint as separate surfaces so their opacities do
  not stack over a second already-opaque parent surface.
- Standalone demo routes append preview-window-fixtures.ts only in memory;
  reuse existing project fixtures and do not invoke native actions.
- Browser routes on port 5191: `/?demo=1`, `/?window=pronunciation&demo=1`,
  `/?window=dictionaries&demo=1`, `/?window=anki&demo=1`.

Browser checks: category navigation, original advanced disclosures, IPA rule
selection, resource deletion confirmation, unsaved resource draft discard,
Anki entry selection, horizontal overflow at desktop/small sizes, green chrome
audit, and page errors. Host/model operations remain outside appearance preview.

## Fine shadows and compact switches

Native checkboxes are drawn as 32x20px switches with a 16px thumb, except controls
whose accessible name contains 锁定 or locked. Existing native checked/disabled
state, label targeting, keyboard behavior, and React change callbacks remain.
Primary buttons use near-black gray #3d4044. Send remains blue; deletion stays red.
Text fields use a 1px focus outline. White main panels use two low-opacity shadow
layers; gray is reserved primarily for selection, navigation and small notices.
The capture checks keyboard Space and label toggling, native lock appearance,
switch width, text-field focus width, original route interactions and layout.

## Final formula typography

Uses the project's existing remark-math / rehype-katex renderer and local KaTeX
fonts. Inline and display equations use black, 1.2em, weight 500, slight oblique.
Roman math letter groups (including uppercase VOT) also use italic styling.
Display equations have no gray shell and a 2px near-black left rule (#34363a).
Only math-containing quote backgrounds are removed; ordinary quoted prose and
code blocks retain their existing behavior. The VOT preview now uses real math
notation rather than plain text in a quote. No model prompts or production files
were changed. Latest family preview: lighter-formula-review.html.
