import os
import subprocess
import sys
import threading
from pathlib import Path

import modal


def run_script(path: Path):
    """Takes a path to a script for running it as a function through Modal."""
    result = {"success": False, "output": "", "error": ""}

    def stream_output(stream, capture_list):
        """Read from stream line by line and print in real-time while also capturing to a list."""
        for line in iter(stream.readline, ""):
            print(line, end="")  # Print in real-time
            capture_list.append(line)
        stream.close()

    if not path.exists():
        raise FileNotFoundError(f"Script file not found: {path}")

    try:
        # Run the script using subprocess
        process = subprocess.Popen(
            [sys.executable, path.as_posix()],
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
    return result


class Runner:
    def __init__(
        self,
        app_name: str,
        local_mount_dir: str | Path = "./scripts",
        remote_mount_dir: str | Path = "/scripts",
        image_url: str | None = None,
        packages: list[str] | None = None,
        n_cpu: int = 8,
        gpu: str | None = None,
    ):
        self.app_name = app_name  # we use the LaminDB project name as the app name
        self.app = self.create_modal_app(app_name)

        self.local_mount_dir = local_mount_dir
        self.remote_mount_dir = remote_mount_dir

        self.image = self.create_modal_image(
            local_dir=local_mount_dir, packages=packages, image_url=image_url
        )

        self.modal_function = self.app.function(image=self.image, cpu=n_cpu, gpu=gpu)(
            run_script
        )

    def run(self, script_local_path: str | Path):
        script_remote_path = self.local_to_remote_path(str(script_local_path))
        with modal.enable_output(show_progress=True):  # Prints out modal logs
            with self.app.run():
                self.modal_function.remote(Path(script_remote_path))

    def create_modal_app(self, app_name: str):
        app = modal.App(app_name)
        return app

    def local_to_remote_path(self, local_path: str | Path) -> str:
        local_path = Path(local_path).absolute()
        local_mount_dir = Path(self.local_mount_dir).absolute()
        remote_mount_dir = Path(self.remote_mount_dir)

        # Check if local_path is inside local_mount_dir
        try:
            # This will raise ValueError if local_path is not relative to local_mount_dir
            relative_path = local_path.relative_to(local_mount_dir)
        except ValueError as err:
            raise ValueError(
                f"Local path '{local_path}' is not inside the mount directory '{local_mount_dir}'"
            ) from err

        # Join remote_mount_dir with the relative path
        remote_path = remote_mount_dir / relative_path

        # Return as string with normalized separators
        return remote_path.as_posix()

    def lamin_env_setup(self):
        from pathlib import Path

        import lamindb_setup
        from dotenv import load_dotenv

        settings_env_variable: dict = {}
        settings_dir = lamindb_setup.core._settings_store.settings_dir

        user_env_path = Path(settings_dir) / "current_user.env"
        instance_env_path = Path(settings_dir) / "current_instance.env"

        load_dotenv(user_env_path)
        load_dotenv(instance_env_path)

        key_value = os.environ.get("lamin_user_api_key")

        if not key_value:
            raise ValueError("No Lamin API key found in current_user.env")
        # pass keys to the image env as a dictionary
        settings_env_variable["LAMIN_API_KEY"] = key_value
        settings_env_variable["lamin_project_name"] = self.app_name
        settings_env_variable["lamin_instance_name"] = os.environ.get(
            "lamindb_instance_name"
        )
        settings_env_variable["lamin_instance_owner"] = os.environ.get(
            "lamindb_instance_owner"
        )

        return settings_env_variable

    def create_modal_image(
        self,
        python_version: str = "3.12",
        packages: list | None = None,
        local_dir: str | Path = "./scripts",
        remote_dir: str = "/scripts/",
        image_url: str | None = None,
        env_variables: dict | None = None,
    ) -> modal.Image:
        all_env_variables = self.lamin_env_setup()  # Lamin default env variables

        if packages is None:
            packages = []

        if env_variables:
            all_env_variables.update(env_variables)

        if image_url is None:
            image = modal.Image.debian_slim(python_version=python_version)
        else:
            image = modal.Image.from_registry(image_url, add_python=python_version)
        return (
            image.pip_install(packages)
            .env(all_env_variables)
            .add_local_dir(local_dir, remote_dir)
        )
