"""Filters: regular expressions on the RE2 engine, searched anywhere, not case-sensitive unless ticked."""

from __future__ import annotations

import time

import pytest

from clara.automations import matching
from clara.automations.events import KINDS

MAIL = KINDS["mail.received"]


def check(raw):
    return matching.check(MAIL, raw)


def test_a_pattern_is_searched_anywhere_without_regard_to_case():
    filter_ = check({"subject": "invoice"})
    assert filter_ == {"subject": {"pattern": "invoice", "not": False, "case_sensitive": False}}
    assert matching.matches(filter_, {"subject": "Your INVOICE for May"})
    assert not matching.matches(filter_, {"subject": "Hello"})
    assert not matching.matches(filter_, {})  # a missing field is the empty text


def test_the_case_tick_and_the_must_not_match_tick():
    sensitive = check({"subject": {"pattern": "URGENT", "case_sensitive": True}})
    assert matching.matches(sensitive, {"subject": "URGENT: call"})
    assert not matching.matches(sensitive, {"subject": "urgent: call"})
    forbidden = check({"from": {"pattern": "newsletter", "not": True}})
    assert matching.matches(forbidden, {"from": "Ann <ann@example.org>"})
    assert not matching.matches(forbidden, {"from": "NEWSLETTER <n@example.org>"})
    assert matching.matches(forbidden, {})  # nothing to find in an empty text, so it must not match: it does not


def test_anchors_alternation_and_a_list_of_conditions_on_one_field():
    filter_ = check({"subject": [{"pattern": "^(invoice|receipt)\\b"}, {"pattern": "draft$", "not": True}]})
    assert matching.matches(filter_, {"subject": "Receipt 12"})
    assert not matching.matches(filter_, {"subject": "Your receipt"})  # ^ anchors the start
    assert not matching.matches(filter_, {"subject": "Receipt draft"})
    assert [j.ok for j in matching.judge(filter_, {"subject": "Receipt draft"})] == [True, False]


def test_a_bool_field_and_all_fields_must_hold():
    filter_ = check({"unread": True, "has_attachment": {"eq": False}, "from": "@billing"})
    assert filter_["unread"] == {"eq": True}
    assert matching.matches(filter_, {"unread": 1, "has_attachment": 0, "from": "a@billing.example"})
    assert not matching.matches(filter_, {"unread": 1, "has_attachment": 1, "from": "a@billing.example"})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"subject": "(?=a)"}, "no lookahead"),
        ({"subject": r"(a)\1"}, "no back-reference"),
        ({"subject": "("}, "missing"),
        ({"subject": {"pattern": "x", "ignore": 1}}, "not part of a condition"),
        ({"subject": {"pattern": ""}}, "not empty"),
        ({"subject": {"pattern": "x", "not": "yes"}}, "true or false"),
        ({"subject": {"contains": "x"}}, "pattern"),  # the old operators are gone
        ({"nope": "x"}, "not a field"),
        ({"unread": "yes"}, "true or false"),
        ({"subject": "x" * 501}, "at most 500"),
        ({"subject": []}, "one condition"),
    ],
)
def test_a_filter_the_engine_or_the_rules_refuse_is_refused_with_the_reason(raw, message):
    with pytest.raises(matching.FilterError, match=message):
        check(raw)


def test_a_pattern_that_backtracks_in_python_is_linear_here():
    # `(a+)+$` on a long run of a's ending in something else takes longer than the age of the universe in `re`
    filter_ = check({"subject": "(a+)+$", "snippet": "^(a|aa)+$"})
    hostile = "a" * 100_000 + "!"
    started = time.perf_counter()
    assert not matching.matches(filter_, {"subject": hostile, "snippet": hostile})
    assert time.perf_counter() - started < 2.0


def test_the_filter_in_words():
    filter_ = check({"subject": "invoice", "from": {"pattern": "news", "not": True, "case_sensitive": True}})
    assert matching.words(filter_) == "subject matches “invoice”, from must not match “news” (case matters)"
