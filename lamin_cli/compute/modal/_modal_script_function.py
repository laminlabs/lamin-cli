# Execute a script remote function
def run_script_from_path(script_remote_path: str):
    import subprocess
    import sys
    import threading
    from pathlib import Path

    result = {"success": False, "output": "", "error": ""}

    def stream_output(stream, capture_list):
        """Read from stream line by line and print in real-time while also capturing to a list."""
        for line in iter(stream.readline, ""):
            print(line, end="")  # Print in real-time
            capture_list.append(line)
        stream.close()

    try:
        # Check if the file exists
        if not Path(script_remote_path).exists():
            raise FileNotFoundError(f"Script file not found: {script_remote_path}")

        # Run the script using subprocess
        process = subprocess.Popen(
            [sys.executable, script_remote_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )

        # Capture output and error while streaming stdout in real-time
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        # Create threads to handle stdout and stderr streams
        stdout_thread = threading.Thread(
            target=stream_output, args=(process.stdout, stdout_lines)
        )
        stderr_thread = threading.Thread(
            target=stream_output, args=(process.stderr, stderr_lines)
        )

        # Set as daemon threads so they exit when the main program exits
        stdout_thread.daemon = True
        stderr_thread.daemon = True

        # Start the threads
        stdout_thread.start()
        stderr_thread.start()

        # Wait for the process to complete
        return_code = process.wait()

        # Wait for the threads to finish
        stdout_thread.join()
        stderr_thread.join()

        # Join the captured output
        stdout_output = "".join(stdout_lines)
        stderr_output = "".join(stderr_lines)

        # Check return code
        if return_code == 0:
            result["success"] = True
            result["output"] = stdout_output
        else:
            result["error"] = stderr_output

    except Exception as e:
        import traceback

        result["error"] = str(e) + "\n" + traceback.format_exc()

    print(f"Result status: {'success' if result['success'] else 'failed'}")

    return result
