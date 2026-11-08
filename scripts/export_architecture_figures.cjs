/** Export the five SVG diagrams to PNG with Chinese font rendering. */
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const names = [
  '00-business-overview',
  '01-system-architecture',
  '02-routing-and-execution',
  '03-ingestion-and-retrieval',
  '04-task-sequence',
];

(async () => {
  const dir = path.resolve(__dirname, '../docs/diagrams');
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.DIAGRAM_BROWSER ? { executablePath: process.env.DIAGRAM_BROWSER } : {}),
  });
  try {
    const page = await browser.newPage({ deviceScaleFactor: 2 });
    for (const name of names) {
      const svg = await fs.readFile(path.join(dir, name + '.svg'), 'utf8');
      await page.setContent('<html><meta charset="utf-8"><style>html,body{margin:0}svg{display:block}</style><body>' + svg + '</body></html>');
      await page.evaluate(() => document.fonts.ready);
      const size = await page.locator('svg').evaluate(svg => ({
        width: svg.width.baseVal.value, height: svg.height.baseVal.value,
      }));
      await page.setViewportSize(size);
      const issues = await page.locator('svg').evaluate(svg => {
        const failures = [];
        for (const text of svg.querySelectorAll('text')) {
          const b = text.getBBox();
          if (b.x < 0 || b.y < 0 || b.x + b.width > svg.width.baseVal.value || b.y + b.height > svg.height.baseVal.value) {
            failures.push('outside canvas: ' + text.textContent);
          }
        }
        for (const group of svg.querySelectorAll('g[data-node]')) {
          const r = group.querySelector('rect').getBBox();
          for (const text of group.querySelectorAll('text')) {
            const b = text.getBBox();
            if (b.x < r.x + 8 || b.x + b.width > r.x + r.width - 8 || b.y < r.y + 4 || b.y + b.height > r.y + r.height - 4) {
              failures.push('outside node padding: ' + text.textContent);
            }
          }
        }
        return failures;
      });
      if (issues.length) throw new Error(name + ': ' + issues.join('; '));
      await page.screenshot({ path: path.join(dir, name + '.png'), fullPage: true });
      console.log(name + '.png');
    }
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
