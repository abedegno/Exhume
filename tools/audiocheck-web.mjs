// The web build's sound, measured (docs/WEB.md): the site directory served WITHOUT the COOP/COEP
// headers, as tools/webcheck.mjs serves it, in a Chrome WITH A WINDOW (headless Chrome has no audio
// device, so the port would play into nothing and never run dry). Each game is started in a fresh
// profile (its first visit: the MT-32 when the ROMs are packed) and played, untouched, for SECONDS;
// the port's audio_underruns() is read once a second. Fails when the count rises after the first
// second, and fails closed when a zero proves nothing: when the page's audio context is not
// running at the end, when nothing reached the speakers (the peak level over the run), or when the
// port's "audio: music card" line does not name a sounding MT-32 (ANY_CARD=1 lets any card through,
// for a site packed without ROMs). Usage: node tools/audiocheck-web.mjs SITE [SECONDS] [GAME...]
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
  if (process.env.SUSPEND) await p.evaluateOnNewDocument(() => { window.__suspend = true; });
  await p.evaluateOnNewDocument(() => { const A = window.AudioContext; window.__ctxs = [];
    window.AudioContext = class extends A { constructor(...a) { super(...a); window.__ctxs.push(this);
      // SUSPEND=1: every context held suspended (a check that a silent device fails the run)
      if (window.__suspend) { this.suspend(); this.resume = () => Promise.resolve(); } } };
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
  // the music card the port opened: its own line (sound/audio.c, the web only), the last one said;
  // and, for the record, the choice it wrote to the home's DATA/UW.CFG (the MT-32 is 6 in UW1, 5 in UW2)
  const said = log.map(l => /: audio: music card (\S+)( \(silent\))?/.exec(l)).filter(Boolean).at(-1);
  const cfg = await p.evaluate(g => { const f = `/home/web_user/.${g}port/DATA/UW.CFG`, M = window.__exhumeModule;
    try { return M.FS.analyzePath(f).exists ? `${f}: ${M.FS.readFile(f, { encoding: 'utf8' }).split(/\s/)[0]}` : `${f}: none`; } catch (e) { return `${f}: ${e}`; } }, g);
  const ctxs = await p.evaluate(() => window.__ctxs.map(c => ({ state: c.state, t: c.currentTime, rate: c.sampleRate })));
  const peak = Math.max(0, ...levels);
  console.log(`     ${g}: audio device: ${ctxs.map(c => `${c.state} at ${c.t.toFixed(1)} s, ${c.rate} Hz`).join('; ') || 'none'}`);
  console.log(`     ${g}: peak level each second: ${levels.map(l => l.toFixed(2)).join(' ')}`);
  const changes = counts.map((n, i) => [i + 1, n]).filter(([i, n]) => i === 1 || n !== counts[i - 2]);
  console.log(`     ${g}: underruns over time (s:count at each change): ${changes.map(([s, n]) => `${s}:${n}`).join(' ')}`);
  const results = [
    [ctxs.length > 0 && ctxs.every(c => c.state === 'running'), `the audio device is running (${ctxs.map(c => c.state).join(', ') || 'no audio context'})`],
    [peak > 0.001, `sound reached the speakers (peak level ${peak.toFixed(2)})`],
    [!!said && (process.env.ANY_CARD || (said[1] === 'mt32' && !said[2])),
     `${process.env.ANY_CARD ? 'the port names its music card (ANY_CARD: any card)' : 'the music plays on the MT-32'} (the port: ${said ? said[0].slice(2) : 'no "audio: music card" line'}; ${cfg})`],
  ];
  const late = counts.at(-1) - counts[0];
  results.push([counts[0] >= 0 && late === 0, `${counts.at(-1)} underruns in ${seconds} s, ${late} after the first second`]);
  for (const [good, what] of results) console.log(`${good ? 'ok  ' : 'FAIL'} ${g}: ${what}`);
  const ok = results.every(r => r[0]);
  if (!ok) fails++;
  await ctx.close();
}
await browser.close(); server.close();
process.exit(fails ? 1 : 0);
