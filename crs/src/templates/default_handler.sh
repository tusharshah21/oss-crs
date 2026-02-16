#!/bin/bash
set -euxo pipefail

REQUEST_DIR=${1:-/request}
RESPONSE_DIR=${2:-/response}

# 1. Apply patch — find the git repo under $SRC
if [ -f "$REQUEST_DIR/patch.diff" ] && [ -s "$REQUEST_DIR/patch.diff" ]; then
    REPO_DIR=""
    for dir in "$SRC"/*/; do
        if [ -d "$dir/.git" ]; then
            REPO_DIR="$dir"
            break
        fi
    done
    if [ -z "$REPO_DIR" ]; then
        echo "ERROR: No git repository found under $SRC" >&2
        exit 1
    fi
    cd "$REPO_DIR"
    git apply "$REQUEST_DIR/patch.diff"
fi

# 2. Compile (disable exit-on-error so we capture the exit code)
set +e
compile 2>&1 | tee "$RESPONSE_DIR/build.log"
BUILD_EXIT=${PIPESTATUS[0]}
set -e

echo "$BUILD_EXIT" > "$RESPONSE_DIR/build_exit_code"

# 3. Copy build output
mkdir -p "$RESPONSE_DIR/out/"
cp -r /out/* "$RESPONSE_DIR/out/" 2>/dev/null || true

exit "$BUILD_EXIT"
