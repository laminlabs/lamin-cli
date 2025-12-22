# prepare test data
TEST_DIR=$(mktemp -d)
trap "rm -rf $TEST_DIR" EXIT
INPUT_FILE="$TEST_DIR/test_input.txt"
OUTPUT_FILE="$TEST_DIR/test_output.txt"
echo "test input data" > "$INPUT_FILE"
lamin save "$INPUT_FILE" --key test/input.txt

# actual script
set -e  # exit on error
lamin track
lamin load --key test/input.txt
echo "test output data" > "$OUTPUT_FILE"
lamin save "$OUTPUT_FILE" --key test/output.txt
lamin finish
