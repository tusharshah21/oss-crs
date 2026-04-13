#!/bin/bash
set -euo pipefail

PATCH_FILE=${1:-/patch/patch.diff}
RESPONSE_DIR=${2:-/response}

# Use git.real if available — the replay build system replaces /usr/bin/git
# with a wrapper that stubs out 'apply'.
if [ -x /usr/bin/git.real ]; then
    GIT=/usr/bin/git.real
else
    GIT=/usr/bin/git
fi

$GIT config --global --add safe.directory '*' >/dev/null 2>&1 || true

# 1. Apply patch
if [ ! -f "$PATCH_FILE" ]; then
    echo "ERROR: Patch file not found: $PATCH_FILE" >&2
    echo "1" > "$RESPONSE_DIR/build_exit_code"
    echo "Patch file not found: $PATCH_FILE" > "$RESPONSE_DIR/build.log"
    exit 1
fi

if [ -s "$PATCH_FILE" ]; then
    echo "Applying patch: $PATCH_FILE ($(wc -c < "$PATCH_FILE") bytes)" >&2
    # Cascade: strict → lenient context → GNU patch binary.
    # Mirrors CRSBench builder/infrastructure.py _apply_single_patch logic.
    #   1. git apply --whitespace=nowarn (strict context, ignore trailing ws)
    #   2. git apply --whitespace=nowarn -C1 (1-line context for fuzz)
    #   3. patch -p1 --fuzz=3 (GNU patch, very lenient)
    # This handles RTS-instrumented pom.xml diffs, trailing whitespace in
    # ground-truth patches, and minor context shifts from prior patch steps.
    PATCH_APPLIED=false
    if $GIT apply --whitespace=nowarn --verbose "$PATCH_FILE" 2>"$RESPONSE_DIR/patch_error.log"; then
        PATCH_APPLIED=true
    else
        echo "WARN: strict git apply failed, retrying with -C1:" >&2
        cat "$RESPONSE_DIR/patch_error.log" >&2
        if $GIT apply --whitespace=nowarn --verbose -C1 "$PATCH_FILE" 2>"$RESPONSE_DIR/patch_error.log"; then
            PATCH_APPLIED=true
        else
            echo "WARN: git apply -C1 also failed, trying patch binary:" >&2
            cat "$RESPONSE_DIR/patch_error.log" >&2
            if patch -p1 --fuzz=3 -i "$PATCH_FILE" 2>"$RESPONSE_DIR/patch_error.log"; then
                PATCH_APPLIED=true
            fi
        fi
    fi
    if [ "$PATCH_APPLIED" = false ]; then
        echo "ERROR: all patch methods failed:" >&2
        cat "$RESPONSE_DIR/patch_error.log" >&2
        echo "1" > "$RESPONSE_DIR/build_exit_code"
        cat "$RESPONSE_DIR/patch_error.log" > "$RESPONSE_DIR/build.log"
        exit 1
    fi
    echo "Patch applied successfully" >&2
    # Copy patch for test.sh RTS mode which reads from /tmp/patch.diff
    # TODO: make this path configurable via env var instead of hardcoded
    cp "$PATCH_FILE" /tmp/patch.diff 2>/dev/null || true
else
    echo "Patch file is empty, skipping git apply" >&2
fi

# 2. Build
# OSS_CRS_BUILD_CMD is extracted from the builder image's CMD by the sidecar.
BUILD_CMD="${OSS_CRS_BUILD_CMD:-compile}"
set +e
$BUILD_CMD 2>&1 | tee "$RESPONSE_DIR/build.log"
BUILD_EXIT=${PIPESTATUS[0]}
set -e

echo "$BUILD_EXIT" > "$RESPONSE_DIR/build_exit_code"

exit "$BUILD_EXIT"
