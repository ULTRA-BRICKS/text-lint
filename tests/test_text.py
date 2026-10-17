from text_lint.text import LineIndex, is_russian, mask, paragraphs, protected_spans


def _covered(text, spans, needle):
    start = text.index(needle)
    end = start + len(needle)
    return any(s <= start and end <= e for s, e in spans)


def test_fenced_block_inline_code_and_urls_are_protected():
    text = (
        "Intro line.\n\n"
        "```python\nGreat question = 1\n```\n\n"
        "Run `robust --flag` and see https://example.com/delve?x=1.\n"
        "Also <https://example.org/a> and www.example.net/path here.\n"
    )
    spans = protected_spans(text)
    assert _covered(text, spans, "```python\nGreat question = 1\n```")
    assert _covered(text, spans, "`robust --flag`")
    assert _covered(text, spans, "https://example.com/delve?x=1")
    assert _covered(text, spans, "https://example.org/a")
    assert _covered(text, spans, "www.example.net/path")
    assert not _covered(text, spans, "Intro line.")
    # The sentence-ending period after a URL is prose, not part of the URL.
    url_end = text.index("delve?x=1") + len("delve?x=1")
    assert not any(s <= url_end < e for s, e in spans)


def test_unterminated_fence_protects_to_the_end():
    text = "Text.\n\n~~~\ncode Great question\n"
    assert _covered(text, protected_spans(text), "code Great question")


def test_mask_keeps_length_and_newlines():
    text = "a `b c`\nd"
    masked = mask(text, protected_spans(text))
    assert len(masked) == len(text)
    assert masked == "a      \nd"


def test_paragraphs_split_on_blank_lines():
    text = "one\ntwo\n\n  \nthree\n"
    assert [text[s:e] for s, e in paragraphs(text)] == ["one\ntwo", "three"]


def test_is_russian_uses_cyrillic_ratio():
    assert is_russian("Стоит отметить, что API отвечает 200.")
    assert not is_russian("The API returns 200, see Стоит.")
    assert not is_russian("1234 !!!")


def test_line_index_round_trip_on_cyrillic():
    text = "первая\nвторая строка\n"
    index = LineIndex(text)
    off = text.index("строка")
    assert index.line_col(off) == (2, 8)
    assert index.offset(2, 8) == off


def test_inline_code_may_wrap_a_line_but_not_a_blank_line():
    text = "Text `code\nwraps` here.\n\n`open\n\nclosed` no.\n"
    spans = protected_spans(text)
    assert (5, 17) in spans
    assert not any(s <= text.index("open") < e for s, e in spans)


def test_fence_inside_a_list_item_is_protected():
    text = "- ```\n  код\n\n  В современном мире.\n  ```\n"
    assert protected_spans(text) == [(0, len(text) - 1)]


def test_line_index_rejects_a_column_past_its_line():
    import pytest

    index = LineIndex("x\n Great question\n")
    with pytest.raises(IndexError):
        index.offset(1, 4)


def test_url_keeps_balanced_parentheses():
    text = "See https://en.wikipedia.org/wiki/Foo_(bar) now."
    assert protected_spans(text) == [(4, text.index(" now"))]
