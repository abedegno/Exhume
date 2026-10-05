# CI with private data on free public runners

A decompilation's gate needs the original program and the original toolchain, and a port's accuracy suite needs the game's data; none of them may be published. Exhume's first answer was to run the gate only in a git pre-push hook and give hosted CI only `tools/repocheck.py` (docs/readability.md). This page is the second: run the whole gate and the port's tests on GitHub's free hosted runners, in a public repository, from an encrypted bundle that only the repository's own workflows can open. UW2Decomp has run it this way since its CI commit (`6252fdf`), and skills/ci-bundle is the working order.

## The pattern

- **A private storage repository** (UW2: `abedegno/uw2-ci-assets`) holds one file, the bundle: an [age](https://age-encryption.org)-encrypted tar.gz of `game/` (the owner's own copy of the program), `tc/` and `tasm/` (the toolchain's disk images), `MANIFEST.txt` and `SHA256SUMS`. Its Actions are disabled, so nothing in it ever runs.
- **A read-only deploy key** of that repository, its private half a secret of the public repository (`UW2_ASSETS_DEPLOY_KEY`). It can read that one repository and nothing else.
- **The age secret key**, a second secret (`UW2_ASSETS_AGE_KEY`), and kept by the owner in the macOS Keychain (`security find-generic-password -s uw2-ci-assets-age -w` prints it; piped to `age-keygen -y`, its recipient). Without it the bundle is noise, so a leaked deploy key alone exposes nothing.
- **A local composite action** (`.github/actions/<name>-assets`) writes the deploy key to `$RUNNER_TEMP` with mode 600, clones the storage repository over SSH trusting only GitHub's published host key, deletes the key, and runs `tools/ci-assets.sh`, which decrypts with the age key on age's standard input (never a file), checks every file against SHA256SUMS, prints only how many passed, and sets the variables the tools read (UW2: `UW2_EXE`, `UW2_DIR`, `TC_DISKS`, `TASM_DISKS`).
- **The tools that need no data** come from a second action (`linux-tools`): the Ubuntu packages, Python with `tools/requirements.txt`, Node without dos-mcp's Chrome (CI uses emu2 and DOSBox-X, never js-dos), and emu2, SDL3, libmt32emu and Nuked OPL3 built by their setup scripts, each cached by the script that pins it.

## The workflows

`tools/citemplates.py` writes them from `tools/templates/ci/`, with a project's values from the file exhume.toml's `[ci] vars` names; with examples/uw2/ci.toml they are UW2Decomp's, byte for byte, and both UW2's and the defaults' pass actionlint (examples/uw2/prove-port.sh, `ci`).

| Workflow | When | What it runs | Needs the bundle |
| --- | --- | --- | --- |
| `port.yml` | every push and pull request, forks included | the port's build and compile-only measurement on Ubuntu, macOS and Windows (MSYS2 CLANG64); nothing is run | no |
| `repocheck.yml` | every push and pull request, forks included | `tools/repocheck.py` | no |
| `accuracy.yml` | pushes to `main`, pull requests from branches of this repository, and by hand | the toolchain unpacked, then the fast tier: the gate, the port, the quick fuzzing, every session against its golden | yes |
| `nightly.yml` | a schedule, and by hand | the full tier, with DOSBox-X from Ubuntu making every golden again; fails if a regenerated golden differs from the committed one, and puts the step times and coverage totals in the job summary | yes |

| `release.yml` | a tag `v*`, and by hand | the players' packages for macOS, Linux and Windows (docs/port.md, "Packages for players"); for a tag, a draft release with them and the LGPL libraries' source | no (Apple's secrets, when set, sign and notarise the macOS app) |

A pull request from a fork gets `port.yml` and `repocheck.yml` only: GitHub gives a fork's pull request no secrets, and `accuracy.yml` skips itself for one rather than fail.

A project whose bundle is not made yet can keep `accuracy.yml` and `nightly.yml` and have them skipped rather than fail: `job_if`, the condition of accuracy.yml's jobs, and `nightly_if`, a line giving nightly.yml's job one, can require a repository variable (UW1Decomp's: `vars.UW1_CI_ASSETS == 'true'`), which the owner sets once the bundle and its two secrets are in place.

## The safeguards

- **Nothing decrypted is cached**, nor anything built from it. The caches hold only emu2, the built libraries, Nuked OPL3, and pip's and npm's downloads, each keyed on the script or file that pins it.
- **Artifacts are pictures and text only**: the difference pictures of a session that differs from its golden, the port's log of each replay, the run's own output, and at night the changed golden screens and the list of changed golden files. Never a state dump, a saved game, an object, an EXE or anything else under `build/`.
- **Nothing lists or prints the decrypted tree.** ci-assets.sh reports a count; the tools print only the path of the EXE.
- **The last step of every job deletes it**, whatever happened before: the decrypted tree, the clone, the key file, the unpacked toolchain and `build/` (`if: always()`). The runner is discarded after the job anyway.
- **No `pull_request_target`.** The jobs with secrets run on pushes to `main`, on the schedule, by hand, and on pull requests whose branch is in the repository itself, whose authors can already push there. Fork pull requests get no data.
- **Least privilege.** `permissions: contents: read` in every workflow; `persist-credentials: false` on every checkout of the project; the deploy key reads one repository; the bundle is useless without the age key.

## Making the bundle

Put `game/`, `tc/` (`Disk01.img` to `Disk04.img`), `tasm/` (`Disk01.img`) and a `MANIFEST.txt` in an empty directory, and in it:

```sh
find game tc tasm MANIFEST.txt -type f | sort | xargs shasum -a 256 > SHA256SUMS
COPYFILE_DISABLE=1 tar --no-xattrs -czf - game tc tasm MANIFEST.txt SHA256SUMS \
  | age -r "$(security find-generic-password -s PROJECT-ci-assets-age -w | age-keygen -y)" -o PROJECT-ci-assets.tar.gz.age
```

Commit that one file to the storage repository. `COPYFILE_DISABLE` and `--no-xattrs` keep macOS's extended attributes out; GNU tar on the runner would warn about them otherwise (ci-assets.sh silences that warning too).

## Rotation

- **A new bundle** (new game files, a new toolchain image): make it as above and commit it in place of the old one.
- **A new age key**: `age-keygen -o new.key`; store its `AGE-SECRET-KEY-1...` line with `security add-generic-password -U -s PROJECT-ci-assets-age -a "$USER" -w "$(grep AGE-SECRET-KEY new.key)"`; make the bundle again with the new recipient; set the secret with `security find-generic-password -s PROJECT-ci-assets-age -w | gh secret set PROJECT_ASSETS_AGE_KEY -R OWNER/PROJECT`; delete `new.key`. Until the new bundle is pushed, the jobs that need it fail.
- **A new deploy key**: `ssh-keygen -t ed25519 -N '' -C 'PROJECT CI (read-only)' -f deploy`; `gh repo deploy-key add deploy.pub -R OWNER/PROJECT-ci-assets -t 'PROJECT CI (read-only)'` (read-only unless `-w` is given); `gh secret set PROJECT_ASSETS_DEPLOY_KEY -R OWNER/PROJECT < deploy`; remove the old key (`gh repo deploy-key list`, `gh repo deploy-key delete ID`); delete `deploy` and `deploy.pub`.

## Linux on the runner

Three things differ from macOS, and the tools allow for each: the file system is case-sensitive, and emu2 creates a file under the case the DOS program gave, so tools/dosbackend.mjs finds a DOS run's outputs without regard to case; the toolchain setup must take everything the link needs from the disk images (Turbo C++'s `OVERLAY.LIB` is in `XLIB.ZIP`, which profiles/borland-tc101/setup-tc.sh extracts); and a sibling build's symbol table the gate reads for original names must be committed or the gate says it cannot check that mark. On UW2 the fast tier passes on Ubuntu 24.04 on x86-64 and on arm64, and so does every step of the full tier but the deep fuzzing, which was not tried there; DOSBox-X 2024.03.01 from Ubuntu makes goldens identical to the committed ones (made in DOSBox-X 2026.10.01 on macOS and in js-dos).
