#!/bin/sh
# Ask the build queue (tools/buildd.py, running outside the sandbox) to run match.py or
# verify.py, wait, and print the result. Needs only python3 to find the queue.
#   usage: tools/remote.sh match|verify src/FILE.C [--dis NAME] [--no-build]
here=$(cd "$(dirname "$0")" && pwd)
q=$(python3 "$here/config.py" queue) || exit 1
id=$$-$(date +%s)
mkdir -p "$q"; echo "$*" > "$q/$id.req.tmp"; mv "$q/$id.req.tmp" "$q/$id.req"
while [ ! -f "$q/$id.out" ]; do sleep 2; done
cat "$q/$id.out"; rm -f "$q/$id.out"
