"""Tests for the interview answer store.

The store is where a follow-up exchange is kept intact. The two properties that
matter are that appending never discards what came before, and that a
string-valued answer from a checkpoint written before this shape existed is
widened rather than iterated — `list("abc")` would silently shred an answer into
single characters, which reads as a corrupt transcript rather than an error.
"""

import pytest

from agents import answers as answer_store


class TestCoerce:
    def test_empty_and_none(self):
        assert answer_store.coerce(None) == {}
        assert answer_store.coerce({}) == {}

    def test_list_values_pass_through(self):
        store = {"a.0": ["first", "second"]}
        assert answer_store.coerce(store) == {"a.0": ["first", "second"]}

    def test_string_value_is_wrapped_not_iterated(self):
        assert answer_store.coerce({"a.0": "hello"}) == {"a.0": ["hello"]}

    def test_mixed_shapes_in_one_store(self):
        coerced = answer_store.coerce({"a.0": "legacy", "a.1": ["current"]})
        assert coerced == {"a.0": ["legacy"], "a.1": ["current"]}

    def test_tuple_value_becomes_a_list(self):
        assert answer_store.coerce({"a.0": ("one", "two")}) == {"a.0": ["one", "two"]}

    def test_unexpected_value_is_stringified_rather_than_dropped(self):
        assert answer_store.coerce({"a.0": 42}) == {"a.0": ["42"]}

    def test_empty_string_answer_is_kept(self):
        """A refusal or a blank turn is data — the key must not vanish."""
        assert answer_store.coerce({"a.0": ""}) == {"a.0": [""]}


class TestAppend:
    def test_appends_to_an_existing_key(self):
        store = answer_store.append({"a.0": ["first"]}, "a.0", "second")
        assert store["a.0"] == ["first", "second"]

    def test_creates_a_missing_key(self):
        assert answer_store.append({}, "a.0", "first") == {"a.0": ["first"]}

    def test_appends_onto_a_legacy_string_value(self):
        store = answer_store.append({"a.0": "legacy"}, "a.0", "follow-up")
        assert store["a.0"] == ["legacy", "follow-up"]

    def test_does_not_mutate_the_input(self):
        """LangGraph state updates must not write through to the state they read."""
        original = {"a.0": ["first"]}
        answer_store.append(original, "a.0", "second")
        assert original == {"a.0": ["first"]}

    def test_leaves_other_keys_alone(self):
        store = answer_store.append({"a.0": ["x"], "b.1": ["y"]}, "a.0", "z")
        assert store["b.1"] == ["y"]


class TestReaders:
    def test_first_returns_the_original_answer(self):
        assert answer_store.first({"a.0": ["original", "follow-up"]}, "a.0") == "original"

    def test_first_of_a_missing_key_is_empty(self):
        assert answer_store.first({}, "a.0") == ""

    def test_joined_returns_the_whole_exchange(self):
        assert answer_store.joined({"a.0": ["one", "two"]}, "a.0") == "one two"

    def test_joined_accepts_a_separator(self):
        assert answer_store.joined({"a.0": ["one", "two"]}, "a.0", " | ") == "one | two"
