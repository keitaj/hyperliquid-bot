# Contributing

Thank you for your interest in contributing to Hyperliquid Trading Bot!

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/<your-username>/hyperliquid-bot.git
   cd hyperliquid-bot
   ```
3. Install dependencies:
   ```bash
   pip install ".[dev]"
   ```
4. Create a branch for your change:
   ```bash
   git checkout -b feat/your-feature
   ```

## Development

### Running Tests

```bash
python -m pytest tests/ -v
```

### Linting

```bash
flake8 . --max-line-length=120 --exclude=.git,__pycache__,.env
```

### Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/) conventions
- Use f-strings for log formatting (not `%` or `.format()`)
- Add type hints to function signatures
- Add docstrings to classes and public methods
- Max line length: 120 characters

## Pull Requests

1. Keep PRs focused — one feature or fix per PR
2. Include a clear description of what changed and why
3. Ensure CI passes (lint + tests)
4. Update `README.md` if you add new CLI flags or strategies

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: Add new strategy for XYZ
fix: Correct position sizing when account is empty
docs: Update README with new parameters
refactor: Extract common position sizing logic
```

## Adding a New Strategy

1. Create `strategies/your_strategy.py` extending `BaseStrategy`
2. Implement `generate_signals()` and `calculate_position_size()`
3. Read all parameters from `config.get('key', default)` — no hardcoded values
4. Register in `strategies/__init__.py` and `bot.py` (strategy_map + default_configs + argparse)
5. Add tests in `tests/`
6. Document in `README.md` (human section + AI YAML reference)

## Reporting Issues

- Use the [Bug Report](.github/ISSUE_TEMPLATE/bug_report.md) template for bugs
- Use the [Feature Request](.github/ISSUE_TEMPLATE/feature_request.md) template for ideas
- Include your Python version, OS, and bot version (`git log --oneline -1`)

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
