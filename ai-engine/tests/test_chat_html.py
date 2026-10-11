"""Outbound chat HTML is allowlist-sanitised (FoundryClient.chat_message and roll flavor)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from foundry.client import FoundryClient
from utils.chat_html import sanitize_chat_html as san


def test_exact_passthrough():
    assert san("<b>bold</b> <i>x</i><br>") == "<b>bold</b> <i>x</i><br>"
    assert san('<p class="n">hi</p>') == '<p class="n">hi</p>'


@pytest.mark.parametrize("evil", [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    "<iframe src='//evil'></iframe>",
    "<svg onload=alert(1)>",
])
def test_dangerous_tags_are_escaped_not_emitted(evil):
    out = san(evil)
    assert "<" not in out and ">" not in out
    assert "&lt;" in out


def test_plain_text_markdown_emoji_unicode_unchanged():
    assert san("**md** 🎲 ünïcode — ok") == "**md** 🎲 ünïcode — ok"


def test_event_and_style_attributes_dropped_on_allowed_tags():
    assert san('<b onclick="x()" style="a:b">t</b>') == "<b>t</b>"


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", " java\tscript:alert(1)", "data:text/html,x",
    "vbscript:x", "//evil.example/x", "&#106;avascript:alert(1)",
])
def test_unsafe_hrefs_dropped(href):
    out = san(f'<a href="{href}">x</a>')
    assert "href" not in out and out == "<a>x</a>"


def test_safe_hrefs_get_noopener():
    assert san('<a href="/journal/x">x</a>') == '<a href="/journal/x" rel="noopener">x</a>'
    assert san('<a href="http://a.b">x</a>') == '<a href="http://a.b" rel="noopener">x</a>'


def test_malformed_and_nested_tags():
    assert san("<b><i>x</b>") == "<b><i>x</i></b>"
    assert san("</p>stray") == "stray"
    assert san("<b>unclosed") == "<b>unclosed</b>"
    assert san("1 < 2 & 3 > 2") == "1 &lt; 2 &amp; 3 &gt; 2"
    out = san("<<script>script>alert(1)</script>")
    assert "<script" not in out
    assert san("<!-- c --><b>x</b>") == "<b>x</b>"
    assert "<b" not in san("<b").replace("&lt;b", "")


def test_quote_breakout_in_class_is_escaped():
    out = san('<p class="x" onmouseover="y">t</p>')
    assert out == '<p class="x">t</p>'
    out = san("<p class='a\"b'>t</p>")
    assert 'a&quot;b' in out


def test_empty_and_none():
    assert san("") == "" and san(None) == ""


def test_chat_message_sends_sanitised_content():
    c = FoundryClient.__new__(FoundryClient)
    c._send = AsyncMock(return_value={"id": "m"})
    asyncio.run(c.chat_message('<b>hi</b><script>x</script><img src=x onerror=y>', whisper=["u1"]))
    sent = c._send.await_args.kwargs["content"]
    assert sent.startswith("<b>hi</b>") and "<script" not in sent and "<img" not in sent
    assert c._send.await_args.kwargs["whisper"] == ["u1"]


def test_roll_flavor_is_sanitised():
    from actions.executors import execute_roll

    f = MagicMock()
    f.roll = AsyncMock(return_value={"total": 3})
    with patch("actions.executors._is_player_character", AsyncMock(return_value=False)):
        asyncio.run(execute_roll("1d20", "Goblin", flavor="<img src=x onerror=y>Hit", foundry=f))
    flavor = f.roll.await_args.kwargs["flavor"]
    assert "<img" not in flavor and "Hit" in flavor
