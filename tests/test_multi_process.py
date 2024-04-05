from multiprocessing import Process
from pathlib import Path
import subprocess

scripts_dir = Path(__file__).parent.resolve() / "scripts"


def run_script():
    filepath = scripts_dir / "initialized.py"
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())


def test_parallel_execution():
    num_processes = 4
    processes = []

    # Create and start new processes
    for _ in range(num_processes):
        p = Process(target=run_script)
        p.start()
        processes.append(p)

    # Wait for all processes to finish
    for p in processes:
        p.join()
