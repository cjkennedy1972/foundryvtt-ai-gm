"""Allowlist sanitiser for HTML posted as a Foundry ChatMessage.

Narration, NPC speech and roll flavor come from the LLM (and, indirectly, from
players), and Foundry renders ChatMessage content as HTML. Anything outside the
allowlist is escaped to visible text rather than dropped, so nothing the model
wrote silently vanishes.
"""
import html
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset({
    "b", "strong", "i", "em", "u", "br", "p", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "hr", "blockquote", "code", "pre", "a",
})
_VOID = frozenset({"br", "hr"})
_SAFE_SCHEMES = ("http", "https")


def _safe_href(value: str) -> bool:
    # Browsers strip tabs/newlines/control chars inside a scheme, so do the same before checking.
    v = "".join(ch for ch in (value or "") if ch.isprintable() and not ch.isspace()).lower()
    head = v.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if ":" not in head:
        return not v.startswith("//")  # relative; protocol-relative is an off-site link, refuse
    return head.split(":", 1)[0] in _SAFE_SCHEMES


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self.open: list = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.out.append(html.escape(self.get_starttag_text() or ""))
            return
        parts = [tag]
        for name, value in attrs:
            value = value or ""
            if name == "class":
                parts.append(f'class="{html.escape(value)}"')
            elif name == "href" and tag == "a" and _safe_href(value):
                parts.append(f'href="{html.escape(value)}" rel="noopener"')
        self.out.append("<" + " ".join(parts) + ">")
        if tag not in _VOID:
            self.open.append(tag)

    def handle_endtag(self, tag):
        if tag in ALLOWED_TAGS and tag not in _VOID:
            if tag in self.open:
                while self.open:
                    top = self.open.pop()
                    self.out.append(f"</{top}>")
                    if top == tag:
                        break
            return  # stray close tag: drop
        if tag not in ALLOWED_TAGS:
            self.out.append(html.escape(f"</{tag}>"))

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    # Comments, doctype and processing instructions carry nothing worth keeping.
    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


def sanitize_chat_html(text) -> str:
    """Return `text` with every tag/attribute outside the allowlist escaped."""
    if not text:
        return "" if text is None else str(text)
    parser = _Sanitizer()
    parser.feed(str(text))
    parser.close()
    while parser.open:
        parser.out.append(f"</{parser.open.pop()}>")
    return "".join(parser.out)
