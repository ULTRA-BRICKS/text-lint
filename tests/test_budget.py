"""Warm budgets with fake external engines and a local fake Laya socket."""

from time import perf_counter

import pytest

from text_lint import check
from text_lint.engines import native
from text_lint.inputs import Meta, unit_from_text
from text_lint.profiles import load_profile


@pytest.mark.parametrize("profile", ["tg-bot", "as-user"])
def test_warm_send_budget(profile, fake_engines, laya_server):
    laya_server.answers["first_person"] = .9
    doc = {"schema": "text-lint/input@1", "fields": [
        {"name": name, "text": "Проверка", "format": "plain", "gate": name != "harness_title"}
        for name in ("title", "subagent", "harness_title")
    ] + [{"name": "body", "text": ("The cache holds данные. " * 200)[:4096], "format": "plain"}]}
    check(doc, profile=profile)
    times = []
    for _ in range(5):
        started = perf_counter()
        check(doc, profile=profile)
        times.append(perf_counter() - started)
    # Nearest-rank p95 of five observations is their maximum.
    print(f"{profile}: warm p95={max(times):.4f}s, n=5, fake external engines")
    assert max(times) <= 1.5
    assert len(laya_server.requests) == 6


def test_spoken_native_budget():
    profile = load_profile("spoken")
    units = [unit_from_text(("I can read this sentence. " * 50)[:1000], "plain")]
    times = []
    for _ in range(5):
        started = perf_counter()
        rules, _ = native.rules_for(profile)
        native.run(units, Meta(), rules, laya={"first_person": .9})
        times.append(perf_counter() - started)
    print(f"spoken native: p95={max(times):.4f}s, n=5")
    assert max(times) <= .250
