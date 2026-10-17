"""Real engines: vendored ai-tells + TextLint through a real `vale`, and a real `aiw-ru`.

Skipped when an engine is not installed; CI sets SLOP_CHECK_REQUIRE_ENGINES=1 and installs
both, so there a missing engine fails the run instead of skipping it.
"""

import io
import json
import os
import shutil
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from text_lint.cli import main
from text_lint.engines import find_aiwru, find_vale

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TEXTLINT_STYLE = ROOT / "text_lint" / "packs" / "vale" / "styles" / "TextLint"
# Package data, so an installed copy ships its rule samples too.
SAMPLES = files("text_lint") / "packs" / "vale" / "samples.tsv"

_missing = [name for name, path in (("vale", find_vale()), ("aiw-ru", find_aiwru())) if path is None]
if _missing and os.environ.get("SLOP_CHECK_REQUIRE_ENGINES") == "1":
    raise RuntimeError(f"SLOP_CHECK_REQUIRE_ENGINES=1 but not installed: {', '.join(_missing)}")
pytestmark = pytest.mark.skipif(
    bool(_missing),
    reason=f"real engine(s) not installed: {', '.join(_missing)} (brew install vale; uv tool install aiw-ru)",
)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    # Engine discovery above ran with the caller's SLOP_CHECK_VALE / SLOP_CHECK_AIWRU, so the
    # tests keep them too: both phases must find the same binaries.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


def check(capsys, *argv, text=None, monkeypatch=None):
    if text is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
    code = main(["check", "--format", "json", *argv])
    return code, json.loads(capsys.readouterr().out)


def rules(data):
    return {f["rule_id"] for f in data["findings"]}


def test_english_slop_fixture(capsys):
    code, data = check(capsys, str(FIXTURES / "en_slop.md"))
    assert code == 1
    found = rules(data)
    for rule in (
        "TextLint.Sycophancy",           # Great question
        "ai-tells.HedgingPhrases",       # It's important to note that
        "ai-tells.OverusedVocabulary",   # robust
        "ai-tells.EmDashUsage",          # —
        "TextLint.StatusReport",         # Successfully implemented
        "TextLint.UnprovenTestClaim",    # All tests pass
        "ai-tells.ClosingPleasantries",  # I hope this helps / Let me know if you need anything else
    ):
        assert rule in found, rule
    assert data["score"] > 50
    assert data["engines"]["vale"]["status"] == "ok"


def test_russian_slop_fixture(capsys):
    code, data = check(capsys, str(FIXTURES / "ru_slop.md"))
    assert code == 1
    found = rules(data)
    assert "aiwru.filler.стоит-отметить" in found
    assert "aiwru.tier1.в-современном-мире" in found
    assert "aiwru.chatbot.надеюсь-поможет" in found
    assert "TextLint.RuVazhnoPonimat" in found  # «важно понимать, как»: aiw-ru misses it
    assert not [r for r in found if r.startswith("ai-tells.")]
    # No phrase is reported twice by the two engines.
    spans = [tuple(f["span"]) for f in data["findings"]]
    assert len(spans) == len(set(spans))


@pytest.mark.parametrize("name", ["clean_en.md", "clean_ru.md"])
def test_clean_technical_text_has_zero_findings(capsys, name):
    code, data = check(capsys, str(FIXTURES / name))
    assert data["findings"] == []
    assert code == 0


def test_code_and_urls_are_ignored(capsys):
    code, data = check(capsys, str(FIXTURES / "code_urls.md"))
    assert data["findings"] == []
    assert code == 0


def test_list_item_that_starts_with_inline_code_is_still_checked(capsys, monkeypatch):
    # Blanking `styles/TextLint/` to spaces would make this item an indented code block
    # that Vale skips; the engines get the real Markdown instead.
    text = '- `styles/TextLint/`: our pack covers praise like "Great catch" too.\n'
    _, data = check(capsys, text=text, monkeypatch=monkeypatch)
    assert "TextLint.Sycophancy" in rules(data)


def test_findings_never_touch_code_or_urls(capsys, monkeypatch):
    # Real Vale reports "This enables `robust." (Metacommentary) across the code span.
    text = "This enables `robust.`\n\nThis enables https://example.com/robust here.\n"
    _, data = check(capsys, text=text, monkeypatch=monkeypatch)
    for f in data["findings"]:
        assert "`" not in f["match"] and "https://" not in f["match"], f


