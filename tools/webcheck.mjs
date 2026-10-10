// The web build's page tests (docs/WEB.md): the site directory served WITHOUT the COOP/COEP
// headers, as GitHub Pages serves it, in headless Chrome. Usage: node tools/webcheck.mjs SITE [CASE...]
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
import puppeteer from 'puppeteer';
const site = path.resolve(process.argv[2]); const only = process.argv.slice(3);
const types = { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.data': 'application/octet-stream', '.css': 'text/css' };
const server = http.createServer((q, r) => {
  const f = path.join(site, decodeURIComponent(q.url.split('?')[0]).replace(/\/$/, '/index.html'));
  if (!f.startsWith(site) || !fs.existsSync(f)) { r.writeHead(404); return r.end(); }
  if (stallData && path.extname(f) === '.data') return stall(fs.readFileSync(f), r);
  r.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' });
  if (slowData && path.extname(f) === '.data') return trickle(fs.readFileSync(f), r, slowData);
  fs.createReadStream(f).pipe(r);
}).listen(0);
// a game's .data served at about 6 MB/s while slowData is set (the controls case), so its download
// takes seconds and the page's progress can be seen to move, however fast the machine
// (slowData the milliseconds between its 256 KB pieces: 40, about 6 MB/s)
let slowData = 0;
async function trickle(buf, r, gap) {
  for (let i = 0; i < buf.length; i += 256 << 10) { r.write(buf.subarray(i, i + (256 << 10))); await new Promise(t => setTimeout(t, gap)); }
  r.end();
}
// a .data that stops half way and never ends, while stallData is set (the stall case); the
// responses are ended when the case is done
let stallData = false; const stuck = [];
function stall(buf, r) {
  r.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': buf.length });
  r.write(buf.subarray(0, buf.length >> 1)); stuck.push(r);
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
    slowData = 40;
    try {
      await p.click('#play-uw1');
      await p.waitForFunction(() => !document.getElementById('game').hidden || !!document.getElementById('status').textContent, { timeout: 60000 });
    } finally { slowData = 0; }
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
  async saves() {  // the home (settings, saved games, recordings) kept in IndexedDB: written back as it changes, there after a reload
    // one fresh context throughout, so that its IndexedDB is empty at the start and kept across the reloads
    const ctx = await browser.createBrowserContext(); const p = await page(ctx); let log = []; p.on('console', m => log.push(m.text()));
    const home = '/home/web_user/.uw1port', sleep = ms => new Promise(r => setTimeout(r, ms));
    const play = async () => {
      await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
      log = []; await p.click('#play-uw1');
      let lit = 0; for (let i = 0; i < 90 && lit <= 10000; i++) { lit = await litPixels(p); if (lit <= 10000) await sleep(500); }
      return lit > 10000;
    };
    const read = f => p.evaluate(f => { try { return window.__exhumeModule.FS.readFile(f, { encoding: 'utf8' }); } catch { return null; } }, f);
    // a file's contents as kept in IndexedDB (Emscripten's IDBFS: a database named by the mount
    // point, store FILE_DATA, keyed by the path), or null: what a reload would find
    const stored = f => p.evaluate((h, f) => new Promise(ok => { const q = indexedDB.open(h); q.onerror = () => ok(null);
      q.onsuccess = () => { const db = q.result; try { const g = db.transaction('FILE_DATA').objectStore('FILE_DATA').get(f);
        g.onsuccess = () => { db.close(); ok(g.result && g.result.contents ? new TextDecoder().decode(g.result.contents) : null); }; g.onerror = () => { db.close(); ok(null); };
      } catch { db.close(); ok(null); } }; }), home, f);
    await p.goto(url, { waitUntil: 'networkidle0' });
    check(await play(), 'saves: uw1 draws its title');
    // a setting changed through the settings screen (port_config_set): the gear, Down to Volume, Left (100 to 90)
    await p.$eval('#gear', b => b.click()); await sleep(500);
    for (const k of ['ArrowDown', 'ArrowDown', 'ArrowDown', 'ArrowLeft']) { await p.keyboard.press(k); await sleep(150); }
    await p.keyboard.press('Escape');
    check(/volume=90/.test(await read(`${home}/uw1port.cfg`) || ''), 'saves: the settings screen writes volume=90', JSON.stringify(await read(`${home}/uw1port.cfg`)));
    await sleep(2000);
    check(/volume=90/.test(await stored(`${home}/uw1port.cfg`) || ''), 'saves: the setting is copied to IndexedDB when written', JSON.stringify(await stored(`${home}/uw1port.cfg`)));
    // a saved game's file, put in the home's save folder through the page's FS (a scripted save in
    // the game is too slow here), copied when the page is hidden (a player switching tabs)
    await p.evaluate(h => { const F = window.__exhumeModule.FS; F.mkdirTree(`${h}/SAVE4`); F.writeFile(`${h}/SAVE4/WEBCHECK.DAT`, 'kept when hidden'); }, home);
    await sleep(1500);
    const before = await stored(`${home}/SAVE4/WEBCHECK.DAT`);
    await p.evaluate(() => { Object.defineProperty(document, 'visibilityState', { get: () => 'hidden', configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
    await sleep(2000);
    check(before === null && await stored(`${home}/SAVE4/WEBCHECK.DAT`) === 'kept when hidden', 'saves: the page hidden, the home is copied to IndexedDB', JSON.stringify(before));
    // a reload as the page's own (location.reload, as a player's F5): puppeteer's reload bypasses
    // the cache, and the service worker, so the page reloaded that way is not isolated
    await Promise.all([p.waitForNavigation({ waitUntil: 'networkidle0' }), p.evaluate(() => location.reload())]);
    check(await play(), 'saves: uw1 draws its title after a reload');
    const cfg = await read(`${home}/uw1port.cfg`);
    check(/(^|\n)volume=90(\n|$)/.test(cfg || ''), 'saves: the settings file has volume=90 after the reload', JSON.stringify(cfg));
    check(log.some(l => /settings: loaded volume=90/.test(l)), 'saves: the port reads volume 90 at its start', log.filter(l => /settings/.test(l)).join(' | '));
    check(await read(`${home}/SAVE4/WEBCHECK.DAT`) === 'kept when hidden', 'saves: a file in a save folder, copied when the page was hidden, is there after the reload');
    // the game's end (the window closed): the home copied before the page reloads itself, the
    // session's recording closed first (its header's count FFFFFFFFh, written only by the close)
    await p.evaluate(h => { const F = window.__exhumeModule.FS; F.mkdirTree(`${h}/SAVE4`); F.writeFile(`${h}/SAVE4/WEBCHECK2.DAT`, 'kept at the end'); }, home);
    const gone = p.waitForNavigation({ timeout: 20000 }).catch(() => null);
    await p.evaluate(() => window.__exhumeModule._exhume_quit());
    await gone;
    // the end waited for the game's thread to stop at a clock read (parked) before it wrote out
    // the files and closed the recording, then stopped the loop (loop_end's "presents" line)
    const at = re => log.findIndex(l => re.test(l)), parkedAt = at(/web: game parked, flushing/), endAt = at(/presents, \d+ paced/);
    check(parkedAt >= 0 && endAt > parkedAt, 'saves: the end waits for the game to park before writing out its files',
          log.filter(l => /^web:|presents,/.test(l)).join(' | '));
    check(await play(), 'saves: uw1 draws its title after the game ended');
    check(await read(`${home}/SAVE4/WEBCHECK2.DAT`) === 'kept at the end', 'saves: a file written before the game ended is there after');
    // every RECORD.OUT in the home, with its header's count: the ended session's is its own,
    // recordings/YYYYMMDD-HHMMSS/RECORD.OUT, not a copy in a later session's stage/ (which leaves
    // recordings/ out), and there is no RECORDIN/ (1.0.0's recordings, one a day, through the
    // game's 8.3 names: each session of the day overwrote the last, and stage/ copied it)
    const heads = await p.evaluate(h => { const F = window.__exhumeModule.FS, out = [];
      const walk = d => { for (const n of F.readdir(d)) { if (n === '.' || n === '..') continue; const f = `${d}/${n}`;
        if (F.isDir(F.stat(f).mode)) walk(f); else if (n === 'RECORD.OUT') { const b = F.readFile(f); out.push([f.slice(h.length + 1), b.length, [...b.subarray(8, 12)].map(x => x.toString(16)).join(' ')]); } } };
      walk(h); return out; }, home);
    check(heads.some(([f, n, c]) => /^recordings\/\d{8}-\d{6}\/RECORD\.OUT$/.test(f) && n > 12 && c === 'ff ff ff ff'),
          'saves: the ended session\'s recording was closed and kept, in its own folder under recordings/', JSON.stringify(heads));
    check(heads.length > 0 && heads.every(([f]) => /^recordings\/\d{8}-\d{6}\/RECORD\.OUT$/.test(f)),
          'saves: every recording is in a session\'s folder under recordings/ (none in RECORDIN/ or a stage/)', JSON.stringify(heads));
    await ctx.close();
  },
  async refused() {  // a browser that refuses the service worker (cookies and site data blocked): a message and no game to click
    const ctx = await browser.createBrowserContext(); const p = await ctx.newPage();
    await p.evaluateOnNewDocument(() => { ServiceWorkerContainer.prototype.register = () => Promise.reject(new DOMException('blocked for the check', 'SecurityError')); });
    await p.goto(url, { waitUntil: 'networkidle0' }); await new Promise(r => setTimeout(r, 1000));
    const s = await p.evaluate(() => ({ isolated: window.crossOriginIsolated, status: document.getElementById('status').textContent,
                                        disabled: [...document.querySelectorAll('#menu button')].every(b => b.disabled) }));
    check(!s.isolated && /cannot run the game here/i.test(s.status) && /cookies/.test(s.status), 'refused: the page says why it cannot start, in plain words', JSON.stringify(s));
    check(s.disabled, 'refused: the game buttons are disabled');
    await p.evaluate(() => { window.startGame('uw1'); }); await new Promise(r => setTimeout(r, 2000));
    const t = await p.evaluate(() => ({ menu: !document.getElementById('menu').hidden, status: document.getElementById('status').textContent }));
    check(t.menu && /cannot run the game here/i.test(t.status), 'refused: a start says the same, without a technical error', JSON.stringify(t));
    await ctx.close();
  },
  async stop() {  // a game that stops (a fatal error, a halt, a trap on the game's thread): the menu back with a message, not a frozen picture
    // web_test_stop(how) (runtime/port/sys/pit.c): the game's thread stops at its next clock read
    for (const [how, what, re] of [[1, 'a fatal error', /stopped: web_test_stop: a fatal error/], [2, 'a halt', /stopped: web_test_stop: a halt/], [3, 'a trap', /stopped: it failed/]]) {
      const p = await page(); const log = []; p.on('console', m => log.push(m.text()));
      await p.goto(url, { waitUntil: 'networkidle0' }); await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
      await p.click('#play-uw1');
      let lit = 0; for (let i = 0; i < 90 && lit <= 10000; i++) { lit = await litPixels(p); if (lit <= 10000) await new Promise(r => setTimeout(r, 500)); }
      const gone = p.waitForNavigation({ timeout: 30000, waitUntil: 'networkidle0' }).then(() => true).catch(() => false);
      await p.evaluate(h => window.__exhumeModule._web_test_stop(h), how);
      const reloaded = await gone;
      const s = await p.evaluate(() => ({ menu: !document.getElementById('menu').hidden, status: document.getElementById('status').textContent }));
      check(lit > 10000 && reloaded && s.menu && re.test(s.status), `stop: ${what} brings back the menu with a message`, JSON.stringify({ lit, reloaded, ...s }) + ' ' + log.slice(-4).join(' | '));
      if (how !== 3) check(log.some(l => /web: game ended, flushing/.test(l)), `stop: ${what} writes out the game's files first`, log.filter(l => /^web:/.test(l)).join(' | '));
      await p.close();
    }
  },
  async stall() {  // the load gives up only on a stall: a slow download that keeps moving goes on; one that stops brings the menu back
    const ctx = await browser.createBrowserContext(); const p = await page(ctx); const log = []; p.on('console', m => log.push(m.text()));
    await p.evaluateOnNewDocument(() => { window.__exhumeStallMs = 2000; });
    await p.goto(url, { waitUntil: 'networkidle0' }); await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
    // a download that takes several times the stall limit (256 KB every 150 ms, about 1.7 MB/s) but never pauses for it
    slowData = 150; const t0 = Date.now(); let took = 0;
    try {
      await p.click('#play-uw2');
      await p.waitForFunction(() => !document.getElementById('game').hidden || !!document.getElementById('status').textContent, { timeout: 120000 });
      took = Date.now() - t0;
    } finally { slowData = 0; }
    const s = await p.evaluate(() => ({ game: !document.getElementById('game').hidden, status: document.getElementById('status').textContent }));
    check(s.game && took > 3 * 2000, `stall: a slow download that keeps moving is not cut off (${(took / 1000).toFixed(1)} s, limit 2 s without a byte)`, JSON.stringify(s));
    await ctx.close();
    // a download that stops half way: the menu, with the reason, on a fresh page
    const ctx2 = await browser.createBrowserContext(); const q = await page(ctx2);
    await q.evaluateOnNewDocument(() => { window.__exhumeStallMs = 2000; });
    await q.goto(url, { waitUntil: 'networkidle0' }); await q.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
    stallData = true;
    try {
      const gone = q.waitForNavigation({ timeout: 30000 }).then(() => true).catch(() => false);
      await q.click('#play-uw1');
      const reloaded = await gone;
      await q.waitForSelector('#status', { timeout: 10000 });
      const t = await q.evaluate(() => ({ menu: !document.getElementById('menu').hidden, status: document.getElementById('status').textContent }));
      check(reloaded && t.menu && /download stopped/.test(t.status), 'stall: a download that stops brings the menu back, on a fresh page, with the reason', JSON.stringify({ reloaded, ...t }));
    } finally { stallData = false; for (const r of stuck.splice(0)) r.destroy(); }
    await ctx2.close();
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
