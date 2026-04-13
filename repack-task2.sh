#!/bin/bash
# repack-task2.sh — repackage the Termux taskwarrior .deb as task2
# Conflicts with taskwarrior (TW3) so only one can be installed at a time.
#
# Usage: bash repack-task2.sh <input.deb> [output.deb]
#   input.deb  : taskwarrior_2.6.2-3_aarch64.deb (original Termux package)
#   output.deb : task2_2.6.2-3_aarch64.deb (default)

set -e

INPUT="${1:?Usage: $0 <input.deb> [output.deb]}"
OUTPUT="${2:-task2_2.6.2-3_aarch64.deb}"
WORKDIR="$(mktemp -d)"

cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "Extracting $INPUT ..."
dpkg-deb -R "$INPUT" "$WORKDIR"

CONTROL="$WORKDIR/DEBIAN/control"

echo "Patching control file ..."
# Rename package, add Conflicts, remove Provides if present
sed -i \
    -e 's/^Package: taskwarrior$/Package: task2/' \
    -e 's/^Description: .*/Description: Taskwarrior 2.6.2 for Termux (packaged as task2 to coexist with TW3)/' \
    "$CONTROL"

# Add Conflicts line if not already present
grep -q '^Conflicts:' "$CONTROL" || echo 'Conflicts: taskwarrior' >> "$CONTROL"

echo "--- Resulting control file:"
cat "$CONTROL"
echo "---"

echo "Repacking as $OUTPUT ..."
dpkg-deb -b "$WORKDIR" "$OUTPUT"

echo "Done: $OUTPUT"
echo ""
echo "Transfer to phone and install:"
echo "  adb push $OUTPUT /sdcard/Download/"
echo "  # or: scp $OUTPUT phone:~/"
echo ""
echo "On Termux:"
echo "  pkg uninstall taskwarrior   # remove TW3 if installed"
echo "  dpkg -i ~/../usr/tmp/$OUTPUT  # adjust path as needed"
echo "  task --version"
