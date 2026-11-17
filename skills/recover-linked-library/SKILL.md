---
name: recover-linked-library
description: Use when an executable contains linked library or SDK contributions, when masked matches leave provider identity uncertain, or when a private or duplicate symbol complicates attribution.
---

# Recover a linked library

Discover historical source before decompiling. Library recovery is a provenance and linker-selection problem as well as a compiler problem.

1. Inventory the original archive/container, selected member names and offsets, hashes, exported/private symbols, complete contribution extents, compiler signatures and known sibling releases. Preserve originals as local reference evidence; never use their object bytes as recovered source output.
2. Search historical GitHub tags/commits and preservation/source-release repositories, then primary vendor SDK/source archives and released sibling games. Follow historical makefiles and headers, not only current APIs. Pin the repository commit or archive/member hash, original notices, version selectors and modifications. Public downloadability grants no additional redistribution rights. Record unsuccessful searches to avoid repeating them.
3. Classify each candidate: unchanged historical source, source-assisted derivative, independently reconstructed source, reference-only object, supplied compiler runtime or restricted vendor source. Put unresolved/restricted families and related derived metadata in an explicit local-dependency manifest. Do not relabel them MIT/GPL because another component uses that license. Separate actual vendor restrictions from cautious recovery-authored notes, and read original release grants before declaring rights unresolved or restricted.
4. Compile the narrowest complete member with measured compiler/assembler versions, ABI, headers and switches. Vary one hypothesis with a positive and negative control under a fixed compiler-call/time budget. A runtime-library version can differ from the application's compiler version.
5. Prove all code/data/BSS extents and typed fixups. Resolve actual provider identity and archive/member selection, including duplicate PUBLICs, private namespaces, segment-relative frames and native first-wins behavior. A masked match or a callee guessed from the consumer is insufficient. Preserve source/public/private ordering that native linking observes.
6. Freeze RAW producer bytes and hashes before parsing/copying/publication; verify those same bytes throughout the chain. Reject stale, mutated or partial outputs. Prove normal and optimized-Python validation when adding mandatory checks. Use [verify-and-merge](../verify-and-merge/SKILL.md), then [link-and-diff](../link-and-diff/SKILL.md) for contribution versus semantic, serialized and full-file gates.

Deliver a portable provenance record: source revisions/hashes, notices and distribution boundary, compiler/options/header identities, actual selected provider map, controls, complete proof counts, unresolved historical names and attained gate level. Keep machine-local homes in ignored local configuration. Source assistance and byte equality do not prove unique original spelling.

The [Duke audit](../../docs/audits/duke3d-reuse.md) demonstrates historical Apogee/Build source reuse, mixed Watcom releases and separately supplied vendor/runtime components; it supplies no game or SDK source to Exhume.
