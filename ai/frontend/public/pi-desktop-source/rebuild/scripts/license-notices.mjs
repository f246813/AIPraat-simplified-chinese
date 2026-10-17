// Preserve the shipped packages' own license/notice texts, not just SPDX labels.
import { readFileSync, readdirSync, existsSync, mkdirSync, writeFileSync, cpSync } from 'node:fs';
import { join } from 'node:path';
const lock = JSON.parse(readFileSync('package-lock.json', 'utf8'));
const sections = ['Third-party dependency notices\nGenerated from package-lock.json; includes installed non-development dependencies.\nPI-Desktop renderer excerpts (LGPL-3.0) are also bundled: see pi-desktop-source/NOTICE.md and the replaceable source there. No SillyTavern code is bundled.\n'];
sections.push('Codex public thread-section protocol/source (Apache-2.0) is bundled in codex-source/: see NOTICE.md, LICENSE, UPSTREAM-NOTICE and SOURCE.json. Desktop sidebar/menu rendering is a local adaptation.\n');
let count = 0;
for (const [path, entry] of Object.entries(lock.packages).sort(([a], [b]) => a.localeCompare(b))) {
  if (!path || entry.dev || !existsSync(join(path, 'package.json'))) continue;
  const pkg = JSON.parse(readFileSync(join(path, 'package.json'), 'utf8'));
  const license = typeof pkg.license === 'string' ? pkg.license : entry.license || 'See package source';
  if (/\b(?:A?GPL|LGPL)[- ]/i.test(license)) throw new Error(`Review copyleft dependency before shipping: ${pkg.name} ${license}`);
  const files = readdirSync(path, {withFileTypes:true}).filter(f => f.isFile() && /^(licen[sc]e|copying|notice|copyright)(\.|$)/i.test(f.name));
  sections.push(`\n${'='.repeat(76)}\n${pkg.name} ${pkg.version}\nLicense: ${license}\nSource: ${entry.resolved || pkg.homepage || path}\n`);
  for (const file of files) sections.push(`\n--- ${file.name} ---\n${readFileSync(join(path, file.name), 'utf8')}\n`);
  if (!files.length) sections.push('License text not included at package root; consult the source URL above.\n');
  count++;
}
// Lucide icons are ISC; the package ships a combined LICENSE. KaTeX contains font notices.
for (const path of ['node_modules/katex/dist/fonts']) {
  if (!existsSync(path)) continue;
  for (const file of readdirSync(path).filter(f => /license|copying/i.test(f))) sections.push(`\n--- ${path}/${file} ---\n${readFileSync(join(path,file),'utf8')}\n`);
}
mkdirSync('public', {recursive:true});
// Explicit renderer-source distribution, separate from the npm dependency audit.
cpSync('../third_party/pi-desktop', 'public/pi-desktop-source', {recursive:true});
cpSync('src/pi', 'public/pi-desktop-source/modified', {recursive:true});
cpSync('../third_party/codex', 'public/codex-source', {recursive:true});
cpSync('src/codex', 'public/codex-source/modified', {recursive:true});
cpSync('src/HistoryOrganization.tsx', 'public/codex-source/modified/HistoryOrganization.tsx');
cpSync('src/SidebarOptions.tsx', 'public/codex-source/modified/SidebarOptions.tsx');
mkdirSync('public/codex-source/modified/transcript', {recursive:true});
for (const file of ['Chat.tsx','SearchBar.tsx','styles.css']) cpSync(`src/${file}`, `public/codex-source/modified/transcript/${file}`);
mkdirSync('public/codex-source/modified-host', {recursive:true});
for (const file of ['modern_organization.py','modern_store.py','modern_app.py','modern_search.py','modern_settings.py']) cpSync(`../praat_ai/${file}`, `public/codex-source/modified-host/${file}`);
for (const file of ['NOTICE.md','LICENSE','UPSTREAM-NOTICE']) sections.push(`\n--- Codex ${file} ---\n${readFileSync(`../third_party/codex/${file}`, 'utf8')}\n`);
for (const file of ['NOTICE.md','LICENSE','COPYING']) sections.push(`\n--- PI-Desktop ${file} ---\n${readFileSync(`../third_party/pi-desktop/${file}`, 'utf8')}\n`);
for (const directory of ['src','tests','scripts']) cpSync(directory, `public/pi-desktop-source/rebuild/${directory}`, {recursive:true});
for (const file of ['package.json','package-lock.json','tsconfig.json','vite.config.ts','playwright.config.ts','index.html']) cpSync(file, `public/pi-desktop-source/rebuild/${file}`);
writeFileSync('public/THIRD-PARTY-NOTICES.txt', sections.join(''), 'utf8');
console.log(`Generated notices for ${count} installed production packages.`);
