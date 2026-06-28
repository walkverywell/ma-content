import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = 'file:///' + path.join(__dirname, 'index.html').replace(/\\/g, '/');
const outDir = path.join(__dirname, 'output');

const browser = await chromium.launch();
const page = await browser.newPage();

await page.goto(htmlPath, { waitUntil: 'networkidle' });
// Extra wait for fonts
await page.waitForTimeout(2500);

// 21:9 cover — 2100×900
const wide = page.locator('#wechat-21x9');
await wide.screenshot({
  path: path.join(outDir, 'wechat-21x9-cover.png'),
  type: 'png',
});
console.log('✓ wechat-21x9-cover.png');

// 1:1 square — 1080×1080
const square = page.locator('#wechat-1x1');
await square.screenshot({
  path: path.join(outDir, 'wechat-1x1-cover.png'),
  type: 'png',
});
console.log('✓ wechat-1x1-cover.png');

await browser.close();
console.log('Done. Files in output/');
