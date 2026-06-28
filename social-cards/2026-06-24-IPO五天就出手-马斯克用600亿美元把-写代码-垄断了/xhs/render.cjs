
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const htmlPath = process.argv[2];
const outDir   = process.argv[3];
const specs    = JSON.parse(process.argv[4]); // [{id,file,w,h}]
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);
  for (const s of specs) {
    const el = page.locator('#' + s.id);
    if (await el.count() === 0) { console.log('跳过(无元素): ' + s.id); continue; }
    await el.screenshot({ path: path.join(outDir, s.file), type: 'png' });
    console.log('OK ' + s.file);
  }
  await browser.close();
})();
