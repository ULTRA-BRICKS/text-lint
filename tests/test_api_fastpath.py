"""API normalization keeps canonical units when the source needs no changes."""

import pytest

from text_lint import inputs
from text_lint.text import LineIndex


@pytest.mark.parametrize(
    "raw", ["", "plain ASCII\ntext", "café Кириллица 日 😀\nend", "a\ufeff😀"]
)
@pytest.mark.parametrize("fmt", ["plain", "markdown", "html"])
def test_unchanged_api_unit_is_the_canonical_unit(raw, fmt):
    canonical = inputs.unit_from_text(raw, fmt)
    actual = inputs.unit_from_api_text(raw, fmt)
    assert actual == canonical  # No replacement unit or composed identity map.
    assert actual.raw is raw
    assert actual.semantic_text is None and actual.semantic_to_raw is None
    assert actual.semantic is raw
    if fmt != "html":
        assert actual.text is raw
        assert actual.norm_to_raw is None
    for start in range(len(actual.text) + 1):
        assert actual.position(start, space="text") == canonical.position(
            start, space="text"
        )
        for end in range(start, len(actual.text) + 1):
            assert actual.raw_span(start, end) == canonical.raw_span(start, end)


@pytest.mark.parametrize("fmt", ["plain", "markdown"])
def test_unchanged_api_unit_allocates_no_per_character_state(fmt):
    import tracemalloc

    raw = (
        "plain ASCII text\n" * 100_000
    )  # 1.7 MB; an offset list alone would hold 13 MB.
    tracemalloc.start()
    try:
        unit = inputs.unit_from_api_text(raw, fmt)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert unit.text is raw
    assert peak < 64 * 1024


@pytest.mark.parametrize(
    "raw, normalized, offsets, spans",
    [
        ("\ufeff😀x", "😀x", [1, 2], [(1, 2), (2, 3)]),
        ("\ufeff\ufeff😀", "😀", [2], [(2, 3)]),
        ("\ufeff", "", [], []),
        ("😀\rx", "😀\nx", [0, 1, 2], [(0, 1), (1, 2), (2, 3)]),
        ("😀\r\nx", "😀\nx", [0, 1, 3], [(0, 1), (1, 3), (3, 4)]),
        ("\ufeff😀\r\nx", "😀\nx", [1, 2, 4], [(1, 2), (2, 4), (4, 5)]),
    ],
)
@pytest.mark.parametrize("fmt", ["plain", "markdown", "html"])
def test_changed_source_keeps_offsets(raw, normalized, offsets, spans, fmt):
    unit = inputs.unit_from_api_text(raw, fmt)
    assert unit.raw is raw
    assert unit.text == normalized
    assert unit.norm_to_raw == offsets
    index = LineIndex(raw)
    for i, span in enumerate(spans):
        assert unit.raw_span(i, i + 1) == span
        assert unit.position(i, space="text") == index.line_col(span[0])
    assert unit.position(len(normalized), space="text") == index.line_col(len(raw))


def test_unchanged_html_preserves_entity_spans():
    raw = "<b>😀&amp;x</b>"
    unit = inputs.unit_from_api_text(raw, "html")
    canonical = inputs.unit_from_text(raw, "html")
    assert unit == canonical
    assert unit.text == "😀&x"
    assert unit.raw_span(1, 2) == (4, 9)
    assert unit.position(0, space="text") == (1, 4)


@pytest.mark.parametrize(
    "raw", ["", "utilize ASCII", "😀 ка日eat utilize\nend", "a\ufeff😀 utilize"]
)
def test_unchanged_api_report_matches_canonical(raw, fake_engines):
    from text_lint import api
    from text_lint.check import check_units
    from text_lint.inputs import Meta
    from text_lint.profiles import load_profile

    profile = load_profile("tg-bot")
    canonical = check_units(
        [inputs.unit_from_text(raw, profile.markup)], Meta(), profile, enforce="block"
    )
    assert api.check(raw, profile="tg-bot") == canonical
