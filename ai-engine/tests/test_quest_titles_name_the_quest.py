#!/usr/bin/env python3
"""Every generated quest was titled after an article.

QuestGenerator.generate built its title from the first word of the target:

    title = f"{hook} {target.split()[0].title()}"

Every QUEST_TARGETS entry but one opens with "a" or "the", so the title threw
away the noun and kept the article: "Retrieve A", "Rescue A", "Investigate
The". execute_generate_quest uses that string as the JournalEntry name, so a
GM running a campaign arc gets a journal of eight two-word entries, most of
them duplicates of each other.

Run:
    cd ai-engine && python -m pytest tests/test_quest_titles_name_the_quest.py -v
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from procedural.quests import QuestGenerator

ARTICLES = {"a", "an", "the"}


def _titles():
    """One title per target.

    Steered by each target's last word, which is distinctive enough to select
    it: passing the whole target matches its neighbours on filler words
    ("a village from bandits" also matches "secrets from a rival" on "from").
    The description carries the target verbatim, so it confirms which one the
    steering actually landed on.
    """
    gen = QuestGenerator()
    random.seed(19)

    titles = {}
    for target in QuestGenerator.QUEST_TARGETS:
        quest = gen.generate(theme=target.split()[-1])
        assert target in quest.description, f"steering missed {target!r}: {quest.description}"
        titles[target] = quest.title
    return titles


def test_no_title_is_a_hook_plus_an_article():
    """"Retrieve A" tells the GM nothing about the quest."""
    offenders = {
        target: title
        for target, title in _titles().items()
        if title.split()[-1].lower() in ARTICLES
    }

    assert offenders == {}, f"titles ending in an article: {offenders}"


def test_the_hook_is_not_followed_by_an_article():
    """"Retrieve A Stolen Artifact" reads like a sentence fragment, not a name.

    Only the leading word is checked: "Steal Secrets From A Rival" keeps the
    article that belongs inside the phrase.
    """
    offenders = {}
    for target, title in _titles().items():
        hook = next(h for h in QuestGenerator.QUEST_HOOKS if title.startswith(h))
        subject = title[len(hook):].split()
        if subject and subject[0].lower() in ARTICLES:
            offenders[target] = title

    assert offenders == {}, f"titles leading with an article: {offenders}"


def test_a_title_names_what_the_quest_is_about():
    """The noun the target is built around should survive into the title."""
    missing = {}
    for target, title in _titles().items():
        noun = target.split()[-1]
        if noun.lower() not in title.lower():
            missing[target] = title

    assert missing == {}, f"titles that dropped the target's subject: {missing}"


def test_the_one_target_with_no_article_keeps_its_first_word():
    """"peace between feuding factions" must not lose "peace"."""
    gen = QuestGenerator()
    random.seed(19)

    title = gen.generate(theme="peace between feuding factions").title

    assert "peace" in title.lower(), title


def test_two_different_targets_do_not_get_the_same_name():
    """Journal entries are named after the title, so collisions are useless.

    Compared on the part after the hook: "Retrieve A" and "Rescue A" are
    different strings but name the same nothing.
    """
    subjects = {}
    for target, title in _titles().items():
        hook = next(h for h in QuestGenerator.QUEST_HOOKS if title.startswith(h))
        subjects.setdefault(title[len(hook):].strip(), []).append(target)

    collisions = {s: t for s, t in subjects.items() if len(t) > 1}

    assert collisions == {}, f"targets sharing a title: {collisions}"


def test_a_title_still_leads_with_the_hook():
    gen = QuestGenerator()
    random.seed(19)

    for _ in range(20):
        quest = gen.generate()
        assert any(
            quest.title.startswith(hook) for hook in QuestGenerator.QUEST_HOOKS
        ), quest.title
