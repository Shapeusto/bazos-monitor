"""Shared text helpers (normalisation, term parsing, safe highlighting)."""

from __future__ import annotations

import csv
import io
import unicodedata
from typing import Iterable

from markupsafe import Markup, escape


def normalize_text(text: str) -> str:
    """Lowercase, strip diacritics (NFKD) and collapse whitespace.

    Used for ``listings.search_text`` and for every keyword search so the
    two always match. Example: ``"Kosačka Šípka ľ"`` -> ``"kosacka sipka l"``.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.lower().split())


def parse_terms(text: str) -> list[str]:
    """Split a comma-separated filter input into terms.

    Quoted phrases (which may contain commas) are kept as a single term.
    """
    if not text:
        return []
    try:
        fields = next(csv.reader(io.StringIO(text)))
    except (csv.Error, StopIteration):
        fields = text.split(",")
    terms: list[str] = []
    for field in fields:
        field = field.strip()
        if len(field) >= 2 and field[0] == field[-1] and field[0] in "\"'":
            field = field[1:-1].strip()
        if field:
            terms.append(field)
    return terms


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Return (folded_text, origin_index_per_folded_char)."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        base = "".join(
            ch for ch in unicodedata.normalize("NFKD", char)
            if not unicodedata.combining(ch)
        ).lower()
        for folded in base:
            chars.append(folded)
            origin.append(index)
    return "".join(chars), origin


def highlight(text: str, terms: Iterable[str]) -> Markup:
    """Wrap matched terms in ``<mark>``, diacritics-insensitively and safely.

    The input text is escaped; only the ``<mark>`` markup we add is raw.
    """
    if not text:
        return Markup("")
    normalized_terms = [normalize_text(term) for term in terms if term]
    normalized_terms = [term for term in normalized_terms if term]
    if not normalized_terms:
        return escape(text)

    folded, origin = _fold_with_map(text)
    spans: list[tuple[int, int]] = []
    for term in normalized_terms:
        start = 0
        while True:
            found = folded.find(term, start)
            if found < 0:
                break
            spans.append((origin[found], origin[found + len(term) - 1] + 1))
            start = found + len(term)
    if not spans:
        return escape(text)

    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    parts: list[Markup] = []
    position = 0
    for start, end in merged:
        parts.append(escape(text[position:start]))
        parts.append(Markup("<mark>"))
        parts.append(escape(text[start:end]))
        parts.append(Markup("</mark>"))
        position = end
    parts.append(escape(text[position:]))
    return Markup("").join(parts)
