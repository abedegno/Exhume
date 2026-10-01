---
name: orchestrate
description: Use when running a whole byte-matching decompilation with several AI agents - choosing models, how many agents at once, per-file build budgets, Codex drafts with Claude finishers, the build queue for sandboxed agents, and keeping agents from clobbering each other; with the costs measured on UW2.
---

# Orchestrate matching agents

Paths are relative to the Exhume checkout. You (the orchestrator) prepare target tables, hand one file to each agent, review reports, and merge. Agents never merge, commit, or edit shared files.

## Pool

- Run about three agents at once. Seven parallel Opus agents exhausted a session's usage limit in a few hours on UW2 and all seven died mid-file. Resume an interrupted agent with SendMessage (it keeps its context, which is cheaper than restarting); do not relaunch it.
- Use the strongest Claude model as the matcher and finisher. On UW2, Opus averaged about 4 KB of matched code per 170k tokens. Sonnet matched both trial files but used about five times the tokens per byte (seg014, 579 bytes: 185k tokens; seg043, 1.6 KB: 434k tokens, 53 minutes, 218 tool calls).
- Give a big file to one agent, which may fork helpers for single functions: UW2's largest file, ovr110 (20.6 KB, 68 functions), was finished by one Claude agent and seven forks after a Codex draft.
- Give each agent the brief in skills/match-file (C) or skills/match-asm (assembly), plus: its source file name, its segment and table, and a one-line description of what the file does.

## Codex

- Codex's sandbox cannot start the emulator's headless browser. Run the queue outside it: `python3 tools/buildd.py --budget 30` (backgrounded), and tell Codex to build with `tools/remote.sh match src/FILE.C` and `tools/remote.sh verify src/FILE.C`. The queue accepts only those two commands on files in src/.
- Measured on UW2, in input tokens (mostly cached): gpt-6-sol at medium effort, alone, about 1.3M per file of around 1 KB (ovr117: five builds). A cheap draft (gpt-6-luna) finished by sol at medium: 0.9M to 2.0M, average 1.46M over four files, counting luna at 1/20 since sol costs 20 times luna per token. sol at low effort alone: 1.8M to 2.95M. luna alone never finished a file: it quit after one to four builds, or ran away to 10M tokens ignoring caps.
- Codex stalls on files over about 2 KB and leaves stubs. The pattern that worked: Codex drafts and matches the small functions, a Claude agent finishes the rest (ovr095, ovr097, ovr103, ovr110, ovr166 and others in UW2's history).
- Codex drafts hide wrong extern names behind masked fixups. The finisher must run verify and use symbols.tsv names; ovr103's draft had about 20 wrong names.
- Tell Codex explicitly to keep going until WHOLE SEGMENT MATCHES or the budget runs out, and to end with the brief's report including the build count.

## Budgets

- The queue gives each file a build budget (default 30); at zero it tells the agent to stop and report. Reset a file's budget (delete `<queue>/budget/FILE.C`) before a new attempt. Budgets are the only brake that held on an agent that loops.
- Claude agents get the same limit in the brief: about 20 builds per stubborn function, then report the best version.

## Keeping agents apart

- One file per agent; agents edit only their own source (and, for assembly, their own target table).
- No git commands from agents. Scratch files go in a subfolder of the shared scratch area named after the file.
- Never let two agents write the same file, symbols.tsv, matched.txt or a target table at once. Merge one file at a time yourself (skills/verify-and-merge).
- Shell scripts must not write fixed paths in /tmp: two agents running the same script overwrite each other's output.
- Python tools must not reassign sys.argv when they call each other (pass arguments to main), or an imported tool reads the wrong file.

## Reviewing a report

- Check its claims: rerun match and verify yourself through merge.py.
- New compiler behaviour goes into the profile's compiler.md or assembler.md only when tested.
- Renames go into the tables (`tools/syncnames.py src/FILE.C` renames rows to the publics the file defines) and then symbols.tsv is rebuilt from scratch.
