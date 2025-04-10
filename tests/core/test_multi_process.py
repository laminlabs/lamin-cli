import subprocess
from multiprocessing import Process
from pathlib import Path

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def run_script():
    filepath = scripts_dir / "merely-import-lamindb.py"
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0


def test_parallel_execution():
    n_processes = 32
    processes = []

    # Create and start new processes
    for _ in range(n_processes):
        p = Process(target=run_script)
        p.start()
        processes.append(p)

    # Wait for all processes to finish
    for p in processes:
        p.join()

    for p in processes:
        assert p.exitcode == 0
