// The web build's sound, measured (docs/WEB.md): the site directory served WITHOUT the COOP/COEP
// headers, as tools/webcheck.mjs serves it, in a Chrome WITH A WINDOW (headless Chrome has no audio
// device, so the port would play into nothing and never run dry). Each game is started in a fresh
// profile (its first visit: the MT-32 when the ROMs are packed) and played, untouched, for SECONDS;
// the port's audio_underruns() is read once a second. Fails when the count rises after the first
// second. Usage: node tools/audiocheck-web.mjs SITE [SECONDS] [GAME...]
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
import puppeteer from 'puppeteer';
const site = path.resolve(process.argv[2]); const seconds = +(process.argv[3] || 60);
const games = process.argv.slice(4).length ? process.argv.slice(4) : ['uw1', 'uw2'];
const types = { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.data': 'application/octet-stream', '.css': 'text/css' };
const server = http.createServer((q, r) => {
  const f = path.join(site, decodeURIComponent(q.url.split('?')[0]).replace(/\/$/, '/index.html'));
  if (!f.startsWith(site) || !fs.existsSync(f)) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(r);
}).listen(0);
const url = `http://localhost:${server.address().port}/`;
const browser = await puppeteer.launch({ headless: false, args: ['--autoplay-policy=no-user-gesture-required'] });
let fails = 0;
for (const g of games) {
  const ctx = await browser.createBrowserContext(); const p = await ctx.newPage(); await p.setViewport({ width: 1280, height: 900 });
  p.on('dialog', d => d.dismiss());
  const log = []; p.on('console', m => { const t = m.text(); log.push(t); if (process.env.ALL || /mt32|music|audio|sound|card|driver/i.test(t)) console.log(`     ${g} console: ${t}`); });
  // the page's audio contexts, kept so that their state can be read: the device really running
  await p.evaluateOnNewDocument(() => { const A = window.AudioContext; window.__ctxs = [];
    window.AudioContext = class extends A { constructor(...a) { super(...a); window.__ctxs.push(this); } };
    // and what reaches the speakers: an analyser beside each connection to a destination, its level read once a second
    const connect = AudioNode.prototype.connect; window.__taps = [];
    AudioNode.prototype.connect = function (d, ...r) {
      if (d instanceof AudioDestinationNode) { const a = this.context.createAnalyser(); a.fftSize = 2048; connect.call(this, a); window.__taps.push(a); }
      return connect.call(this, d, ...r); };
    window.__level = () => { let m = 0; const b = new Float32Array(2048);
      for (const a of window.__taps) { a.getFloatTimeDomainData(b); for (const v of b) m = Math.max(m, Math.abs(v)); } return m; }; });
  // THROTTLE=N slows the page's CPU N times (Chrome's emulation): a check that a starved port is seen to run dry
  if (process.env.THROTTLE) await (await p.createCDPSession()).send('Emulation.setCPUThrottlingRate', { rate: +process.env.THROTTLE });
  await p.goto(url, { waitUntil: 'networkidle0' });
  await p.waitForFunction(() => window.crossOriginIsolated === true, { timeout: 20000 });
  await p.click(`#play-${g}`);
  await p.waitForSelector('#game:not([hidden])', { timeout: 120000 });
  // the count each second from the game's start (main called)
  const counts = [], levels = [];
  for (let s = 1; s <= seconds; s++) {
    await new Promise(r => setTimeout(r, 1000));
    counts.push(await p.evaluate(() => { try { return window.__exhumeModule._audio_underruns(); } catch { return -1; } }));
    levels.push(await p.evaluate(() => window.__level()));
    if (process.env.SHOTS && s % 15 === 0) await p.screenshot({ path: path.join(process.env.SHOTS, `${g}-${s}s.png`) });   // SHOTS=DIR: what was playing
    // PAUSE_AT=S: the settings screen opened at second S (the game paused), a check that a ring left to run dry is counted
    if (+process.env.PAUSE_AT === s) await p.evaluate(() => window.__exhumeModule._web_open_settings());
  }
  // the music card the game was started with: UW.CFG's first number, from the home's copy (the port
  // writes the first visit's choice there) or the game's own; the MT-32 is card 6 in UW1, 5 in UW2
  const cfg = await p.evaluate(g => { const M = window.__exhumeModule, found = [];
    const walk = d => { for (const n of M.FS.readdir(d)) { if (n === '.' || n === '..') continue; const f = `${d}/${n}`;
      if (M.FS.isDir(M.FS.stat(f).mode)) walk(f); else if (n.toUpperCase() === 'UW.CFG') found.push([f, M.FS.readFile(f, { encoding: 'utf8' }).split(/\s/)[0]]); } };
    try { walk(`/home/web_user/.${g}port`); walk('/game'); } catch (e) { found.push(['error', String(e)]); }
    return found; }, g);
  const dev = await p.evaluate(() => window.__ctxs.map(c => `${c.state} at ${c.currentTime.toFixed(1)} s, ${c.sampleRate} Hz`).join('; '));
  console.log(`     ${g}: audio device: ${dev || 'none'}`);
  const mt32 = { uw1: '6', uw2: '5' }[g];
  console.log(`     ${g}: music card ${cfg.length ? cfg[0][1] : '?'} (${cfg[0] && cfg[0][1] === mt32 ? 'the MT-32' : 'NOT the MT-32'}) from ${cfg.map(c => c.join('=')).join(', ')}`);
  console.log(`     ${g}: peak level each second: ${levels.map(l => l.toFixed(2)).join(' ')}`);
  const changes = counts.map((n, i) => [i + 1, n]).filter(([i, n]) => i === 1 || n !== counts[i - 2]);
  console.log(`     ${g}: underruns over time (s:count at each change): ${changes.map(([s, n]) => `${s}:${n}`).join(' ')}`);
  const late = counts.at(-1) - counts[0];
  const ok = counts[0] >= 0 && late === 0;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${g}: ${counts.at(-1)} underruns in ${seconds} s, ${late} after the first second`);
  if (!ok) fails++;
  await ctx.close();
}
await browser.close(); server.close();
process.exit(fails ? 1 : 0);
