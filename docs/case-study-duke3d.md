# Case study: Duke Nukem 3D v1.3D

Duke demonstrates a complete source-assisted reconstruction of a registered DOS executable: **1,179,131 bytes**, **zero differing bytes**, SHA-256 `a197b6b4097fa39987f4a843141830ff1553eb52b606b51fc67d067560de0cf4`. The evidence is the [curated release verification record](https://github.com/abedegno/duke3d-1.3d/blob/e8b2c6788674fa82f74213ccb9afd56ffc4ae3e1/verification/rebuild.json), its retained root672 observer and fresh release-tree proof. This documentation import does not claim to have rebuilt the game itself.

The recovery snapshot is `099a0400259bac00c18698b0b871af7df7e5f246`; the inspected public release is `e8b2c6788674fa82f74213ccb9afd56ffc4ae3e1`. [The detailed audit](audits/duke3d-reuse.md) and [per-path inventory](audits/duke3d-reuse.json) classify all 1,673 original tracked paths without importing game source, SDK source or binaries.

## Historical source before decompilation

The released v1.5 source and pinned historical reconstruction repositories supplied algorithms, headers, makefile switches and member names. The final record pins the released reference at `ef609159bd352985a0c63734a11241d86c873fe5`, audio reconstruction at `c363124d376d373441a21b3304eb44d32c0a69f7`, Build reconstruction at `88ffca1bdb8d93c731f6d02e62ae5119393bc73b`, and Duke reconstruction at `fdb214ceea671341aa03d54d09fb7fe47940d3b7`. These sources narrowed compiler/version questions before arbitrary rewriting. Watcom GA, 10.0a, 10.0b and 10.5 each mattered to different contributions.

Library recovery required actual provider identities, private/public scope and selected native members. A masked function candidate could reproduce a caller's shape while naming the wrong callee. A complete contribution proof checked code, data, BSS and typed fixups; native linking then established selection/order and serialized image behavior. These are successive proof levels, not interchangeable meanings of “match.”

## From complete contributions to a complete file

The exact route freshly compiled **121 owned source inputs**, linked **177 selected original compiler-library members and five original libraries**, invoked native WLINK and bound a pinned **217,764-byte DOS4GW prefix**. Supplied stock components and prefix earn no recovered-source credit. Production consumed no reference game payload or cached research objects. The independent observer compared the complete bound file without masks. The recovery main-workspace run and fresh curated-tree run each reached the same complete file hash.

Equal loaded bytes did not initially yield equal serialized relocation records. Source-owned CODE-span metadata and native FILE ordering addressed emission boundaries without patching executable bytes or finished objects. The optional GNU/binutils/objconv pipeline is separately licensed; [its audited implementation](https://github.com/abedegno/duke3d-1.3d/tree/e8b2c6788674fa82f74213ccb9afd56ffc4ae3e1/profiles/gnu_elf_omf_layouts) remains external to MIT core. Plans describe fresh source extents and field containment, not opcodes or target-byte substitutions.

The final emitting tree contains C and named assembly, plus C reading aids and navigation. GAME and ACTORS still document compiler-faithful C lowering limits. Exact bytes do not establish an all-C reconstruction or uniquely recovered original source. Ordinary unused declarations and dead stores may preserve historical compiler allocation or symbol order when controlled fresh builds prove the effect.

Three unidentified DATA groups remain explicit: **96 bytes + two GF1_OS bytes + seven post-NUL GAME bytes**. Their historical declaration/ownership/purpose is not invented, and preservation adds no historical-source credit. The old preparation-only/adoption flags record a policy checkpoint; the observer already measured exact identity, and the public release selects the exact route. Earlier zero-residue and ordinary-C experiments retain their negative history.

## A source release has its own proof

The public tree retains GPL notices, the separate Build license, Open Watcom notices and component provenance. A **477-path local-dependency manifest** keeps restricted FORTE/Gravis and Creative reconstructed sources, source-bearing evidence and related metadata outside public Git history. A local importer checks the exact project-specific overlay; an arbitrary SDK installation is not a substitute. Compiler/media assets, stock libraries, banks, the original/rebuilt executable and vendor prefix remain separately supplied. A fresh curated-checkout build proves the public packaging still supports the measured result.

The audit separately flags `src/mact/README.md` and three MACT assembly files as rights-unresolved. Their cautious recovery-authored notices are not proven vendor restrictions: the original GPL-released v1.5 bundle contains the identical MACT binary, GNU.TXT and GPL interface headers, but no corresponding ASM source. The JSON keeps that unresolved reconstruction-notice question distinct from the 477-path vendor overlay. No MACT source is imported.

For Exhume, the reusable result is a set of bounded format/runner/provenance mechanisms, compiler findings and workflow gates. [Recover-linked-library](../skills/recover-linked-library/SKILL.md) puts historical source discovery first; [package-source-release](../skills/package-source-release/SKILL.md) requires the curated source/rights boundary and fresh proof. Existing Borland extraction-based partial workflows retain their commands and purpose; their extracted spans remain supplied bytes until an owned-source gate replaces them.
