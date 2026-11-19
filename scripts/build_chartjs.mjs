// Rebuild tradeledger/static/vendor/chartjs/chart.umd.min.js from the
// OFFICIAL Chart.js 4.5.1 and @kurkle/color 0.3.2 source tags (Phase 8.5).
//
// Why this exists: the published npm/CDN file could not be downloaded in the
// build environment, but the official Git repositories could be cloned. This
// script reproduces Chart.js's own UMD build (rollup.config.js in the
// Chart.js repository: input src/index.umd.ts, target ES2022, minified, MIT
// banner, global `Chart`) with esbuild instead of rollup + swc + terser.
// The result is genuine Chart.js 4.5.1 code, but it is NOT byte-identical to
// the npm file, so its SRI hash differs from the CDN's.
//
// Usage (needs Node 18+ and esbuild):
//   git clone --depth 1 --branch v4.5.1 https://github.com/chartjs/chart.js  /tmp/chart.js
//   git clone --depth 1 --branch v0.3.2 https://github.com/kurkle/color      /tmp/color
//   node scripts/build_chartjs.mjs /tmp/chart.js /tmp/color [path/to/esbuild/lib/main.js]
//   openssl dgst -sha384 -binary tradeledger/static/vendor/chartjs/chart.umd.min.js | base64
// Expected source commits: chart.js 9c5cf9fac7ec04a71b516e2aff3f7d76876be369,
// color eb0d1db26a35704553c93782bad8c3ac2d971597 (checked below).

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const EXPECTED = {
  chartjs: { version: '4.5.1', commit: '9c5cf9fac7ec04a71b516e2aff3f7d76876be369' },
  color: { version: '0.3.2', commit: 'eb0d1db26a35704553c93782bad8c3ac2d971597' },
};

const [chartDir, colorDir, esbuildPath = 'esbuild'] = process.argv.slice(2);
if (!chartDir || !colorDir) {
  console.error('usage: node scripts/build_chartjs.mjs <chart.js checkout> <color checkout> [esbuild module]');
  process.exit(2);
}

function check(dir, name) {
  const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
  const commit = execFileSync('git', ['-C', dir, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  if (pkg.version !== EXPECTED[name].version || commit !== EXPECTED[name].commit) {
    console.error(`${name}: expected ${EXPECTED[name].version} @ ${EXPECTED[name].commit}, got ${pkg.version} @ ${commit}`);
    process.exit(1);
  }
}
check(chartDir, 'chartjs');
check(colorDir, 'color');

const esbuildModule = esbuildPath === 'esbuild' ? 'esbuild' : pathToFileURL(resolve(esbuildPath)).href;
const { build } = await import(esbuildModule);
const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const banner = `/*!
 * Chart.js v4.5.1
 * https://www.chartjs.org
 * (c) 2025 Chart.js Contributors
 * Released under the MIT License
 */
/*! @kurkle/color v0.3.2 | https://github.com/kurkle/color | (c) 2023 Jukka Kurkela | Released under the MIT License */`;

await build({
  entryPoints: [join(chartDir, 'src/index.umd.ts')],
  bundle: true,
  minify: true,
  format: 'iife',
  target: 'es2022',
  platform: 'browser',
  banner: { js: banner },
  footer: { js: 'if(typeof module!=="undefined"&&module.exports){module.exports=window.Chart;}' },
  alias: { '@kurkle/color': join(colorDir, 'src/index.esm.js') },
  legalComments: 'none',
  outfile: join(project, 'tradeledger/static/vendor/chartjs/chart.umd.min.js'),
  logLevel: 'warning',
});
console.log('built tradeledger/static/vendor/chartjs/chart.umd.min.js');
