---
name: ci-bundle
description: Use when a decompilation or port whose tests need private data (the original program, game data, a commercial toolchain) should run its full gate and accuracy tests on free public GitHub runners - the encrypted bundle in a private repository, the deploy key and age key, the workflow templates, the safeguards that keep the data private, and key rotation.
---

# CI with a private bundle

Paths are relative to the Exhume checkout. docs/ci-bundle.md is the pattern and the reasons; tools/templates/ci are the workflows; examples/uw2/ci.toml is UW2Decomp's instantiation, which reproduces its own CI byte for byte.

## Steps

1. **The bundle.** In an empty directory: `game/` (the owner's own copy of the program), `tc/` and `tasm/` (the toolchain's disk images), `MANIFEST.txt` (what each is and where it came from). Make `SHA256SUMS`, tar and encrypt with a new age key (docs/ci-bundle.md, "Making the bundle"). Keep the age secret key in the macOS Keychain (`security add-generic-password -s PROJECT-ci-assets-age ...`), nowhere else on disk.
2. **The storage repository.** A private repository holding only the encrypted file. Disable its Actions. Add a read-only deploy key (`ssh-keygen -t ed25519`, `gh repo deploy-key add` without `-w`), and delete the local key files once the secret is set.
3. **The secrets.** In the public repository: the deploy key's private half and the age secret key (`gh secret set`). Nothing else needs a secret.
4. **The workflows.** Fill in a values file (start from `python3 tools/citemplates.py --list` and tools/templates/ci/defaults.toml) and point `[ci] vars` at it; `python3 tools/citemplates.py --config exhume.toml .` writes `.github/workflows/{accuracy,nightly,port,repocheck}.yml` and the two actions. Run `actionlint` on them.
5. **Prove it before trusting it.** Push to a branch of the repository itself and watch accuracy.yml run the fast tier; run nightly.yml by hand once. Read the artifacts list of a failing run: only pictures and logs.
6. **Rotate** on any suspicion, and when a collaborator leaves: a new deploy key, a new age key, a new bundle (docs/ci-bundle.md, "Rotation").

## Rules

- Never `pull_request_target`; a fork's pull request gets the data-free workflows only.
- Never cache anything decrypted or built from it; cache only the tools, keyed by the script that pins each.
- Upload only pictures and text logs, never dumps, saves, objects or EXEs.
- Never list or print the decrypted tree; ci-assets.sh prints a count.
- The last step of every job with the bundle deletes the decrypted tree, the clone, the key file, the toolchain and `build/`, under `if: always()`.
- `permissions: contents: read` everywhere; `persist-credentials: false` on checkouts.
- On Linux, find DOS outputs without regard to case, and unpack everything the link needs from the disk images (Turbo C++'s OVERLAY.LIB is in XLIB.ZIP).
