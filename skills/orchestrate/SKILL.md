---
name: orchestrate
description: Use when running a whole byte-matching decompilation with several AI agents - choosing models, how many agents at once, per-file build budgets, Codex drafts with Claude finishers, the build queue for sandboxed agents, and keeping agents from clobbering each other; with the costs measured on UW2. Also for an armcc5 firmware - a git worktree and a cloned Wine prefix per agent, the full gate before every merge - and for long searches: deadlines, worker and disk budgets, and stopping only the processes and prefixes a run owns.
---

# Orchestrate matching agents

Paths are relative to the Exhume checkout. You (the orchestrator) prepare target tables, hand one file to each agent, review reports, and merge. Agents never merge, commit, or edit shared files.

## Pool

- Run about three agents at once. Seven parallel Opus agents exhausted a session's usage limit in a few hours on UW2 and all seven died mid-file. Resume an interrupted agent with SendMessage (it keeps its context, which is cheaper than restarting); do not relaunch it.
- Tell every agent not to spawn sub-agents of its own, never nested agents. The three-agent limit counts every agent running, not just the ones you launched: on UW2 two commenting agents each fanned out to about five sub-agents, twelve ran at once, and the session limit stopped all of them. Restate the rule when resuming, and in every brief, including a port's and a CI's: an agent asked to "do the whole area" is the one most tempted to delegate.
- Use the strongest Claude model as the matcher and finisher. On UW2, Opus averaged about 4 KB of matched code per 170k tokens. Sonnet matched both trial files but used about five times the tokens per byte (seg014, 579 bytes: 185k tokens; seg043, 1.6 KB: 434k tokens, 53 minutes, 218 tool calls).
- Give a big file to one agent, which may fork helpers for single functions: UW2's largest file, ovr110 (20.6 KB, 68 functions), was finished by one Claude agent and seven forks after a Codex draft.
- Give each agent the brief in skills/match-file (C) or skills/match-asm (assembly), plus: its source file name, its segment and table, and a one-line description of what the file does.

## Codex

