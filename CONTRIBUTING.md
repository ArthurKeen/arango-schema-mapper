# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Running tests

```bash
pytest -q
```

## Guidelines
- Keep outputs deterministic (ordering, stable JSON).
- Do not log or persist secrets (API keys, credentials).
- Add/adjust tests for behavior changes (golden fixtures where appropriate).

