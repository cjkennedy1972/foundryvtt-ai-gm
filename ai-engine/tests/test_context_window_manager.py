from context.window_manager import ContextWindowManager


def test_set_system_prompt_replaces_previous_token_count():
    manager = ContextWindowManager(max_tokens=100, keep_system=True, keep_recent=5)

    manager.set_system_prompt("alpha beta gamma delta")
    first_total = manager.total_tokens
    assert first_total == len("alpha beta gamma delta") // manager.CHAR_TO_TOKEN

    manager.set_system_prompt("one two")
    second_total = manager.total_tokens
    assert second_total == len("one two") // manager.CHAR_TO_TOKEN
    assert second_total < first_total


def test_set_system_prompt_does_not_accumulate_on_replacement():
    manager = ContextWindowManager(max_tokens=100, keep_system=True, keep_recent=5)

    manager.set_system_prompt("a" * 40)
    first_total = manager.total_tokens
    manager.set_system_prompt("b" * 20)
    assert manager.total_tokens == len("b" * 20) // manager.CHAR_TO_TOKEN
    assert manager.total_tokens != first_total + len("b" * 20) // manager.CHAR_TO_TOKEN
