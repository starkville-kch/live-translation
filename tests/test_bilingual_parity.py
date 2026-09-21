import os
import re
from html.parser import HTMLParser
from pathlib import Path
import pytest

HANGUL_RE = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")
EXEMPT_MARKER = "{# i18n-exempt #}"


class TemplateNode:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children = []
        self.texts = []

    def has_descendant_with_attr(self, attr_name, attr_val):
        if self.attrs.get(attr_name) == attr_val:
            return True
        for c in self.children:
            if c.has_descendant_with_attr(attr_name, attr_val):
                return True
        return False


class TemplateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = TemplateNode("root", {})
        self.current = self.root
        self.hangul_nodes = []  # list of (text, node)

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        node = TemplateNode(tag, attr_dict, parent=self.current)
        self.current.children.append(node)
        # Avoid nesting children for void elements
        if tag not in ("meta", "link", "img", "br", "hr", "input"):
            self.current = node

    def handle_endtag(self, tag):
        if self.current.parent is not None and self.current.tag == tag:
            self.current = self.current.parent

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        self.current.texts.append(text)
        if HANGUL_RE.search(text):
            # Exclude script and style contents
            ancestor = self.current
            is_ignored = False
            while ancestor and ancestor.tag != "root":
                if ancestor.tag in ("script", "style", "head", "title"):
                    is_ignored = True
                    break
                ancestor = ancestor.parent
            if not is_ignored:
                self.hangul_nodes.append((text, self.current))


def test_template_hangul_parity():
    """Verify that every text node containing Hangul in app/templates:
    1. Sits inside an element with data-lang="ko" (or has ancestor with data-lang="ko").
    2. That element has a data-lang="en" counterpart within the same parent element scope.
    """
    templates_dir = Path(__file__).resolve().parent.parent / "app" / "templates"
    template_files = list(templates_dir.rglob("*.html"))
    assert len(template_files) >= 12, f"Expected at least 12 template files, found {len(template_files)}: {[f.name for f in template_files]}"

    violations = []
    total_hangul_nodes = 0

    for path in template_files:
        content = path.read_text(encoding="utf-8")

        # Skip Jinja comments marked with i18n-exempt
        # Also remove whole lines marked with {# i18n-exempt #}
        clean_lines = []
        for line in content.splitlines():
            if EXEMPT_MARKER in line:
                continue
            clean_lines.append(line)
        clean_content = "\n".join(clean_lines)

        # Strip remaining jinja comments and control tags for HTML parsing
        clean_content = re.sub(r"\{#.*?#\}", "", clean_content, flags=re.DOTALL)

        parser = TemplateParser()
        try:
            parser.feed(clean_content)
        except Exception as e:
            pytest.fail(f"Failed to parse template {path.name}: {e}")

        total_hangul_nodes += len(parser.hangul_nodes)

        for text, node in parser.hangul_nodes:
            # Check if any ancestor has data-lang="ko"
            ko_ancestor = None
            curr = node
            while curr and curr.tag != "root":
                if curr.attrs.get("data-lang") == "ko":
                    ko_ancestor = curr
                    break
                curr = curr.parent

            rel_path = path.relative_to(templates_dir)

            if not ko_ancestor:
                violations.append(
                    f"[{rel_path}] Hangul text '{text}' is not inside an element with data-lang='ko'"
                )
                continue

            # Ensure ko_ancestor's parent has a data-lang="en" counterpart
            parent = ko_ancestor.parent
            if not parent:
                violations.append(
                    f"[{rel_path}] data-lang='ko' element enclosing '{text}' has no parent to pair with data-lang='en'"
                )
                continue

            has_en = any(
                child.has_descendant_with_attr("data-lang", "en")
                for child in parent.children
                if child != ko_ancestor
            )
            if not has_en:
                violations.append(
                    f"[{rel_path}] data-lang='ko' enclosing '{text}' has no data-lang='en' counterpart under parent <{parent.tag}>"
                )

    assert total_hangul_nodes >= 50, f"Expected at least 50 Hangul nodes checked across templates, found only {total_hangul_nodes}"

    if violations:
        msg = f"Found {len(violations)} bilingual parity violations in templates:\n" + "\n".join(violations)
        pytest.fail(msg)
