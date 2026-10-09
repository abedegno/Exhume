// The web build's page tests (docs/WEB.md): the site directory served WITHOUT the COOP/COEP
// headers, as GitHub Pages serves it, in headless Chrome. Usage: node tools/webcheck.mjs SITE [CASE...]
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
import puppeteer from 'puppeteer';
const site = path.resolve(process.argv[2]); const only = process.argv.slice(3);
const types = { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.data': 'application/octet-stream', '.css': 'text/css' };
const server = http.createServer((q, r) => {
  const f = path.join(site, decodeURIComponent(q.url.split('?')[0]).replace(/\/$/, '/index.html'));
  if (!f.startsWith(site) || !fs.existsSync(f)) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' }); fs.createReadStream(f).pipe(r);
}).listen(0);
const url = `http://localhost:${server.address().port}/`;
let fails = 0; const check = (ok, what, more = '') => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}${ok ? '' : ' ' + more}`); if (!ok) fails++; };
const browser = await puppeteer.launch({ headless: 'new', args: ['--autoplay-policy=no-user-gesture-required'] });
async function page() {
  const p = await browser.newPage(); await p.setViewport({ width: 1280, height: 900 });
  p.on('dialog', d => { console.log(`     (dialog: ${d.message().split('\n')[0]})`); d.dismiss(); });   // an alert() would stall the page
  return p;
}
// The canvas's pixels brighter than black, as the page shows them: from a screenshot of the canvas,
// since a WebGL canvas (SDL's renderer) reads back blank once its frame has been shown
async function litPixels(p) {
  const el = await p.$('canvas'); const box = el && await el.boundingBox(); if (!box || !box.width || !box.height) return 0;
  const png = await el.screenshot({ encoding: 'base64' });
  return p.evaluate(async src => { const i = new Image(); i.src = 'data:image/png;base64,' + src; await i.decode();
    const x = document.createElement('canvas'); x.width = i.width; x.height = i.height; const k = x.getContext('2d'); k.drawImage(i, 0, 0);
    const d = k.getImageData(0, 0, x.width, x.height).data; let n = 0; for (let j = 0; j < d.length; j += 4) n += d[j] + d[j + 1] + d[j + 2] > 30; return n; }, png);
}
const cases = {
  async boot() {  // the service worker supplies the headers: after its one reload the page is isolated and offers both games
    const p = await page(); await p.goto(url, { waitUntil: 'networkidle0' });
    await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 }).catch(() => {});
    check(await p.evaluate(() => window.crossOriginIsolated), 'boot: the page is cross-origin isolated (threads possible)');
    for (const g of ['uw1', 'uw2']) check(!!(await p.$(`#play-${g}`)), `boot: a button for ${g}`);
    await p.close();
  },
  async start() {  // a click starts each game: a few seconds in, the canvas is not blank
    for (const g of ['uw1', 'uw2']) {
      const p = await page(); await p.goto(url, { waitUntil: 'networkidle0' });
      await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
      await p.click(`#play-${g}`);
      await new Promise(r => setTimeout(r, 15000));
      const lit = await litPixels(p);
      check(lit > 10000, `start: ${g} draws its title (${lit} lit pixels)`);
      await p.screenshot({ path: path.join(site, `..`, `webcheck-${g}.png`) });
      await p.close();
    }
  },
};
for (const [n, f] of Object.entries(cases)) if (!only.length || only.includes(n)) await f().catch(e => check(false, `${n}: threw`, e.message));
await browser.close(); server.close();
console.log(fails ? `webcheck: ${fails} failed` : 'webcheck: all passed'); process.exit(fails ? 1 : 0);
