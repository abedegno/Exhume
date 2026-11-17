# Duke recovery reuse implementation plan

> For workers: use the executing-plans workflow; keep each ownership area separate.

**Goal:** Audit the generated Duke recovery material and merge independently reusable, licensed tooling and lessons into Exhume, with functioning Watcom and TASM support rather than an experimental symlink.

**Architecture:** Keep Watcom's OMF32/LE semantics in `profiles/watcom/tools`; shared Exhume commands dispatch through the profile. Profiles describe tested tools, while project manifests own module placements, flags, libraries, link commands and vendor prefixes. A TASM 3.1 assembler component can be selected per module. Generic verified snapshots and source/data attribution remain independent of game names and addresses.

**Scope / authorization:** The user requested this implementation after the concrete reuse recommendations. Preserve their dirty `the existing Exhume checkout` checkout. Integrate from current upstream in this isolated checkout; test and review before merging upstream. Do not copy restricted vendor source, game media, compiler binaries, project-specific byte payloads or the Duke source tree. The GNU/objconv fork is a separately licensed optional compiler pipeline; audit its reuse explicitly rather than silently relicensing it as MIT.

## Tasks

- [x] Formats and semantic proof: port bounded OMF32 and LE readers, whole module/composite verification, bindings and group/frame support; use synthetic fixtures, check normal and `-O`, and retain explicit unsupported-feature failures.
- [x] Native tools: reusable DOSBox-X Watcom/WASM runner, TASM 3.1 setup/profile, compiler revision/ABI notes and synthetic native integration tests. No bundled compilers.
- [x] Shared integration: separate function/data mangling, profile dispatch for build/match/verify/merge/gate, explicit module manifest, exact full-file comparison and source/module attribution. Preserve all existing profile behavior.
- [x] Snapshot/publication helper: validate and parse one RAW snapshot, retain expected hashes before copy/parse/bind, reject changed outputs, publish receipts atomically with cleanup. Add real mutation controls.
- [x] Audit and skills: classify the complete Duke tracked tree; document portable/project-specific/restricted/deferred material and why; update library discovery, exact linking, source proof, bounded orchestration, readable ASM/C distinctions and safe release packaging.
- [ ] Test, fresh independent review, resolve findings, push and merge the audited branch. Record exactly what is supported and what remains project-owned.

## Shared interfaces

Format worker exports `LEImage`, `LEError` from `le.py`; `Module`, `OMFError` from `omf32.py`; `compare_range`, `MatchError`, `validate_group_frames` from `matching.py`; `verify_module`, `ModuleError`, `infer_bindings`, `BindingError`, `verify_composite` from `placements.py`.

Runner exports `CompilerError`, `run_batch(home,work,batch,timeout)`, `compile_source(home,source,output,options,timeout=30,include_dirs=None,module_name=None)` and `assemble_source(home,source,output,options,timeout=30,source_name=None,producer='wasm')` from `runner.py`, retaining keyword-only options as appropriate. TASM has its own `profiles/tasm31/profile.toml` and setup tool.

Shared dispatcher uses `[link] object_model = 'omf32-le'`. Watcom projects declare `[watcom] modules = 'watcom-modules.json'`; that file maps source names to compiler/assembler options, complete OMF segment placements, independent symbol bindings and optional group frames. Final link/bind is project configured; the gate reports complete file identity separately from declared source module coverage and supplied dependencies.

## Review focus

Wrong callee identity despite masked opcode agreement; private symbol collisions; BSS mistaken for backed data; nested executable origin/page/fixup boundaries; partial/crossing fields and unsupported records; malformed manifests and path traversal; failed tools leaving stale objects; changed bytes between validation and receipt publication; accidental Borland behavior changes; source-only fixture licensing and target-specific assumptions leaking into generic code.
