"""Tests for the PSČ -> kraj lookup and location classification (no network)."""

from geo import (
    all_kraje,
    classify_location,
    kraj_confidence,
    kraj_for_city,
    kraj_for_psc,
    normalize_city,
)


def test_exact_match():
    assert kraj_for_psc("01001") == "Žilinský kraj"
    assert kraj_confidence("01001") == "exact"


def test_prefix_match():
    # 040 12 is not an exact row in the source, so the 040 prefix is used.
    assert kraj_for_psc("04012") == "Košický kraj"
    assert kraj_confidence("04012") == "prefix"


def test_spaces_are_ignored():
    assert kraj_for_psc("951 88") == "Nitriansky kraj"


def test_unknown_returns_none():
    assert kraj_for_psc("11000") is None  # Czech PSČ, not in the Slovak dataset
    assert kraj_for_psc("") is None
    assert kraj_for_psc(None) is None


def test_all_kraje_has_eight_regions():
    assert len(all_kraje()) == 8


def test_bratislava_psc_81101():
    # The primary dataset omits the Bratislava city PSČ; the GeoNames
    # override must fill it in.
    assert kraj_for_psc("81101") == "Bratislavský kraj"
    assert kraj_confidence("81101") == "prefix"


def test_bratislava_psc_across_prefixes():
    for psc in ["81102", "81109", "81201", "81510", "82001", "82108",
                "83101", "84105", "85000", "85101", "85200", "85405"]:
        assert kraj_for_psc(psc) == "Bratislavský kraj", psc


def test_bratislava_whole_city_code():
    assert kraj_for_psc("80000") == "Bratislavský kraj"
    assert kraj_confidence("80000") == "exact"


def test_neighbouring_region_near_border_is_not_greedy():
    # 931 01 Šamorín (Trnavský kraj) lies just across the Danube from
    # Bratislava; the 8xx prefix rule must not swallow it.
    assert kraj_for_psc("93101") == "Trnavský kraj"
    # 906/908/925 span several regions and must not become Bratislavský.
    assert kraj_for_psc("90601") == "Trnavský kraj"
    assert kraj_for_psc("92501") == "Trnavský kraj"


# ---------------------------------------------------------------------------
# classify_location
# ---------------------------------------------------------------------------

def test_classify_valid():
    assert classify_location("01001", "") == ("Žilinský kraj", "valid", "psc")
    assert classify_location("81101", "Bratislava") == ("Bratislavský kraj", "valid", "psc")


def test_classify_placeholder():
    # 12345 is now explained by the foreign city "Zahraničie"; the remaining
    # configured placeholders stay placeholders.
    assert classify_location("12345", "") == (None, "foreign", None)
    assert classify_location("00000", "") == (None, "placeholder", None)
    assert classify_location("99999", "") == (None, "placeholder", None)


def test_classify_placeholder_list_is_configurable():
    custom = frozenset({"77777"})
    assert classify_location("77777", "", placeholders=custom) == (None, "placeholder", None)
    # not in the custom list and a foreign pattern -> foreign
    assert classify_location("77777", "")[1] == "foreign"


def test_classify_foreign():
    assert classify_location("11000", "Česká republika") == (None, "foreign", None)
    assert classify_location("60200", "")[1] == "foreign"  # Czech Brno


def test_foreign_city_relabels_psc():
    # Task 0: "Zahraničie" / "Česká republika" are foreign, not invalid PSČ.
    assert classify_location("12345", "Zahraničie") == (None, "foreign", None)
    assert classify_location("12345", "Zahraničie ") == (None, "foreign", None)
    assert classify_location("11000", "Česká republika") == (None, "foreign", None)
    # A foreign/placeholder PSČ still uses the (Slovak) city fallback.
    assert classify_location("12345", "Košice") == ("Košický kraj", "foreign", "city")
    # A normal Slovak city with a placeholder PSČ is still a placeholder.
    assert classify_location("00000", "Košice") == ("Košický kraj", "placeholder", "city")


def test_classify_unmatched():
    assert classify_location("99998", "") == (None, "unmatched", None)


def test_classify_missing():
    assert classify_location("", "") == (None, "missing", None)
    assert classify_location(None, None) == (None, "missing", None)
    assert classify_location("   ", "Košice") == ("Košický kraj", "missing", "city")


def test_city_fallback_unambiguous():
    assert classify_location("99998", "Košice") == ("Košický kraj", "unmatched", "city")
    assert classify_location("00000", "Košice") == ("Košický kraj", "placeholder", "city")


def test_city_fallback_ambiguous_stays_unknown():
    # "Bohunice" occurs in several kraje in the source dataset.
    assert kraj_for_city("Bohunice") is None
    assert classify_location("99998", "Bohunice") == (None, "unmatched", None)


def test_city_abbreviation_expanded():
    # The DB contains "Nové Mesto n.Váhom"; the dataset spells it out.
    assert normalize_city("Nové Mesto n.Váhom") == "nove mesto nad vahom"
    assert kraj_for_city("Nové Mesto n.Váhom") == "Trenčiansky kraj"
    assert classify_location("99998", "Nové Mesto n.Váhom") == (
        "Trenčiansky kraj", "unmatched", "city",
    )
    assert kraj_for_city("Nové Mesto n. Váhom") == "Trenčiansky kraj"


