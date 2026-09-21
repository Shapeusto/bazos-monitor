"""Tests for text helpers (no network)."""

from text import highlight, normalize_text, parse_terms


def test_normalize_text_strips_diacritics_and_lowercases():
    assert normalize_text("Kosačka Šípka ľ") == "kosacka sipka l"


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  Ahoj\n  svet  ") == "ahoj svet"


def test_parse_terms_splits_commas_and_keeps_quoted_phrases():
    assert parse_terms('i5, "acer nitro", ssd') == ["i5", "acer nitro", "ssd"]
    assert parse_terms("") == []


def test_highlight_marks_matches_diacritics_insensitively():
    assert "<mark>M1</mark>" in str(highlight("MacBook Pro M1", ["m1"]))
    assert "<mark>Kosačka</mark>" in str(highlight("Kosačka Šípka", ["kosacka"]))


def test_highlight_escapes_html():
    out = str(highlight("<script>alert(1)</script>", []))
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_highlight_escapes_but_keeps_mark():
    out = str(highlight("<b>acer</b>", ["acer"]))
    assert "&lt;b&gt;" in out
    assert "<mark>acer</mark>" in out
    assert "<b>" not in out
