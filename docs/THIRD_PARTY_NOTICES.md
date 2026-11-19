# Third-party software

## Chart.js 4.5.1 (bundled since Phase 8.5)

| | |
|---|---|
| What | JavaScript charting library used by the `/analytics` dashboard |
| Version | 4.5.1 (`CHART_JS_VERSION` in `tradeledger/config.py`) |
| Licence | MIT, copyright 2014-2024 Chart.js Contributors. Full text: `tradeledger/static/vendor/chartjs/LICENSE.md` |
| Bundled dependency | `@kurkle/color` 0.3.2, MIT, copyright 2018-2021 Jukka Kurkela. Full text: `tradeledger/static/vendor/chartjs/LICENSE-kurkle-color.md` |
| How it is loaded | **Served by TradeLedger itself** from `/static/vendor/chartjs/chart.umd.min.js`, with the SRI hash `CHART_JS_BUNDLED_INTEGRITY` |
| Copied into this repository? | **Yes**, with both licences and `BUILD.md` |
| Origin | Built from the official source: `github.com/chartjs/chart.js` tag `v4.5.1` (commit `9c5cf9fac7ec04a71b516e2aff3f7d76876be369`) and `github.com/kurkle/color` tag `v0.3.2` (commit `eb0d1db26a35704553c93782bad8c3ac2d971597`), using `scripts/build_chartjs.mjs` (esbuild). The npm and CDN downloads were blocked in the build environment; the GitHub repositories were not. The file is genuine Chart.js 4.5.1 code, but it is **not byte-identical** to the npm file, so its SRI hash differs from the CDN's |

**Consequences of self-hosting:**
- Charts work with no internet access.
- The Content-Security-Policy stays `script-src 'self'` on every page, the dashboard included.
- No third party sees visitors' IP addresses.

### Using the CDN instead (optional)

Set both `TRADELEDGER_CHART_JS_URL=https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js` and `TRADELEDGER_CHART_JS_INTEGRITY` to that file's hash. On a machine with internet access, compute the hash with:

```bash
curl -sL https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js \
  | openssl dgst -sha384 -binary | openssl base64 -A
```

The `/analytics` page's CSP then allows exactly that one URL. A CDN URL never inherits the bundled file's hash: there is a test for this.

### Updating the version safely

1. Read the Chart.js release notes for every version between 4.5.1 and the new one.
2. Replace `static/vendor/chartjs/chart.umd.min.js`, either with the official npm file or with a rebuild through `scripts/build_chartjs.mjs` (update its expected commits). Update `LICENSE.md` and `BUILD.md`, then put the new SHA-384 in `CHART_JS_BUNDLED_INTEGRITY` and the new version in `CHART_JS_VERSION`. `tests/test_chartjs_bundle.py` fails until the file, the hash and `BUILD.md` agree.
3. Update the version in `tests/test_chartjs_real.py` (`EXPECTED_VERSION`) and in the CI step that runs `npm pack`.
4. Run `pytest`, including `-m chartjs_real` with the new file in place, then open `/analytics` and look at every chart. Only the `chartjs_real` tests use the real library; the `chartjs_stub` tests use a stand-in and cannot catch drawing changes in a new version.


## Python packages added in Phase 8

| Package | Version | Licence | Used for |
|---|---|---|---|
| cryptography | 50.0.1 | Apache-2.0 or BSD-3-Clause | Argon2id password hashing |
| psycopg (with psycopg-binary) | 3.3.8 | LGPL-3.0 | PostgreSQL driver (imported as a library, not modified) |
| psycopg-pool | 3.3.0 | LGPL-3.0 | PostgreSQL connection pool |
| gunicorn | 26.0.0 | MIT | Production WSGI server in the Docker image |

psycopg, psycopg-pool and gunicorn are pinned from PyPI release listings, but they could **not** be installed in the Phase 8 build environment. The CI "postgres" and "docker" jobs install them.

## Container images (Phase 8)

- `python:3.13-slim` (Docker Official Image): the base of the TradeLedger image.
- `postgres:16-alpine` (Docker Official Image): the database in `compose.yaml`.

Neither is redistributed in this repository.
