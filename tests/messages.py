"""Message bodies shared by the message-rule tests, taken from tg-cli's own tests."""

import json

# tests/escalation-format.test.ts COMPLIANT: every section of the decision format.
COMPLIANT = "<h3>Context</h3><p>The resolver in features/foo.ts:42 picks the wrong click target on cold maps.</p><hr>\n<h3>Options</h3><table><tr><th>Option</th><th>Pros</th><th>Cons</th></tr>\n<tr><td>A</td><td>fast</td><td>risky</td></tr><tr><td>B</td><td>safe</td><td>slow</td></tr></table><hr>\n<h3>Recommendation</h3><ul><li>I recommend A because it is faster</li></ul><hr>\n<h4>Where to look</h4><ul><li>features/foo.ts:42</li></ul>\n<hr><h4>Glossary</h4><ul><li>resolver — picks the element a click lands on</li></ul>"

# tests/intent-gate.test.ts PROBLEM_OK: a problem in its format.
PROBLEM_OK = "<h3>Контекст</h3><p>Деплой tg-cli на прод застрял: CI падает на шаге setup-bun из-за rate limit GitHub API.</p><hr>\n<h3>Что заблокировано</h3><ul><li>мерж #692 и всех PR после него</li></ul><hr>\n<h3>Что нужно от тебя</h3><ul><li>нужен токен GitHub с правом read:packages для раннера</li></ul><hr>\n<h3>Где смотреть</h3><ul><li>.github/workflows/ci.yml:20</li></ul>"

# tests/message-structure.test.ts denseLongText: 500+ characters of plain text, no structure.
DENSE = " ".join(f"detail{i}" for i in range(90))

# A report in its format (tests/intent-gate.test.ts): a reference and its verification.
REPORT_OK = "Готово: #690 смержен, CI зелёный, деплой прошёл."


def input_doc(fields, **meta):
    """An input@1 document; ``fields`` are (name, text, format) or (name, text, format, gate)."""
    out = []
    for name, text, fmt, *gate in fields:
        field = {"name": name, "text": text, "format": fmt}
        if gate:
            field["gate"] = gate[0]
        out.append(field)
    return json.dumps({"schema": "text-lint/input@1", "fields": out, "meta": meta}, ensure_ascii=False)
