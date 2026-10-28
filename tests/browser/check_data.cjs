/* Run against an isolated loopback preview; no production login or writes. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const base = process.env.DATA_BASE_URL || 'http://127.0.0.1:8017';
  const output = path.resolve(process.env.SCREENSHOT_DIR || 'data/screenshots/step17-data');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined });
  const results = [];
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let indicator;
      for (const [name, url] of [
        ['landing', '/data'], ['country', '/data/country/US'], ['indicator', null],
        ['sources', '/data/sources'], ['dataset', '/data/datasets/fx_spot?series=USD%2FJPY&start=2020-01-01'],
        ['china', '/data/country/CN']
      ]) {
        const route = url || indicator;
        const response = await page.goto(base + route);
        if (response.status() !== 200) throw new Error(`${route}: ${response.status()}`);
        await page.locator('#data-content[aria-busy="false"]').waitFor({ timeout: 90000 });
        if (await page.locator('[data-state="error"], [data-state="unavailable"]').count()) throw new Error(`${route}: failed state`);
        if (name === 'country') {
          indicator = await page.locator('.data-table-frame a[href^="/data/indicator/US/"]').first().getAttribute('href');
        }
        const dimensions = await page.evaluate(() => ({
          width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth,
          charts: document.querySelectorAll('[data-indicator-chart] canvas,[data-dataset-chart] canvas').length,
          scrollingTables: [...document.querySelectorAll('.data-table-frame')].filter(el => el.scrollWidth > el.clientWidth).length
        }));
        if (dimensions.scrollWidth > width + 1) throw new Error(`${route}: page overflow ${dimensions.scrollWidth}/${width}`);
        if (name === 'indicator' && await page.locator('[data-indicator-chart]').count()) {
          const count = () => page.evaluate(() => echarts.getInstanceByDom(document.querySelector('[data-indicator-chart]')).getOption().series.length);
          if (await count() !== 2) throw new Error('Initial chart series missing');
          await page.locator('[data-first-toggle]').check();
          if (await count() !== 3) throw new Error('First-print series missing');
          await page.locator('[data-first-toggle]').uncheck();
          if (await count() !== 2) throw new Error('First-print series did not hide');
        }
        if (name === 'dataset') {
          if (!dimensions.charts) throw new Error('Dataset chart missing');
          const download = await page.locator('a[href*="/csv?"]').getAttribute('href');
          const csv = await page.request.get(base + download);
          if (!csv.ok() || csv.headers()['x-export-row-limit'] !== '50000') throw new Error('Dataset CSV failed');
          const lines = (await csv.text()).trim().split('\n');
          if (lines.length < 2 || lines.length > 50001) throw new Error('Dataset CSV row count');
        }
        await page.screenshot({ path: path.join(output, `${name}-${width}.png`), animations: 'disabled' });
        results.push({ page: name, ...dimensions });
      }
      if (errors.length) throw new Error(errors.join('; '));
      await page.close();
    }
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ pages: results.length, widths: [1440, 390], overflow: 0, chartToggle: 'passed', csv: 'passed' }));
})().catch(error => { console.error(error.message); process.exit(1); });
