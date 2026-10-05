#!/bin/sh
# Install a git pre-push hook in a decompilation project: `git push` runs the gate first and
# stops when it fails. The gate needs the toolchain and the original program, which hosted CI
# cannot have, so on a public repository this hook is the gate (CI runs tools/repocheck.py).
#
#   sh tools/install-hooks.sh PROJECT_DIR ["COMMAND"]
#
# COMMAND is what the hook runs in PROJECT_DIR (default `make check`, for a project with the
# Makefile from tools/templates/Makefile). In a repository of several projects (a game per
# folder), each keeps its own entry in the one hook, so only the projects installed are run.
# The hook checks the working tree, not the commits being pushed, so commit or stash first.
# Bypass for one push (a docs-only change, say):  git push --no-verify   or   SKIP_CHECK=1 git push
set -e
[ -n "$1" ] || { echo "usage: sh install-hooks.sh PROJECT_DIR [COMMAND]"; exit 2; }
dir=$(cd "$1" && pwd)
root=$(git -C "$dir" rev-parse --show-toplevel)
# the project's folder relative to the repository: "." for a repository that is one project, or a
# game's folder in a repository of several, each of which keeps its own entry in the one hook
rel=$(cd "$dir" && git rev-parse --show-prefix); rel=${rel%/}; [ -n "$rel" ] || rel=.
cmd=${2:-make check}
hook=$(git -C "$root" rev-parse --git-path hooks)/pre-push
case "$hook" in /*) ;; *) hook="$root/$hook" ;; esac
mkdir -p "$(dirname "$hook")"
if [ -f "$hook" ] && ! grep -q 'exhume pre-push' "$hook"; then
  echo "$hook exists and is not Exhume's; leaving it alone"; exit 1
fi
# the projects already in the hook, less this one, then this one
entries=$( [ -f "$hook" ] && grep "^run '" "$hook" | grep -v "^run '$rel' " || true)
cat > "$hook" <<'EOF'
#!/bin/sh
# exhume pre-push: run each project's gate in its folder before anything leaves this machine.
# Bypass: git push --no-verify, or SKIP_CHECK=1 git push
[ -n "$SKIP_CHECK" ] && { echo "pre-push: SKIP_CHECK set, not running the gate"; exit 0; }
top=$(git rev-parse --show-toplevel) || exit 1
run() {
  echo "pre-push: $1: $2 (bypass with git push --no-verify)"
  ( cd "$top/$1" && sh -c "$2" ) || { echo "pre-push: $1: the gate failed, push stopped"; exit 1; }
}
EOF
[ -n "$entries" ] && printf '%s\n' "$entries" >> "$hook"
printf "run '%s' '%s'\n" "$rel" "$cmd" >> "$hook"
chmod +x "$hook"
echo "installed $hook: $rel runs $cmd"
