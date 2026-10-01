# Example: Ultima Underworld II

UW2 is Exhume's first case study (docs/case-study-uw2.md). The decompilation itself lives in the UW2Decomp repository; this directory holds what Exhume needs to work on it.

- `exhume.toml`: UW2's configuration. Every UW2-specific constant the generic tools need is here: the EXE, DGROUP's paragraph, how overlays are named, the IDA listing and its paragraph bias, the toolchain directories, the FM Towns sibling build. By default it works in place on a UW2Decomp checkout at `~/UW2Decomp`; `EXHUME_BUILD`, `EXHUME_SYMBOLS`, `EXHUME_MATCHED` and `EXHUME_MAP` send outputs elsewhere.
- `extract.py` and `link.py`: the link stage, a reference implementation. They are UW2Decomp's tools with their paths taken from exhume.toml and nothing else changed, because most of what they do is measured from UW2.EXE (docs/link.md separates the rules from the facts). `python3 examples/uw2/link.py` builds the far data source if needed, generates the data-only modules from your EXE, links with TLINK in headless DOS and runs exediff.
- `prove.sh`: runs Exhume's tools on the UW2Decomp checkout without writing to it, and compares every result with UW2Decomp's own tools: all objects, three fresh matches (an overlay, a resident file, an assembly module), verify on every source, symbols.tsv rebuilt from scratch, the link, and with `--map` the map pipeline.

Requirements beyond Exhume's: a UW2Decomp checkout (with `build/` populated by its own tools, for the comparisons), your own `UW2.EXE` (`UW2_EXE`, default `~/UWGOG/UW2/UW2.EXE`), the IDA listing `uw2_asm.asm` from UWReverseEngineering (`UW2_ASM`), and the Turbo C++ 1.01 and TASM 2.0 directories (`EXHUME_TC`, `EXHUME_TASM`, default UW2Decomp's `TC` and `TASM`).

To boot the linked game and take screenshots:

```sh
node tools/rungame.mjs --data ~/UWGOG/UW2 --as UW2.EXE --skip SAVE0 build/uw2/LINK/out/UW2.EXE shot- \
  w:12000 k:Escape w:3000 k:Escape w:3000 k:Escape w:3000 s:menu k:Enter w:3000 \
  k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:2000 s:chargen
```

Three Escapes pass the title and introduction to the main menu, where Create Character is the default; the Enters accept each character creation step and stop at the name prompt, where the attributes (`Str:`, drawn by OVR101.C) are on screen.
