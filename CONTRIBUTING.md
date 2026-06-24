# Contributing

Thanks for considering a contribution.

This project is an engineering experiment around automation reliability, risk control, and transparent postmortems. Contributions should make the system safer, clearer, or easier to audit.

## Good contribution areas

- risk-control improvements
- exchange API contract tests
- report-generation automation
- clearer strategy archive entries
- better dry-run / testnet behavior
- safer configuration validation
- documentation that prevents credential leaks or unsafe live use
- honest backtests, including losing results

## Contributions that are not a fit

Please do not open issues or PRs for:

- guaranteed-profit settings
- copy-trading signals
- "best leverage" advice
- marketing claims about stable yield
- hiding losses from reports
- bypassing risk controls

## Development checks

Before opening a PR, run:

```bash
pytest tests/test_exchange_contract.py -q
pytest tests/test_trader_v2.py -q
pytest tests/ -q
```

If your change touches `trader/exchange.py`, update `tests/test_exchange_contract.py` in the same commit.

## Documentation standard

When changing strategy behavior, update `STRATEGY_ARCHIVE.md` before or alongside the code change.

When changing live execution behavior, document:

- what failure mode it addresses
- how it was verified
- whether it changes risk exposure
- whether it touches the exchange IO boundary
