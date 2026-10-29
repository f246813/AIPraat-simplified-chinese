"""Current maintenance entry points must not link to removed workflows."""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]


class MaintenanceDocsTests(unittest.TestCase):
    def test_local_links_in_current_project_documents_resolve(self):
        paths = []
        for directory in ("docs", "ai/docs"):
            paths.extend(p for p in (ROOT / directory).rglob("*.md")
                         if "manual" not in p.parts and "webview-ui-design" not in p.parts)
        paths.extend((ROOT / "installer/verification").glob("*.md"))
        paths.extend(ROOT / p for p in (
            "README.md", "CREDITS.zh-CN.md", "ai/README.zh-CN.md",
            "ai/Anki连接说明.md", "ai/anki_addon/README.md", "ai/plugin/README.zh-CN.md",
            "installer/README.zh-CN.md", "guide.md", "test-records/README.md",
            "HOW_TO_BUILD_ONE.md", "HOW_TO_BUILD_ALL.md"))
        if (ROOT / "AGENTS.md").is_file():
            paths.append(ROOT / "AGENTS.md")
        errors = []
        for path in paths:
            source = path.read_text(encoding="utf-8-sig")
            for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", source):
                target = match[1].strip().strip("<>").split(' "')[0]
                if re.match(r"^[a-zA-Z]+:", target) or target.startswith("#"):
                    continue
                local = unquote(target.split("#")[0])
                if not (path.parent / local).exists():
                    errors.append(f"{path.relative_to(ROOT)}: {target}")
        self.assertFalse(errors, "失效文档链接：\n" + "\n".join(errors))