@pytest.mark.parametrize("text, rule", [
    ("Обычный\u200b текст.\n", "aiwru.invisible-chars.zero-width"),
    ("Обыч\u00adный текст.\n", "aiwru.soft-hyphen.soft-hyphen"),
    ("В\u00a0современном мире всё иначе.\n", None),
])
def test_aiwru_described_issues_are_findings(capsys, monkeypatch, text, rule):
    code, data = check(capsys, text=text, monkeypatch=monkeypatch)
    assert code == 1
    if rule:
        assert rule in rules(data)


@pytest.mark.parametrize("text", [
    "- ```\n  code\n\nGreat question!\n",
    "> ```\n> code\n\nGreat question!\n",
    "> - ```\n>   code\n>\n> Great question!\n",
    "    ```\n\nGreat question!\n",
    "```foo`bar\nGreat question!\n",
])
def test_prose_after_an_unclosed_container_fence_is_checked(capsys, monkeypatch, text):
    _, data = check(capsys, text=text, monkeypatch=monkeypatch)
    assert "TextLint.Sycophancy" in rules(data)


def test_invisible_char_in_code_does_not_hide_one_in_prose(capsys, monkeypatch):
    text = "Обычный `x\u200b` текст.\n\nДругой\u200b текст.\n"
    _, data = check(capsys, text=text, monkeypatch=monkeypatch)
    hits = [f for f in data["findings"] if f["rule_id"] == "aiwru.invisible-chars.zero-width"]
    assert [f["line"] for f in hits] == [3]


def test_url_finding_dropped_also_drops_its_score(capsys, monkeypatch):
    _, data = check(capsys, text="Обычный текст. https://example.com/?utm_source=chatgpt.com\n",
                    monkeypatch=monkeypatch)
    assert data["findings"] == [] and data["score"] == 0


def test_leading_bom_keeps_russian_spans(capsys, monkeypatch):
    code, data = check(capsys, text="\ufeffВ современном мире всё иначе.\n", monkeypatch=monkeypatch)
    hit = [f for f in data["findings"] if f["rule_id"].startswith("aiwru.")]
    assert hit and hit[0]["line"] == 1 and hit[0]["col"] == 1
    assert code == 1


@pytest.mark.parametrize("text", [
    "Great **question**! `Great question`\n",
    "Great [question](https://example.com)! `Great question`\n",
    "Great\n**question**!\n\n`Great question`\n",
])
def test_rendered_match_is_not_moved_into_later_code(capsys, monkeypatch, text):
    # Vale matches the rendered "Great question"; the same words in code later on the line
    # must not capture the finding (it would then be dropped as protected).
    _, data = check(capsys, text=text, monkeypatch=monkeypatch)
    assert "TextLint.Sycophancy" in rules(data)


def test_english_tells_inside_a_russian_paragraph_are_kept(capsys, monkeypatch):
    text = "Это русское предложение с большим количеством кириллических букв. This robust guard helps.\n"
    _, data = check(capsys, text=text, monkeypatch=monkeypatch)
    assert "ai-tells.OverusedVocabulary" in rules(data)


def test_em_dash_rule_skips_cyrillic_lines(capsys, monkeypatch):
    text = "Очередь — это надёжнее.\n\nThe queue — it is safer.\n"
    _, data = check(capsys, text=text, monkeypatch=monkeypatch)
    assert [f["line"] for f in data["findings"] if f["rule_id"] == "ai-tells.EmDashUsage"] == [3]


def _samples():
    rows = []
    for line in SAMPLES.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            rule, text = line.split("\t", 1)
            rows.append((rule, text))
    return rows


def test_every_textlint_rule_has_a_sample():
    covered = {rule for rule, _ in _samples()}
    rule_files = {p.stem for p in TEXTLINT_STYLE.glob("*.yml")}
    assert rule_files, f"no TextLint rules under {TEXTLINT_STYLE}"
    assert rule_files <= covered


@pytest.mark.parametrize("rule, text", _samples())
def test_textlint_rule_flags_its_sample(capsys, monkeypatch, rule, text):
    _, data = check(capsys, text=text + "\n", monkeypatch=monkeypatch)
    assert f"TextLint.{rule}" in rules(data)


def test_pr_context_adds_commit_rules(capsys, monkeypatch):
    text = "This PR adds a retry loop to the uploader.\n"
    _, plain = check(capsys, text=text, monkeypatch=monkeypatch)
    _, pr = check(capsys, "--context", "pr", text=text, monkeypatch=monkeypatch)
    assert "ai-tells-commits.CommitSelfReference" not in rules(plain)
    assert "ai-tells-commits.CommitSelfReference" in rules(pr)
