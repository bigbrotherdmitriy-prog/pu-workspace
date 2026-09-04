from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import re


ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "div", "em", "font", "h1", "h2", "h3",
    "i", "li", "ol", "p", "s", "span", "strong", "u", "ul",
}
BLOCKED_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "form", "input"}
BLOCK_TAGS = {"blockquote", "div", "h1", "h2", "h3", "li", "ol", "p", "ul"}
SAFE_FONTS = {"arial", "calibri", "georgia", "tahoma", "times new roman", "verdana"}
COLOR_RE = re.compile(r"^(?:#[0-9a-f]{3}(?:[0-9a-f]{3})?|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)|[a-z]{3,20})$", re.I)
SIZE_RE = re.compile(r"^(?:[89]|[1-6]\d|7[0-2])px$", re.I)


def _safe_style(value: str) -> str:
    safe: list[str] = []
    for declaration in value.split(";"):
        name, separator, raw = declaration.partition(":")
        if not separator:
            continue
        name = name.strip().casefold()
        raw = raw.strip()
        if name == "color" and COLOR_RE.fullmatch(raw):
            safe.append(f"color:{raw}")
        elif name == "font-size" and SIZE_RE.fullmatch(raw):
            safe.append(f"font-size:{raw}")
        elif name == "font-family" and raw.strip(" '\"").casefold() in SAFE_FONTS:
            safe.append(f"font-family:{raw.strip()}")
        elif name == "text-align" and raw.casefold() in {"left", "center", "right", "justify"}:
            safe.append(f"text-align:{raw.casefold()}")
    return ";".join(safe)


class _SafeMailComposerHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocked_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.casefold()
        if tag in BLOCKED_TAGS:
            self.blocked_depth += 1
            return
        if self.blocked_depth or tag not in ALLOWED_TAGS:
            return
        allowed: list[str] = []
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        style = _safe_style(values.get("style", ""))
        if style:
            allowed.append(f' style="{escape(style, quote=True)}"')
        if tag == "a":
            href = values.get("href", "").strip()
            if href.casefold().startswith(("https://", "http://", "mailto:")):
                allowed.append(f' href="{escape(href, quote=True)}"')
                allowed.append(' rel="noopener noreferrer"')
        if tag == "font":
            color = values.get("color", "").strip()
            face = values.get("face", "").strip()
            size = values.get("size", "").strip()
            if COLOR_RE.fullmatch(color):
                allowed.append(f' color="{escape(color, quote=True)}"')
            if face.casefold() in SAFE_FONTS:
                allowed.append(f' face="{escape(face, quote=True)}"')
            if size in {"1", "2", "3", "4", "5", "6", "7"}:
                allowed.append(f' size="{size}"')
        self.parts.append(f"<{tag}{''.join(allowed)}>")

    def handle_startendtag(self, tag: str, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str):
        tag = tag.casefold()
        if tag in BLOCKED_TAGS:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if not self.blocked_depth and tag in ALLOWED_TAGS and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str):
        if not self.blocked_depth:
            self.parts.append(escape(data))


class _MailPlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocked_depth = 0

    def handle_starttag(self, tag: str, _attrs):
        tag = tag.casefold()
        if tag in BLOCKED_TAGS:
            self.blocked_depth += 1
        elif not self.blocked_depth and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.casefold()
        if tag in BLOCKED_TAGS:
            self.blocked_depth = max(0, self.blocked_depth - 1)
        elif not self.blocked_depth and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self.blocked_depth:
            self.parts.append(data)


def sanitize_mail_html(value: str) -> str:
    parser = _SafeMailComposerHTML()
    parser.feed(value[:50_000])
    parser.close()
    return "".join(parser.parts).strip()


def mail_html_to_text(value: str) -> str:
    parser = _MailPlainText()
    parser.feed(value)
    parser.close()
    return "\n".join(line.rstrip() for line in "".join(parser.parts).splitlines() if line.strip()).strip()


def is_rich_mail_body(value: str) -> bool:
    return bool(re.search(r"<(?:p|div|br|strong|b|em|i|u|s|ul|ol|li|blockquote|h[1-3]|span|font|a)(?:\s|>|/)", value, re.I))
