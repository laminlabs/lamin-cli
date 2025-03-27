import sys
import os
import modal
from ._modal_script_function import run_script_from_path 

class Runner:
    def __init__(self, app_name, local_mount_dir='./scripts', remote_mount_dir='/scripts', packages=None, cpu=8.0):
        self.app = self.create_modal_app(app_name)
        self.local_mount_dir = local_mount_dir
        self.remote_mount_dir = remote_mount_dir
        self.image = self.create_modal_image(packages=packages)
        self.modal_function = self.app.function(image=self.image, cpu=cpu)(run_script_from_path)
    
    def run(self, script_local_path):
        script_remote_path = self.local_to_remote_path(script_local_path)
        with modal.enable_output():
            with self.app.run():
                self.modal_function.remote(script_remote_path)
    
    def create_modal_app(self, app_name):
        app = modal.App(app_name)
        return app
    
    def local_to_remote_path(self, local_path: str) -> str:
        local_path = os.path.abspath(local_path)
        local_mount_dir = os.path.abspath(self.local_mount_dir)
        remote_mount_dir = os.path.normpath(self.remote_mount_dir)

        if not local_path.startswith(local_mount_dir):
            raise ValueError(f"Local path '{local_path}' is not inside the mount directory '{local_mount_dir}'")

        # Get relative path from local mount point
        relative_path = os.path.relpath(local_path, local_mount_dir)

        # Join the relative path to the remote mount point
        remote_path = os.path.join(remote_mount_dir, relative_path)

        return remote_path

    # This needs to be expanded by a lot obviously here we can use a new Environment Class and abstract this.
    def create_modal_image(self,python_version="3.10", packages=[], local_dir='./scripts', remote_dir='/scripts/'):
        return (modal.Image.debian_slim(python_version=python_version)
                .pip_install(packages)
                .add_local_dir(self.local_mount_dir, self.remote_mount_dir))