- Codex's sandbox could not start js-dos's headless browser on UW2; whether it can run emu2 or DOSBox-X is untested. Run the queue outside it: `python3 tools/buildd.py --budget 30` (backgrounded; it serves one request per DOS session, as many at once as suit the DOS in use, `--jobs` to change), and tell Codex to build with `tools/remote.sh match src/FILE.C` and `tools/remote.sh verify src/FILE.C`. The queue accepts only those two commands on files in src/.
- Measured on UW2, in input tokens (mostly cached): gpt-6-sol at medium effort, alone, about 1.3M per file of around 1 KB (ovr117: five builds). A cheap draft (gpt-6-luna) finished by sol at medium: 0.9M to 2.0M, average 1.46M over four files, counting luna at 1/20 since sol costs 20 times luna per token. sol at low effort alone: 1.8M to 2.95M. luna alone never finished a file: it quit after one to four builds, or ran away to 10M tokens ignoring caps.
- Codex stalls on files over about 2 KB and leaves stubs. The pattern that worked: Codex drafts and matches the small functions, a Claude agent finishes the rest (ovr095, ovr097, ovr103, ovr110, ovr166 and others in UW2's history).
- Codex drafts hide wrong extern names behind masked fixups. The finisher must run verify and use symbols.tsv names; ovr103's draft had about 20 wrong names.
- Tell Codex explicitly to keep going until WHOLE SEGMENT MATCHES or the budget runs out, and to end with the brief's report including the build count.

- On a firmware toolchain run under Wine, Codex's default sandbox blocks the local sockets that
  wineserver and Python's multiprocessing need (`bind: Operation not permitted`), so it can edit
  but never compile or gate. Either start it from the project root with full access
  (`codex -s danger-full-access`), or have it edit in small batches logged to a file
  (`build/codex-log.md`: file, kind of change, count) and gate and bisect the batches yourself.
  Its writable root is the directory it starts in: a project outside that directory is read-only
  to it.
- Codex is good at bulk, low-risk edits that a gate then judges: formatting, renaming locals,
  one mechanical rewrite applied everywhere. Left alone on a hard matching unit it tends to grind
  local variant sweeps far past the point of return (see match-file, "When the search stalls").

## Handing a run to another agent

When a run stops before its milestone (a usage limit, a change of agent, the owner's request),
write a handover the next agent can trust, in two parts: a portable one in the repository
(the state, what is proved, what is left) and a machine-local, gitignored one (`*.local.md`:
paths, preserved worktrees, scratch, toolchain locations; never the licence or keys). It names:

- the frozen baseline: commit, the gate command and its last lines, the blob count, so the next
  agent verifies before it trusts anything;
- every preserved worktree and scratch directory, with the hash of each best draft, and the rule
  that the next agent reads them and never modifies them;
- per unfinished unit: the best draft, its exact remaining difference, and the controls (what
  was tried and ruled out, and how many variants), so nothing exhausted is repeated;
- corrections: earlier evidence that turned out wrong (an invalid control, a mistranslated
  constant), so the next agent does not build on it;
- what is not authorised (push, PR, flashing) and the owner's standing permissions.

## Session limits and checkpoints

- A usage limit stops every running agent at once, wherever it is. Plan for it: give each agent a unit of work that can be committed on its own, and commit a checkpoint before starting the next large step (UW2Decomp committed its port's Milestone 5 as a checkpoint, c58e5b7, ninety minutes before it was done, 50459d1). After a limit, resume the agents that stopped (SendMessage keeps their context) rather than restarting them, and check what each left on disk first: a half-applied edit is worse than none.
- Write the plan and the state of the work into the repository (a design page with milestones and their exit tests, like Underworld Exhumed's uw2/docs/PORT.md), not only into the conversation, so a fresh session can pick it up.
- Keep one agent doing the gate and the commits; a limit hit mid-commit by several agents leaves a tree no one can explain.

## The DOS

- Build the fast DOS before starting agents: `sh tools/setup-emu2.sh` (docs/method.md, "Choosing the DOS"). In emu2 a match build is about half a second and the whole UW2 gate 8 seconds; in js-dos a build takes 3 to 7 seconds (about 30 during the UW2 project) and the gate over a minute. The agent limit above is about tokens and session limits, not the DOS.
- Sessions at once follow the DOS (one per core up to 12 natively, three in js-dos). Several full gates at once still share those cores; agents use `gate.py check --fast`.

## Budgets

- The queue gives each file a build budget (default 30); at zero it tells the agent to stop and report. Reset a file's budget (delete `<queue>/budget/FILE.C`) before a new attempt. Budgets are the only brake that held on an agent that loops.
- Claude agents get the same limit in the brief: about 20 builds per stubborn function, then report the best version.

## Keeping agents apart

- One file per agent; agents edit only their own source (and, for assembly, their own target table).
- No git commands from agents. Scratch files go in a subfolder of the shared scratch area named after the file.
- Never let two agents write the same file, symbols.tsv, matched.txt or a target table at once. Merge one file at a time yourself (skills/verify-and-merge).
- Shell scripts must not write fixed paths in /tmp: two agents running the same script overwrite each other's output.
- Python tools must not reassign sys.argv when they call each other (pass arguments to main), or an imported tool reads the wrong file.

## The readability pass

After the exact link and the modding build, skills/readability-pass makes the tree readable without changing a byte. Its orchestration differs from matching: steps 1 and 2 (the gate, shared headers) are one job over the whole tree; steps 3, 4 and 6 (constants, fields, comments) split by subsystem, one agent owning a subsystem's sources and its header, never two agents on one header. Agents run `tools/gate.py check --fast` on their own changes and you run the full gate before each commit; comment agents' edits go through `tools/comments.py apply` and you check the tree with `tools/comments.py check --ref HEAD`. The no-nested-agents rule above came from this pass.

## Firmware (armcc5): agents in parallel

On a firmware the unit of work is a range of source files (by address), and the state agents could
clobber is larger than a source and a table: the map, score and results files, best-source
directories, work directories under build/, and Wine prefixes. What worked on a 500-function
firmware matched by three agents at once:

- One git worktree per agent (`git worktree add ../fw-rangeB -b rangeB`), each with its own
  build/. Tools that keep results write them to files (a results table, a best source per
  function, merged files); two processes writing one such file each keep their own copy and the
  last to write wins, silently dropping the other's work. In a worktree an agent can run any tool;
  in a shared tree, never two writers of one results file at once.
- Give each agent a range of files, not of functions: a file is proved whole, and file boundaries
  (compiler.md, "files built without --split_sections") move as agents learn. A map change outside
  an agent's range goes in its report, for you to make.
- One Wine prefix per agent, and one per parallel job within an agent: a prefix's wineserver
  serialises its clients and now and then hangs. Make one template prefix, warm it once (`wine cmd
  /c exit` with no pipes: linker.md, the first-call hang), stop its server, and clone it for each
  agent. On APFS `cp -c -R template agentN` clones copy-on-write, so a clone costs almost no disk
  where a fresh prefix is about 300 MB and a boot. Never point two agents at one prefix, and never
  at a prefix a running gate uses.
- Agents run the fast gate on their own changes; you run the full gate (no cache) on the merge
  result before every merge into the main branch, and again after any change of the compiler
  home. A file that passes in an agent's worktree can fail in the merged tree: another agent's map
  change, a renamed symbol, a shared header.
- Merge tool changes by hand. Agents improve shared tools in their worktrees (a scoring fix, a
  translator rule); review each tool diff and apply it yourself rather than merging branches
  wholesale, since two agents' changes to one tool conflict in ways git does not see, and a change
  to a scorer changes every recorded score (rescore after it).
- A release finding (one file only matches under a later MDK) changes the compile home for
  everyone: switch it in the main tree, run the full gate with every proved file, then tell the
  agents.

## Long searches: budgets, resources and ownership

A search for the last hard functions can run for hours with compilers, observers, models and
solvers in parallel, beside other people's work on the same machine. What kept such runs honest
and safe on one firmware:

- Give each run a fixed deadline and fixed quotas (sources, compiler calls, retained evidence),
  with a reserve at the end for audits and cleanup: stop admitting new work while the reserve
  is still there. Never extend a finished run's deadline or reuse its records; a new run gets a
  new directory.
- One budget for workers across every stage. Leave at least two cores free and count the
  compile, observe, model and solver workers together. More workers stop paying early: on one
  sample of full compile-and-audit work, 1, 2 and 4 workers took 551, 310 and 250 seconds (four
  gave 2.2 times one). Under Wine that run kept at most two prefix slots at once.
- Disk. Prefixes add up: one cleanup of inactive generated prefixes recovered 53 GB. Keep a
  free-space floor, reserve space for each prefix slot before starting it, cap the evidence a
  run keeps, and remove the prefixes a run made when it ends.
- Ownership. Stop only what you can prove you started: record each process with its id, its
  start time and its group, and each prefix you create. Never kill Wine globally or stop the
  server of a prefix you did not make; a gate or another agent may be using it. A recorded
  process-group number can later belong to someone else's process, so check its start time
  before signalling it.
- Old experiment scripts often do their work when imported. Never import one as a library;
  copy the helper you need into a new module. A model library that keeps its state in globals
  holds one graph per process: run each model in its own process.
- Rebuild the controls first: before a batch, check that the current best source and a known
  control still give their recorded scores under this run's tools and options.

## A C2000 image (ti-c2000)

- Hand out units by original file, not by address range alone: cl2000's code depends on the whole
  file (earlier callees, the file's own variables and their `.ebss` order), so a function can only
  be finished inside the file it came from. The `.cinit` records show where one file's variables
  end and the next one's begin.
- Give each agent profiles/ti-c2000/match-brief.md. Compiles run under Wine; every tool run
  takes a cloned prefix of its own (`[c2000] jobs` of them), so agents can compile side by side.
- A rename in the map or a header can move another file's `.ebss`. After merging renames, run
  `profiles/ti-c2000/tools/c28ebss.py --all` and the gate before handing out more work.
- Keep a function nothing matches for the compiler scan (`match.py --all-cgt`) and the batch
  search (c28batch.py); on one firmware the last hard functions were settled by the compiler
  version and by which object an access names, not by more hand edits.

## Reviewing a report

- Check its claims: rerun match and verify yourself through merge.py. On firmware, a score of 0 is
  not a proof (the score masks pool words and call targets) and "every byte equal" from a masked
  compare is not either: the proof is match.py's WHOLE FILE MATCHES, or objplace.py for a file the
  Lite link cannot take, which applies every relocation instead of masking it. Rerun it yourself.
- New compiler behaviour goes into the profile's compiler.md or assembler.md only when tested.
- Renames go into the tables (`tools/syncnames.py src/FILE.C` renames rows to the publics the file defines) and then symbols.tsv is rebuilt from scratch.
