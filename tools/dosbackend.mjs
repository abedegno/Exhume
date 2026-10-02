// The DOS the toolchain runs in. tools/dosrun.mjs stages a directory that becomes C:\ (the
// toolchain, the sources, a batch file ending in `echo DONE > DONE.TXT`) and calls runStage();
// this module runs the batch in one of four emulators and hands back the files.
//
//   EXHUME_DOS=emu2      emu2 (github.com/dmsc/emu2) with tools/emu2-date.patch, built by
//                        tools/setup-emu2.sh into tools/emu2/: each batch line runs as its own
//                        emu2 process, with no COMMAND.COM (see runEmu2 for the lines it takes)
//   EXHUME_DOS=dosbox-x  native DOSBox-X, headless (SDL's dummy drivers), C: mounted on a copy
//                        of the stage
//   EXHUME_DOS=staging   native DOSBox Staging (~/Applications/DOSBox Staging.app or `dosbox`
//                        on PATH); its macOS build has no headless video driver, so each run
//                        opens a window for a second. Never picked automatically.
//   EXHUME_DOS=jsdos     js-dos (DOSBox in WebAssembly, in headless Chrome, through dos-mcp)
//   unset or auto        the first of DEFAULT_ORDER that is installed, else jsdos
//
// exhume.toml's [toolchain] dos sets EXHUME_DOS for every tool that loads the config, unless
// the environment already sets it (tools/config.py). EXHUME_EMU2, EXHUME_DOSBOX_X and
// EXHUME_DOSBOX_STAGING name a binary.
//
// On UW2 all four give the same objects, but for the time stamps Turbo C records for each
// source and header (which differ between two js-dos runs too), and the same EXE
// (docs/method.md, "Choosing the DOS"). tools/rungame.mjs, which runs the program itself,
// always uses js-dos: it needs a screen, keys and dos-mcp's memory reads.
//
//   node tools/dosbackend.mjs      prints the backend in use (tools/dosbatch.py asks this way)
import { spawn, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, openSync, closeSync, statSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { join, delimiter } from "node:path";

export const BACKENDS = ["emu2", "dosbox-x", "staging", "jsdos"];
export const DEFAULT_ORDER = ["emu2", "dosbox-x", "jsdos"];
const here = new URL(".", import.meta.url).pathname;

function onPath(name) {
  for (const d of (process.env.PATH || "").split(delimiter)) {
    const p = join(d, name);
    try { if (statSync(p).isFile()) return p; } catch { }
  }
  return null;
}

/** The emulator binary for a backend, or null when it is not installed. */
export function binary(name) {
  const env = { "dosbox-x": "EXHUME_DOSBOX_X", staging: "EXHUME_DOSBOX_STAGING", emu2: "EXHUME_EMU2" }[name];
  if (env && process.env[env]) return existsSync(process.env[env]) ? process.env[env] : null;
  if (name === "dosbox-x") return onPath("dosbox-x");
  if (name === "staging") {
    for (const p of [join(homedir(), "Applications/DOSBox Staging.app/Contents/MacOS/dosbox"),
      "/Applications/DOSBox Staging.app/Contents/MacOS/dosbox"]) if (existsSync(p)) return p;
    const p = onPath("dosbox");
    if (p && /staging/i.test(spawnSync(p, ["--version"], { encoding: "utf8" }).stdout || "")) return p;
    return null;
  }
  // only the patched build: an unpatched emu2 cannot set the date the linker records
  if (name === "emu2") { const p = join(here, "emu2", "emu2"); return existsSync(p) ? p : null; }
  return null;
}

/** The backend in use: EXHUME_DOS when set (and not "auto"), else the first installed. */
export function backendName() {
  const want = (process.env.EXHUME_DOS || "auto").toLowerCase();
  if (want !== "auto") {
    if (!BACKENDS.includes(want)) throw new Error(`EXHUME_DOS=${want}: not ${BACKENDS.join(", ")} or auto`);
    if (want !== "jsdos" && !binary(want)) throw new Error(`EXHUME_DOS=${want}, but it is not installed (docs/method.md, "Choosing the DOS")`);
    return want;
  }
  return DEFAULT_ORDER.find(n => n === "jsdos" || binary(n));
}

// a file in dir by its DOS path, whatever case the emulator gave it on a case-sensitive disk
function findFile(dir, dosPath) {
  let p = dir;
  for (const part of dosPath.replace(/\\/g, "/").split("/").filter(Boolean)) {
    const exact = join(p, part);
    if (existsSync(exact)) { p = exact; continue; }
    let hit = null;
    try { hit = readdirSync(p).find(e => e.toUpperCase() === part.toUpperCase()); } catch { }
    if (!hit) return null;
    p = join(p, hit);
  }
  return p;
}

/**
 * Run BAT (a batch file in stage) with stage as C:\, giving up after timeout seconds.
 * Returns { backend, finished, read(name) -> Buffer | null, close() }. finished means DONE.TXT
 * exists afterwards. The stage itself is left as it was: every run works on a copy (or, in
 * js-dos, on a virtual disk), so a retry starts from the same files.
 */
export async function runStage(stage, bat, timeout = 300) {
  const name = backendName();
  if (name === "jsdos") return runJsDos(stage, bat, timeout);
  const run = mkdtempSync(join(tmpdir(), "exhume-dos-c-"));
  cpSync(stage, run, { recursive: true, preserveTimestamps: true });
  const close = async () => { rmSync(run, { recursive: true, force: true }); rmSync(run + ".date", { force: true }); };
  try {
    if (name === "emu2") runEmu2(run, bat, timeout);
    else await runDosBox(name, run, bat, timeout);
  } catch (e) { await close(); throw e; }
  const read = async n => { const p = findFile(run, n); try { return p ? readFileSync(p) : null; } catch { return null; } };
  return { backend: name, finished: !!findFile(run, "DONE.TXT"), read, close };
}

async function runJsDos(stage, bat, timeout) {
  const { JsDosBackend } = await import("dos-mcp/dist/backend/jsdos.js");
  const be = new JsDosBackend({ headless: true });
  let finished = false;
  try {
    await be.loadBundle({ source: stage, autoexec: [bat] });
    for (let i = 0; i < timeout * 2 && !finished; i++) {
      await be.wait(500);
      try { await be.fsStat("C:/DONE.TXT"); finished = true; } catch { }
    }
    if (finished) await be.wait(1000);   // let DOS finish writing before the files are read
  } catch (e) { await be.shutdown(); throw e; }
  const read = async n => { try { return Buffer.from(await be.fsRead(`C:/${n.replace(/\\/g, "/")}`)); } catch { return null; } };
  return { backend: "jsdos", finished, read, close: () => be.shutdown() };
}

// DOSBox-X's defaults that matter: no sound, no long file names (the tools are DOS 3 programs),
// no emulated disk speed limit, the fastest core.
const DOSBOX_X_CONF = `[sdl]
output=surface
[cpu]
core=dynamic
cycles=max
[mixer]
nosound=true
[speaker]
pcspeaker=false
[dos]
lfn=false
hard drive data rate limit=0
keyboardlayout=us
`;

function runDosBox(name, run, bat, timeout) {
  const exe = binary(name);
  const cmds = [`mount c "${run}"`, "c:", `call ${bat}`];
  let args, env = { ...process.env };
  if (name === "dosbox-x") {
    const conf = run + ".conf";
    writeFileSync(conf, DOSBOX_X_CONF);
    args = ["-defaultconf", "-conf", conf, "-silent", "-fastlaunch", "-nogui", "-nomenu", ...[...cmds, "exit"].flatMap(c => ["-c", c])];
    env.SDL_VIDEODRIVER = "dummy"; env.SDL_AUDIODRIVER = "dummy";
  } else {
    // Staging 0.82 never returns from an `exit` given with -c, and with core=dynamic it never
    // returns from the batch on Apple Silicon: --exit and the default core
    args = ["--noprimaryconf", "--nolocalconf", "--set", "cpu_cycles=max", "--set", "mixer nosound=true",
      "--set", "startup_verbosity=quiet", ...cmds.flatMap(c => ["-c", c]), "--exit"];
  }
  return new Promise((resolve, reject) => {
    const p = spawn(exe, args, { cwd: run, env, stdio: ["ignore", "ignore", "pipe"] });
    let err = "";
    p.stderr.on("data", d => { err = (err + d).slice(-2000); });
    const t = setTimeout(() => p.kill("SIGKILL"), timeout * 1000);
    p.on("error", e => { clearTimeout(t); reject(e); });
    p.on("exit", () => { clearTimeout(t); rmSync(run + ".conf", { force: true }); resolve(); });
  });
}

// DOS's own commands, which emu2 has no COMMAND.COM to run
const INTERNAL = /^(break|call|cd|chdir|cls|copy|ctty|date|del|dir|erase|for|goto|if|md|mkdir|path|pause|prompt|rd|ren|rename|rmdir|set|shift|time|type|ver|verify|vol)$/i;

/**
 * emu2 runs one program per process, so the batch is read here. It takes, one to a line:
 *   @echo off, rem, and blank lines (skipped)
 *   echo TEXT, with an optional > FILE or >> FILE
 *   PROGRAM ARGS, with an optional > FILE or >> FILE, where PROGRAM is an .EXE or .COM in C:\
 *     (TCC ... > STEM.LOG, TASM ..., TLIB X @LIB.RSP >> RUN.LOG, TLINK @LINK.RSP >> RUN.LOG,
 *     a program the run itself has just linked)
 * Anything else (a DOS internal command, a pipe, input redirection, two redirections, a
 * nested .BAT, a program not in C:\) stops the run with an error naming the line, rather
 * than being run wrongly. A program that runs past the time left stops the run unfinished.
 */
export function parseEmu2Line(line) {
  line = line.trim();
  if (!line || /^@?echo\s+off$/i.test(line) || /^@?rem(\s|$)/i.test(line)) return null;
  const refuse = why => { throw new Error(`emu2 backend: cannot run batch line '${line}': ${why} (use EXHUME_DOS=dosbox-x or jsdos for such a batch)`); };
  if (/[<|]/.test(line)) refuse("pipes and input redirection need COMMAND.COM");
  const m = line.match(/^([^>]*?)(?:\s*(>>?)\s*([^\s>]+))?$/);
  if (!m) refuse("one redirection only, at the end of the line");
  const [cmd, redir, file] = [m[1].trim().replace(/^@/, ""), m[2] || null, m[3] || null];
  const echo = cmd.match(/^echo(?:\s+(.*))?$/i);
  if (echo) return { echo: echo[1] || "", redir, file };
  const [prog, ...args] = cmd.split(/\s+/);
  if (!prog) refuse("no program");
  if (INTERNAL.test(prog)) refuse(`'${prog}' is a DOS internal command`);
  if (/\.BAT$/i.test(prog)) refuse("a nested batch file");
  if (/[\\/:]/.test(prog)) refuse("the program must be in C:\\ itself");
  return { prog, args, redir, file };
}

function runEmu2(run, bat, timeout) {
  const exe = binary("emu2");
  const deadline = Date.now() + timeout * 1000;
  const env = { ...process.env, EMU2_DRIVE_C: run, EMU2_DEFAULT_DRIVE: "C", EMU2_CWD: "\\",
    EMU2_DATE_FILE: run + ".date" };
  const steps = readFileSync(join(run, bat), "latin1").split(/\r?\n/).map(parseEmu2Line).filter(Boolean);
  // every line is parsed before any runs, so a batch emu2 cannot run fails at once
  for (const s of steps) {
    if (s.echo !== undefined) continue;
    const names = /\.(EXE|COM)$/i.test(s.prog) ? [s.prog] : [s.prog + ".EXE", s.prog + ".COM"];
    // a program the batch itself builds (a .COM linked by an earlier line) is found when it runs
    s.found = () => names.map(n => findFile(run, n)).find(Boolean);
    s.names = names;
  }
  for (const s of steps) {
    const target = s.file ? (findFile(run, s.file) || join(run, s.file.replace(/\\/g, "/").toUpperCase())) : null;
    if (s.echo !== undefined) {
      if (target) writeFileSync(target, s.echo + "\r\n", { flag: s.redir === ">>" ? "a" : "w" });
      continue;
    }
    const prog = s.found();
    if (!prog) throw new Error(`emu2 backend: no ${s.names.join(" or ")} in C:\\ for the batch line '${s.prog} ${s.args.join(" ")}'`);
    const left = deadline - Date.now();
    if (left <= 0) return;                                  // unfinished: no DONE.TXT
    const out = target ? openSync(target, s.redir === ">>" ? "a" : "w") : "ignore";
    let r;
    try {
      r = spawnSync(exe, [prog.slice(run.length + 1), ...s.args], { cwd: run, env, stdio: ["ignore", out, "ignore"], timeout: left });
    } finally { if (typeof out === "number") closeSync(out); }
    if (r.error && r.error.code === "ENOENT") throw new Error(`emu2 backend: cannot start ${exe}`);
    if (r.error || r.signal) return;                        // ran out of time: unfinished
  }
}

if (process.argv[1] && new URL(import.meta.url).pathname === process.argv[1]) {
  try { console.log(backendName()); } catch (e) { console.error(e.message); process.exit(1); }
}
