// The web build's page tests (docs/WEB.md): the site directory served WITHOUT the COOP/COEP
// headers, as GitHub Pages serves it, in headless Chrome. Usage: node tools/webcheck.mjs SITE [CASE...]
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
import puppeteer from 'puppeteer';
const site = path.resolve(process.argv[2]); const only = process.argv.slice(3);
const types = { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.data': 'application/octet-stream', '.css': 'text/css' };
const server = http.createServer((q, r) => {
  const f = path.join(site, decodeURIComponent(q.url.split('?')[0]).replace(/\/$/, '/index.html'));
  if (!f.startsWith(site) || !fs.existsSync(f)) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' });
  if (slowData && path.extname(f) === '.data') return trickle(fs.readFileSync(f), r);
  fs.createReadStream(f).pipe(r);
}).listen(0);
// a game's .data served at about 6 MB/s while slowData is set (the controls case), so its download
// takes seconds and the page's progress can be seen to move, however fast the machine
let slowData = false;
async function trickle(buf, r) {
  for (let i = 0; i < buf.length; i += 256 << 10) { r.write(buf.subarray(i, i + (256 << 10))); await new Promise(t => setTimeout(t, 40)); }
  r.end();
}
const url = `http://localhost:${server.address().port}/`;
let fails = 0; const check = (ok, what, more = '') => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}${ok ? '' : ' ' + more}`); if (!ok) fails++; };
const browser = await puppeteer.launch({ headless: 'new', args: ['--autoplay-policy=no-user-gesture-required'] });
async function page(ctx = browser) {
  const p = await ctx.newPage(); await p.setViewport({ width: 1280, height: 900 });
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
  async data() {  // with the data packed (web/pack.sh), each game reaches its own title screen, not the port's folder message
    // (the browser's own "Failed to load resource ... 404 (Not Found)", the favicon before the service worker's reload, is not the port's)
    for (const g of ['uw1', 'uw2']) {
      const p = await page(); const log = []; p.on('console', m => log.push(m.text()));
      await p.goto(url, { waitUntil: 'networkidle0' }); await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
      await p.click(`#play-${g}`); await new Promise(r => setTimeout(r, 20000));
      check(!log.some(l => /game folder|not found|cannot start/i.test(l) && !/^Failed to load resource/.test(l)), `data: ${g} finds its game files`, log.slice(-5).join(' | '));
      check(log.some(l => /mt32: .*ROM/i.test(l) && !/no ROMs/.test(l)), `data: ${g} finds the MT-32 ROMs`, log.filter(l => /mt32/.test(l)).join(' | '));
      // the page seeds the home with settings-at-start=0; the port logs "settings: shown" each time the screen opens
      check(!log.some(l => /settings: shown/.test(l)), `data: ${g} starts without the settings screen`, log.filter(l => /settings/.test(l)).join(' | '));
      await p.close();
    }
  },
  async missing() {  // a game whose files are absent: the page comes back to the menu and says what failed, not a black page
    const p = await page(); await p.goto(url, { waitUntil: 'networkidle0' });
    await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
    await p.evaluate(() => window.startGame('nogame'));
    const s = await p.evaluate(() => ({ menu: !document.getElementById('menu').hidden, game: !document.getElementById('game').hidden,
                                        status: document.getElementById('status').textContent }));
    check(s.menu && !s.game, 'missing: the menu is back and the game hidden', JSON.stringify(s));
    check(/nogame/.test(s.status), 'missing: the status line names what failed', JSON.stringify(s.status));
    await p.close();
  },
  async controls() {  // the page's own controls: the loading progress, the gear, full screen, and a game's end
    // a fresh context: the file packager's cache (IndexedDB) is empty, so the game's .data really downloads
    const ctx = await browser.createBrowserContext(); const p = await page(ctx); const log = []; p.on('console', m => log.push(m.text()));
    await p.goto(url, { waitUntil: 'networkidle0' }); await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
    // every text the progress line shows, from before the click
    await p.evaluate(() => { const e = document.getElementById('progress'); window.__progress = [];
      new MutationObserver(() => { if (window.__progress.at(-1) !== e.textContent) window.__progress.push(e.textContent); }).observe(e, { childList: true, characterData: true, subtree: true }); });
    slowData = true;
    try {
      await p.click('#play-uw1');
      await p.waitForFunction(() => !document.getElementById('game').hidden || !!document.getElementById('status').textContent, { timeout: 60000 });
    } finally { slowData = false; }
    // the download's own reports: megabytes and a percentage strictly between 0 and 100, at least three of them
    const shown = await p.evaluate(() => window.__progress);
    const mid = new Set(shown.map(t => /MB \((\d+)%\)/.exec(t)).filter(m => m && +m[1] > 0 && +m[1] < 100).map(m => +m[1]));
    check(mid.size >= 3, `controls: the page shows the download's progress (${mid.size} values between 0% and 100%)`, JSON.stringify(shown.slice(0, 4)));
    // the game running: its title drawn
    let lit = 0; for (let i = 0; i < 60 && lit <= 10000; i++) { lit = await litPixels(p); if (lit <= 10000) await new Promise(r => setTimeout(r, 500)); }
    check(lit > 10000, `controls: the game draws its title (${lit} lit pixels)`);
    check(!!(await p.$('#gear')) && !!(await p.$('#fullscreen')), 'controls: the gear and full-screen buttons are there');
    // the pointer locked to the canvas by a real click, as a player's would be
    await p.evaluate(() => document.getElementById('canvas').addEventListener('click', e => e.target.requestPointerLock(), { once: true }));
    await p.click('#canvas'); await p.waitForFunction(() => document.pointerLockElement !== null, { timeout: 5000 }).catch(() => {});
    check(await p.evaluate(() => document.pointerLockElement?.id === 'canvas'), 'controls: the pointer is locked to the canvas');
    // a locked pointer's clicks all go to the canvas (Chrome's pointer lock), so the gear is pressed
    // through its own click (as a keyboard's Enter on it would be), not at its place on the screen
    await p.$eval('#gear', b => b.click());
    await p.waitForFunction(() => document.pointerLockElement === null, { timeout: 3000 }).catch(() => {});
    await new Promise(r => setTimeout(r, 500));
    check(log.some(l => /settings: (open|shown)/.test(l)), 'controls: the gear opens the settings screen', log.slice(-5).join(' | '));
    check(await p.evaluate(() => document.pointerLockElement === null), 'controls: the gear frees the pointer');
    await p.evaluate(() => window.__exhumeModule._exhume_quit && window.__exhumeModule._exhume_quit());
    await p.waitForSelector('#menu:not([hidden])', { timeout: 10000 }).then(() => check(true, 'controls: a game that ends returns to the menu')).catch(() => check(false, 'controls: a game that ends returns to the menu'));
    await ctx.close();
  },
  async noworker() {  // no service worker: a message, not a blank page
    const ctx = await browser.createBrowserContext(); const p = await ctx.newPage();
    await p.evaluateOnNewDocument(() => { Object.defineProperty(navigator, 'serviceWorker', { get: () => undefined }); });
    await p.goto(url, { waitUntil: 'networkidle0' });
    check(/cannot run/i.test(await p.$eval('#status', e => e.textContent)), 'noworker: the page says why it cannot start');
    await ctx.close();
  },
};
for (const [n, f] of Object.entries(cases)) if (!only.length || only.includes(n)) await f().catch(e => check(false, `${n}: threw`, e.message));
await browser.close(); server.close();
console.log(fails ? `webcheck: ${fails} failed` : 'webcheck: all passed'); process.exit(fails ? 1 : 0);
