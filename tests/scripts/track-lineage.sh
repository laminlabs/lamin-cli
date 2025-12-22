#!/bin/bash
# Test script to verify lineage tracking with lamin-cli commands

set -e  # Exit on error

# Create a test input file
TEST_DIR=$(mktemp -d)
trap "rm -rf $TEST_DIR" EXIT

INPUT_FILE="$TEST_DIR/test_input.txt"
OUTPUT_FILE="$TEST_DIR/test_output.txt"

echo "test input data" > "$INPUT_FILE"

# Save the input file as an artifact (this will be our input)
lamin save "$INPUT_FILE" --key test/input.txt

# Start tracking a run
# When run non-interactively, track will use the script filename as the key
lamin track

# Load the input artifact (this should track it as a run input)
lamin load --key test/input.txt

# Create and save an output file (this should track it as a run output)
echo "test output data" > "$OUTPUT_FILE"
lamin save "$OUTPUT_FILE" --key test/output.txt

# Finish the run
lamin finish
