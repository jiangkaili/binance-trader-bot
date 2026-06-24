# Security Policy

This project touches exchange API keys and live trading permissions. Treat security issues as urgent.

## Never commit secrets

Do not commit:

- `.env`
- `.env.production`
- `.env.testnet`
- real Binance API keys
- proxy credentials
- account snapshots with sensitive identifiers
- private logs that include credentials or signed URLs

The repository ignores `.env*` by default except `.env.example`.

## API key recommendations

If you experiment with this bot:

1. Create a new exchange API key only for this project.
2. Enable only the permissions you need.
3. Use IP whitelist if your exchange supports it.
4. Do not enable withdrawal permission.
5. Rotate the key immediately if it was pasted into chat, logs, screenshots, issues, or commits.
6. Start with dry-run or testnet before live mode.

## Reporting vulnerabilities

If you find a vulnerability, please open a GitHub issue only if it does not expose live credentials or private account details.

If the issue contains sensitive information, contact the maintainer privately first and avoid posting secrets in public issues.

## High-risk areas

Security-sensitive code paths include:

- exchange request signing
- stop-loss / take-profit placement
- order cancellation
- reduce-only close logic
- state reconciliation after restart
- config loading and environment variable handling

Any change to the exchange IO layer should update `tests/test_exchange_contract.py` in the same commit.
