# Where `chart.umd.min.js` comes from

**In short:** this is genuine Chart.js 4.5.1. It was **built from the official source code**, because the ready-made file on npm and the CDNs could not be downloaded in the build environment. It works the same way, but it is not byte-for-byte the npm file, so its integrity hash is different from the CDN's.

| | |
|---|---|
| Library | Chart.js **4.5.1**, MIT licence ([`LICENSE.md`](LICENSE.md)) |
| Source | `https://github.com/chartjs/chart.js`, tag `v4.5.1`, commit `9c5cf9fac7ec04a71b516e2aff3f7d76876be369` ("Bump version to 4.5.1 (#12132)", 13 October 2025) |
| Its one runtime dependency | `@kurkle/color` **0.3.2**, the version pinned in Chart.js's `pnpm-lock.yaml`; MIT licence ([`LICENSE-kurkle-color.md`](LICENSE-kurkle-color.md)); `https://github.com/kurkle/color`, tag `v0.3.2`, commit `eb0d1db26a35704553c93782bad8c3ac2d971597` |
| Build | `scripts/build_chartjs.mjs` with esbuild 0.28.2. It reproduces the UMD build in Chart.js's own `rollup.config.js`: entry `src/index.umd.ts`, target ES2022, minified, MIT banner, global `window.Chart` |
| Size | 207,635 bytes (the npm file is about 200 KB; the minifiers differ) |
| SHA-384 (SRI) | `sha384-rBeQHxx+fD4yTWwymMLsGUggrWAk1ssEcKZuxBdYt+43t//g2T2q49rj78sPe7D3` |
| Reproducible | yes: running the script again on the same commits gives an identical file (checked) |

**What was verified with this file** (see `docs/TRADELEDGER_FINAL_QA_REPORT.md`):
- all ten `chartjs_real` browser tests pass in Chromium, covering the seven charts, the data, tooltips, filters, resizing, CSP and SRI;
- `Chart.version` reports `4.5.1`;
- the registered controllers are bar, bubble, doughnut, line, pie, polarArea, radar and scatter.

**What was not verified:** Chart.js's own test suite. That needs Karma and many npm packages, which were unavailable.

**To use the official npm file instead** (CI does this as an extra check):
1. Copy `package/dist/chart.umd.min.js` from `npm pack chart.js@4.5.1` over this file.
2. Put its hash in `CHART_JS_BUNDLED_INTEGRITY` in `tradeledger/config.py`.

`tests/test_chartjs_bundle.py` fails if the file and the configured hash ever disagree.
