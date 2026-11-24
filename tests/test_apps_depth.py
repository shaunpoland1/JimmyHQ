"""Nesting is measured by our own rule, so it is the same on every Python version."""

from jimmyhq.apps.depth import MAX_DEPTH, too_deep


def test_nesting_up_to_the_limit_is_fine_and_one_more_is_too_deep():
    assert not too_deep("[" * MAX_DEPTH + "]" * MAX_DEPTH)
    assert too_deep("[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1))
    assert too_deep('{"a":' * (MAX_DEPTH + 1) + "1" + "}" * (MAX_DEPTH + 1))


def test_brackets_inside_strings_do_not_count():
    assert not too_deep('{"text": "' + "[" * 500 + '"}')
    assert not too_deep('{"text": "a \\" quote then ' + "{" * 500 + '"}')


def test_unbalanced_text_is_still_measured():
    assert too_deep("[" * 100_000)
    assert not too_deep("]" * 100_000)
