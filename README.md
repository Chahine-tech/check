# PromptCI

> **pytest for LLM prompts.** Write tests, detect regressions, run in CI.

```python
from promptci import prompt_test

@prompt_test()
def test_qa_knows_capital_of_france(runner):
    result = runner.run(
        prompt="prompts/qa.txt",
        input="What is the capital of France?",
    )
    assert "Paris" in result.output
    assert result.latency_ms < 5000
```

```bash
$ promptci run
                                PromptCI results
 Test                                           Model             Tokens  Latency  Status
 examples/test_prompts.py::test_summary...      claude-sonnet-4-6    174    1.2s    PASS
 examples/test_prompts.py::test_qa_knows...     claude-sonnet-4-6     48    0.9s    PASS
 examples/test_prompts.py::test_qa_admits...    claude-sonnet-4-6     52    1.1s    PASS

3 passed, 0 failed — 274 tokens total — $0.0012
```

Change a prompt → rerun → `promptci diff` shows what regressed.

---

## Install

```bash
uv add promptci
export ANTHROPIC_API_KEY=sk-ant-...
```

## Write a test

Any `pytest` file. Decorate with `@prompt_test()`, declare a `runner` fixture, assert on the result.

```python
# tests/test_summarizer.py
from promptci import prompt_test

@prompt_test(model="claude-sonnet-4-6")
def test_summary_is_concise(runner):
    result = runner.run(
        prompt="prompts/summarizer.txt",
        input="Long text here...",
    )
    assert len(result.output.split()) < 100
    assert result.tokens_used < 500
```

`result` exposes `output`, `input_tokens`, `output_tokens`, `tokens_used`, `latency_ms`, `model`, `cost_usd`.

## Detect regressions

Every run writes snapshots to `.promptci/snapshots/`.

```bash
# Capture baseline on main
git checkout main
promptci run --snapshot-dir .promptci/base

# Run on your branch
git checkout feature/new-prompt
promptci run --snapshot-dir .promptci/head

# Compare
promptci diff .promptci/base .promptci/head
```

Output:

```
PromptCI diff
 Test                                  Base          Head          Status
 test_summary_is_concise               ✓ 342t 1.2s   ✓ 891t 3.1s   REGRESSION
 test_qa_knows_capital_of_france       ✓ 48t 0.9s    ✓ 48t 0.8s    ok

Regressions:
  • test_summary_is_concise — tokens 342 → 891 (+160%)
```

Exit code `1` on any regression. Wire it into CI and you're done.

## CI (GitHub Actions)

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0  # required for base-ref diff mode
- uses: chahine-tech/promptci@v0.1
  with:
    path: tests/prompts
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    base-ref: main
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

On pull requests, the action runs your tests against both `main` and your branch,
compares them, fails the job on regressions, and posts a summary comment on the PR.

| Input                | Default | Description                                                                      |
| -------------------- | ------- | -------------------------------------------------------------------------------- |
| `path`               | `.`     | Test path (file or directory)                                                    |
| `anthropic-api-key`  | —       | **Required.** Anthropic API key                                                  |
| `python-version`     | `3.13`  | Python version                                                                   |
| `base-ref`           |         | Base git ref (e.g. `main`). On PRs, results are compared against this ref        |
| `threshold`          | `0.05`  | Regression threshold as a fraction (5% by default)                               |
| `github-token`       |         | When set on `pull_request` events, posts the results as a PR comment             |
| `fail-on-regression` | `true`  | Fail the job if a regression is detected                                         |

Outputs: `passed`, `failed`, `total-tokens`, `total-cost-usd`, `regressions`.

## What's in the POC

- `@prompt_test` decorator with `pytest` integration
- `Runner` for the Anthropic API (Claude Sonnet 4.6 default)
- `promptci run` — run tests, summarize tokens/latency/cost
- `promptci diff` — compare two snapshot dirs, flag regressions

**Not here yet:** OpenAI/Gemini adapters, static prompt analysis, LLM-as-judge, HTML reports. If you want them, open an issue — priorities come from usage, not from a roadmap.

## License

MIT.
