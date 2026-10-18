from campaign.lore_lookup import look_from_wikitext

PAGE = """{{Infobox_Person|name=Tas|race=[[Kender]]|hair color=Brown|eye color=Brown}}
'''Tas''' was a kender.
==Personality and traits==
Tas was just under four feet tall, thinly built with narrow pointed ears and brown hair worn in a topknot. He loved maps.
"""


def test_look_reads_infobox_and_appearance_sentences():
    look = look_from_wikitext(PAGE)
    assert look.startswith("Kender, brown hair, brown eyes")
    assert "topknot" in look and "maps" not in look and "==" not in look


def test_page_with_nothing_visual_gives_none():
    assert look_from_wikitext("'''Bob''' was a clerk who liked ledgers.") is None
