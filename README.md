# pytest-prompts

> **pytest for LLM prompts.** Write tests, detect regressions, run in CI.

```python
from pytest_prompts import prompt_test

@prompt_test()
def test_summary_is_concise(runner):
    result = runner.run(
        prompt="prompts/summarizer.txt",
        input="The Eiffel Tower is a wrought-iron lattice tower in Paris...",
    )
    verdict = runner.judge(result, "The summary is under 50 words and factually correct")
    assert verdict.verdict, verdict.reasoning
```

```bash
$ pytest-prompts run
                                pytest-prompts results
 Test                                           Model             Tokens  Latency  Status
 tests/test_summarizer.py::test_summary...      claude-sonnet-4-6    174    1.2s    PASS
 tests/test_summarizer.py::test_qa_knows...     claude-sonnet-4-6     48    0.9s    PASS

2 passed, 0 failed — 222 tokens total — $0.0009
```

Change a prompt → `pytest-prompts diff main` → see exactly what regressed.

---

## Install

```bash
uv add pytest-prompts
export ANTHROPIC_API_KEY=sk-ant-...
```

## Quickstart

```bash
# 1. Write a test
cat > tests/test_summarizer.py << 'EOF'
from pytest_prompts import prompt_test

@prompt_test()
def test_summary_is_concise(runner):
    result = runner.run(
        prompt="prompts/summarizer.txt",
        input="Long article here...",
    )
    verdict = runner.judge(result, "The summary is under 50 words")
    assert verdict.verdict, verdict.reasoning
EOF

# 2. Run
pytest-prompts run

# 3. Change your prompt, then detect regressions
pytest-prompts diff main
```

## Write a test

Any `pytest` file. Decorate with `@prompt_test()`, declare a `runner` fixture, assert on the result.

```python
from pytest_prompts import prompt_test

@prompt_test(model="claude-sonnet-4-6")
def test_json_extraction(runner):
    result = runner.run(
        prompt="prompts/extractor.txt",
        input='{"name": "Alice", "age": 30}',
    )
    import json
    data = json.loads(result.output)
    assert data["name"] == "Alice"
    assert result.tokens_used < 500
```

`result` exposes `output`, `input_tokens`, `output_tokens`, `tokens_used`, `latency_ms`, `model`, `cost_usd`.

## LLM-as-judge

String matching is fragile — use `runner.judge()` to evaluate outputs semantically:

```python
@prompt_test()
def test_qa_knows_capital_of_france(runner):
    result = runner.run(prompt="prompts/qa.txt", input="What is the capital of France?")
    verdict = runner.judge(result, "The answer correctly identifies Paris as the capital of France")
    assert verdict.verdict, verdict.reasoning
```

`verdict` exposes `verdict` (bool), `reasoning` (one sentence), `criterion`, `input_tokens`, `output_tokens`, `cost_usd`. Judge calls are recorded in snapshots alongside the run result.

## Detect regressions

```bash
# One command — runs tests on main, then on HEAD, compares automatically
pytest-prompts diff main

# Scope to a specific test directory
pytest-prompts diff main --path tests/prompts/
```

Output:

```
pytest-prompts diff
 Test                             main           HEAD           Status
 test_summary_is_concise          ✓ 342t 1.2s    ✓ 891t 3.1s    REGRESSION
 test_qa_knows_capital_of_france  ✓ 48t 0.9s     ✓ 48t 0.8s     ok

❌ REGRESSION  test_summary_is_concise
   tokens 342 → 891 (+160%)
```

Exit code `1` on any regression. Wire it into CI and you're done.

## CI (GitHub Actions)

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0  # required for base-ref diff mode
- uses: chahine-tech/pytest-prompts@v0.1
  with:
    path: tests/prompts
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    base-ref: main
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

On pull requests, the action runs your tests against both `main` and your branch,
compares them, fails the job on regressions, and posts a summary comment on the PR.

| Input                | Default | Description                                                               |
| -------------------- | ------- | ------------------------------------------------------------------------- |
| `path`               | `.`     | Test path (file or directory)                                             |
| `anthropic-api-key`  | —       | **Required.** Anthropic API key                                           |
| `python-version`     | `3.13`  | Python version                                                            |
| `base-ref`           |         | Base git ref (e.g. `main`). Tests run on base + head and are compared.    |
| `threshold`          | `0.05`  | Regression threshold as a fraction (5% by default)                        |
| `github-token`       |         | When set on `pull_request` events, posts the diff as a PR comment         |
| `fail-on-regression` | `true`  | Fail the job if a regression is detected                                  |

Outputs: `passed`, `failed`, `total-tokens`, `total-cost-usd`, `regressions`.

## What's included

- `@prompt_test` decorator with `pytest` integration
- `Runner` for the Anthropic API (Claude Sonnet 4.6 default)
- `runner.judge()` — LLM-as-judge for semantic assertions
- `pytest-prompts run` — run tests, summarize tokens/latency/cost
- `pytest-prompts diff <ref>` — run on a git ref + HEAD, detect regressions automatically

**Not here yet:** OpenAI/Gemini adapters, static prompt analysis, HTML reports. Open an issue — priorities come from usage, not a roadmap.

## License

MIT.
