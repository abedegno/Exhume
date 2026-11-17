# Reuse integration verification

Integration base: Exhume `c6f7db598c73751f57036883df76b1a1ad50fd58`.
Checked 2026-11-17, with independently authored synthetic fixtures and separately installed native tools. No Duke target bytes, SDK sources or compiler binaries were added.

| Check | Result |
| --- | --- |
| Watcom discovery, normal Python | 159 tests, passing; six unconfigured native tests skipped |
| Same suite with `python -O` | 159 tests, passing; same six skips |
| Existing ARM/TI config and dispatch plus TASM setup | 22 tests, passing |
| Configured native tool smoke tests | Six passing: Watcom GA/10.5, WASM, TASM 3.1, MASM 5.10, DOSBox-X timeout |
| Repository syntax, local Markdown links, forbidden payloads | Passing |
| Git whitespace checks | Passing |

The native tests compile original small C/assembly fixtures, exercise explicit include trees and reject failed output/status. They do not rerun the external Duke recovery. Source, configuration and output mutation controls cover wrong callees, malformed contracts, changed config after parsing, extra headers, output changes after exact comparison, failed retries, multi-module output changes and receipt/input overlap. Required validation remains effective under optimized Python.

A separate reviewer examined shared compatibility, format semantics, snapshot lifetimes, licensing boundaries, documentation and skills. Two concrete findings were resolved with regressions: failed retries retaining canonical success receipts, and batch-build reporting dropping earlier modules' input/output pins. No remaining merge blockers were reported. Recovery/release skill scenarios cover historical-source search, missing library providers, zero source credit for supplied CRT, and standing user authorization.

The portable gate establishes declared-module proofs and literal whole-file equality separately. Complete source coverage, native provider/library selection, full compiler installation and default-header identities, link-driver dependencies and supplied-component attribution remain project responsibilities. LE is supported within the documented subset; LX is not. Explicit `--report` files are historical snapshots rather than canonical latest-attempt status.

Reproduce the hosted checks:

```sh
python3 -m unittest discover -s profiles/watcom/tests -t .
python3 -O -m unittest discover -s profiles/watcom/tests -t .
python3 -m unittest profiles.armcc5.tests.test_config profiles.armcc5.tests.test_dispatch profiles.ti-c2000.tests.test_config profiles.ti-c2000.tests.test_dispatch profiles.tasm31.test_setup
python3 tools/repocheck.py
git diff --check
```

See the [full inventory audit](duke3d-reuse.md) and [profile guide](../../profiles/watcom/README.md).
