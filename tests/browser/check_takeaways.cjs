const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: true });
      await page.route('http://step18.local/**', route => route.fulfill({ contentType: 'text/html', body:
        `<html><head><style>${fs.readFileSync('app/web/static/css/takeaways.css','utf8')}</style><script>${fs.readFileSync('app/web/static/js/explanations.js','utf8')}</script></head><body>
        <button data-explanations-toggle aria-pressed="true">Explanations: On</button><div data-numbers>42</div>
        <aside class="takeaway" data-explanation><details class="glossary-term"><summary>hedge funds</summary><span class="glossary-definition" role="note">Active investment funds.</span></details></aside></body></html>` }));
      await page.goto('http://step18.local/');
      await page.getByRole('button', { name: 'Explanations: On' }).click();
      if (await page.locator('[data-explanation]').isVisible()) throw Error('Explanations stayed visible');
      if (!(await page.locator('[data-numbers]').isVisible())) throw Error('Numbers hidden');
      await page.reload();
      await page.getByRole('button', { name: 'Explanations: Off' }).waitFor();
      if (await page.locator('[data-explanation]').isVisible()) throw Error('Cookie did not persist');
      await page.getByRole('button', { name: 'Explanations: Off' }).click();
      await page.locator('.glossary-term summary').focus(); await page.keyboard.press('Enter');
      if (!(await page.locator('.glossary-definition').isVisible())) throw Error('Keyboard glossary failed');
      await page.locator('.glossary-term summary').click();
      await page.locator('.glossary-term summary').tap();
      if (!(await page.locator('.glossary-definition').isVisible())) throw Error('Tap glossary failed');
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('Explanations default/cookie/reload and glossary keyboard/tap passed at 1440 and 390');
})().catch(error => { console.error(error.message); process.exit(1); });
