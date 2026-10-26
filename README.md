# Binance USDT/BRL public market collector

Public-data-only, read-only Binance Spot USDTBRL collector. GitHub Actions requests data every 15 minutes, publishes JSON snapshots, and keeps a rolling history. **No Binance API keys, accounts, trading or withdrawal permissions required.**

## Data feeds
- [Latest snapshot](data/latest.json): Binance best bid/ask, 24-hour summary, 15m and 1h completed candle indicators, and order-book depth.
- [History](data/history.json): up to 7 days of sampled quotes.
- [Status](data/status.json): last collection succeeded or failed.

When enabled and working:
- https://raw.githubusercontent.com/Nel-E/binance-usdtbrl-collector/main/data/latest.json
- https://raw.githubusercontent.com/Nel-E/binance-usdtbrl-collector/main/data/status.json
- https://raw.githubusercontent.com/Nel-E/binance-usdtbrl-collector/main/data/history.json

## Running it
1. Open **Actions → Collect Binance USDTBRL public market data**.
2. Choose **Run workflow** and verify the run completes successfully.
3. The workflow is also scheduled at minutes 3, 18, 33, and 48 of every hour.
4. If saving results fails, check **Settings → Actions → General → Workflow permissions** and allow read/write repository content as permitted.

Run locally: `python3 -m unittest discover -s tests -v && python3 collector.py` (standard library only).

**Limitations:** This is a roughly 15-minute public REST snapshot collector, not a live streaming price feed. Scheduled GitHub Actions can be delayed or skipped. REST endpoints are queried sequentially and may disagree slightly; buy/sell walls can be cancelled and are not forecasts. Never infer an executable fill solely from last-traded price. Its data do not include NZD or personal positions: the ChatGPT hourly report checks USD/NZD independently. On collection failure, check `data/status.json` and treat previous snapshots as stale.

Reference: [Binance public market data](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md) · [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
