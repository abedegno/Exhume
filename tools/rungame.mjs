// Boot a linked EXE headless in DOS among the game's data files, press keys, take screenshots.
// The last stage of the method: a byte-identical EXE that also runs, and a source change
// that shows on screen, prove the decompilation is live.
//
//   node tools/rungame.mjs --data DIR [--as NAME.EXE] [--skip SUBSTRING]... EXE OUTPREFIX STEP...
//     --data DIR      the game directory, staged into C:\ (never modified)
//     --as NAME       the name the EXE runs under in C:\ (default: its own upper-cased basename)
//     --skip S        leave out staged files whose path contains S (repeatable)
//   steps:  w:MS         wait MS milliseconds
//           k:Key[,Key]  press keys (dos-mcp key names: Enter, Escape, KeyA, Digit1, ...)
//           s:NAME       screenshot to OUTPREFIX + NAME + .png
//
// Example (UW2): node tools/rungame.mjs --data ~/UWGOG/UW2 --as UW2.EXE build/LINK/out/UW2.EXE shot- w:15000 s:title k:Escape w:3000 s:menu
import { JsDosBackend } from "dos-mcp/dist/backend/jsdos.js";
import { cpSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, basename } from "node:path";
const argv = process.argv.slice(2);
let data = null, as = null; const skip = [];
while (argv[0] && argv[0].startsWith("--")) {
  const a = argv.shift();
  if (a === "--data") data = argv.shift();
  else if (a === "--as") as = argv.shift();
  else if (a === "--skip") skip.push(argv.shift());
  else { console.error("unknown option " + a); process.exit(2); }
}
const [exe, prefix, ...steps] = argv;
if (!data || !exe || !prefix) { console.error("usage: node rungame.mjs --data DIR [--as NAME] EXE OUTPREFIX STEP..."); process.exit(2); }
const name = as || basename(exe).toUpperCase();
const stage = mkdtempSync(join(tmpdir(), "exhume-run-"));
cpSync(data, stage, { recursive: true, filter: s => !skip.some(k => s.includes(k)) });
cpSync(exe, join(stage, name));
const be = new JsDosBackend({ headless: true });
try {
  await be.loadBundle({ source: stage, autoexec: [name] });
  for (const st of steps) {
    const [op, arg] = [st.slice(0, 1), st.slice(2)];
    if (op === "w") await be.wait(Number(arg));
    else if (op === "k") { for (const k of arg.split(",")) { await be.sendKeySequence([k]); await be.wait(400); } }
    else if (op === "s") { const r = await be.screenshot("png"); writeFileSync(`${prefix}${arg}.png`, r.bytes); console.log("shot", `${prefix}${arg}.png`); }
    else console.log("unknown step " + st);
  }
} finally { await be.shutdown(); rmSync(stage, { recursive: true, force: true }); }
