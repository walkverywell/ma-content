import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = 'file:///' + path.join(__dirname, 'index.html').replace(/\\/g, '/');
const outDir = path.join(__dirname, 'output');
mkdirSync(outDir, { recursive: true });

const pages_cfg = [
  { id: 'xhs-01', file: 'xhs-01-cover.png' },
  { id: 'xhs-02', file: 'xhs-02-valuation.png' },
  { id: 'xhs-03', file: 'xhs-03-payment.png' },
  { id: 'xhs-04', file: 'xhs-04-detail.png' },
  { id: 'xhs-05', file: 'xhs-05-closing.png' },
];

const browser = await chromium.launch();
const page = await browser.newPage();
await page.setViewportSize({ width: 1400, height: 900 });
await page.goto(htmlPath, { waitUntil: 'networkidle' });
await page.waitForTimeout(3000); // fonts

for (const { id, file } of pages_cfg) {
  const el = page.locator(`#${id}`);
  await el.screenshot({ path: path.join(outDir, file), type: 'png' });
  console.log(`✓ ${file}`);
}

await browser.close();
console.log('\nDone → output/');
