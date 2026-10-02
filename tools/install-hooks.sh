#!/bin/sh
# Install a git pre-push hook in a decompilation project: `git push` runs the gate first and
# stops when it fails. The gate needs the toolchain and the original program, which hosted CI
# cannot have, so on a public repository this hook is the gate (CI runs tools/repocheck.py).
#
#   sh tools/install-hooks.sh PROJECT_DIR ["COMMAND"]
#
# COMMAND is what the hook runs in the project's root (default `make check`, for a project
# with the Makefile from tools/templates/Makefile). The hook checks the working tree, not the
# commits being pushed, so commit or stash first.
# Bypass for one push (a docs-only change, say):  git push --no-verify   or   SKIP_CHECK=1 git push
set -e
[ -n "$1" ] || { echo "usage: sh install-hooks.sh PROJECT_DIR [COMMAND]"; exit 2; }
root=$(cd "$1" && git rev-parse --show-toplevel)
cmd=${2:-make check}
hook=$(git -C "$root" rev-parse --git-path hooks)/pre-push
case "$hook" in /*) ;; *) hook="$root/$hook" ;; esac
mkdir -p "$(dirname "$hook")"
if [ -f "$hook" ] && ! grep -q 'exhume pre-push' "$hook"; then
  echo "$hook exists and is not Exhume's; leaving it alone"; exit 1
fi
cat > "$hook" <<EOF
#!/bin/sh
# exhume pre-push: run the full gate before anything leaves this machine.
# Bypass: git push --no-verify, or SKIP_CHECK=1 git push
[ -n "\$SKIP_CHECK" ] && { echo "pre-push: SKIP_CHECK set, not running the gate"; exit 0; }
cd "\$(git rev-parse --show-toplevel)" || exit 1
echo "pre-push: $cmd (bypass with git push --no-verify)"
$cmd || { echo "pre-push: the gate failed, push stopped"; exit 1; }
EOF
chmod +x "$hook"
echo "installed $hook, running: $cmd"
