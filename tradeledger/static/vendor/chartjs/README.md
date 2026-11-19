# Self-hosted Chart.js

Third-party code lives here, never mixed with TradeLedger's own files in `static/js/`.

| File | What it is |
|---|---|
| `chart.umd.min.js` | Chart.js 4.5.1, served by TradeLedger itself (the default since Phase 8.5) |
| `LICENSE.md` | Chart.js's MIT licence |
| `LICENSE-kurkle-color.md` | MIT licence of `@kurkle/color` 0.3.2, which is bundled inside the file |
| `BUILD.md` | Where the file comes from, its SHA-384 hash, and how to rebuild it |

The dashboard loads `/static/vendor/chartjs/chart.umd.min.js` with a Subresource Integrity (SRI) hash, `CHART_JS_BUNDLED_INTEGRITY` in `tradeledger/config.py`. The browser refuses the file if it has been changed.

To load it from the jsDelivr CDN instead, set both of these:
- `TRADELEDGER_CHART_JS_URL=https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js`
- `TRADELEDGER_CHART_JS_INTEGRITY` to the CDN file's hash.

Real-chart tests: `python -m pytest -m chartjs_real -ra`.
