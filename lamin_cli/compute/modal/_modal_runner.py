import os
import typing as t
from pathlib import Path

import lamindb as ln

import modal

from ._modal_script_function import run_script_from_path


class Runner:
    def __init__(
        self,
        app_name: str,
        local_mount_dir: str | Path = "./scripts",
        remote_mount_dir: str | Path = "/scripts",
        image_url=None,
        packages=None,
        cpu=8.0,
        gpu=None,
    ):
        self.app_name = app_name  # Project? Still thinking of --rebuild-image feature
        self.app = self.create_modal_app(app_name)

        self.local_mount_dir = local_mount_dir
        self.remote_mount_dir = remote_mount_dir

        self.image = self.create_modal_image(
            local_dir=local_mount_dir, packages=packages, image_url=image_url
        )

        self.modal_function = self.app.function(image=self.image, cpu=cpu, gpu=gpu)(
            run_script_from_path
        )

    def run(self, script_local_path: str | Path):
        script_remote_path = self.local_to_remote_path(str(script_local_path))
        with modal.enable_output():  # Prints out modal logs
            with self.app.run():
                self.modal_function.remote(script_remote_path)

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

    # We ideally need some Lamin abstraction for images/containers/environments? that will be compatible will all backends?
    # For now I just focused on Modal, and they provide a nice python API, other backends use .yaml files for configs usually...
    # This is the simplest for of just installing python packahes or using a premade image.
    def create_modal_image(
        self,
        python_version: str = "3.10",
        packages: list | None = None,
        local_dir: str | Path = "./scripts",
        remote_dir="/scripts/",
        image_url: str | None = None,
        settings_env_variable: dict | None = None,
    ):
        from pathlib import Path

        import lamindb_setup
        from dotenv import load_dotenv

        # Get the settings directory and user environment file
        if settings_env_variable is None:
            settings_env_variable = {}
        if settings_env_variable is None:
            settings_env_variable = {}
        if settings_env_variable is None:
            settings_env_variable = {}
        if packages is None:
            packages = []
        settings_dir = lamindb_setup.core._settings_store.settings_dir

        user_env_path = Path(settings_dir) / "current_user.env"
        instance_env_path = Path(settings_dir) / "current_instance.env"

        load_dotenv(user_env_path)
        load_dotenv(instance_env_path)

        key_value = os.environ.get("lamin_user_api_key")

        if not key_value:
            raise ValueError("No Lamin API key found in current_user.env")
        # pass keys to the image env as a dictionary
        settings_env_variable["lamin_user_api_key"] = key_value
        settings_env_variable["lamin_project_name"] = self.app_name
        settings_env_variable["lamin_instance_name"] = os.environ.get(
            "lamindb_instance_name"
        )
        settings_env_variable["lamin_instance_owner"] = os.environ.get(
            "lamindb_instance_owner"
        )

        if image_url:
            image = (
                modal.Image.from_registry(image_url, add_python=python_version)
                .pip_install(packages)
                .env(settings_env_variable)
                .add_local_dir(local_dir, remote_dir)
            )
        else:
            image = (
                modal.Image.debian_slim(python_version=python_version)
                .pip_install(packages)
                .env(settings_env_variable)
                .add_local_dir(self.local_mount_dir, self.remote_mount_dir)
            )

        return image

    # def run_compute_flow(self, script_local_path):
    #     self.run(script_local_path)
