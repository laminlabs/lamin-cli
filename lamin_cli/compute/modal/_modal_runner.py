import os
import modal
import typing as t
import lamindb as ln
from pathlib import Path
from ._modal_script_function import run_script_from_path 

class Runner:
    def __init__(self, 
                app_name:str, 
                local_mount_dir:t.Union[str, Path]='./scripts', 
                remote_mount_dir:t.Union[str, Path]='/scripts',
                image_url=None,
                packages=None, 
                cpu=8.0,
                gpu=None): # we can specify CPU and memory on the App also GPU
        
        self.app_name = app_name
        self.app = self.create_modal_app(app_name)

        self.local_mount_dir = local_mount_dir
        self.remote_mount_dir = remote_mount_dir
        
        self.image = self.create_modal_image(local_dir=local_mount_dir, packages=packages, image_url=image_url)
        #@TODO add GPU support
        self.modal_function = self.app.function(image=self.image, cpu=cpu, gpu=gpu)(run_script_from_path)
    
    def run(self, script_local_path:str):
        script_remote_path = self.local_to_remote_path(script_local_path)
        with modal.enable_output(): # Prints out modal logs
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

        relative_path = os.path.relpath(local_path, local_mount_dir)

        remote_path = os.path.join(remote_mount_dir, relative_path)

        return remote_path
    

    # Just to demo what saving script on lamin would look like 
    def track(self, script_local_path:t.Union[str,Path], description:str='lamin compute run'):
        from lamin_cli._save import save_from_filepath_cli
        
        # How should we handle the key?, this for now?
        key = self.app_name + '/' + Path(script_local_path).name

        save_from_filepath_cli(script_local_path, 
                               key=key, 
                               description=description, 
                               registry='artifact') # Is it Artifact or Transform?.... should be transform
        
        # scratch the track function and use the ln.track functionality.  


    
    # We ideally need some Lamin abstraction for images/containers/environments? that will be compatible will all backends?
    # For now I just focused on Modal, and they provide a nice python API, other backends use .yaml files for configs usually...
    # This is the simplest for of just installing python packahes or using a premade image.
    def create_modal_image(self,python_version:str="3.10", packages:list=[], local_dir:str='./scripts', remote_dir='/scripts/', image_url:str=None, settings_env_variable:dict={}):
        import lamindb_setup
        from dotenv import load_dotenv
        from pathlib import Path
        
        # Get the settings directory and user environment file
        settings_dir = lamindb_setup.core._settings_store.settings_dir

        user_env_path = Path(settings_dir) / 'current_user.env'
        instance_env_path = Path(settings_dir) / 'current_instance.env'

        load_dotenv(user_env_path)
        load_dotenv(instance_env_path)

        key_value = os.environ.get("lamin_user_api_key")

        if not key_value:
            raise ValueError("No Lamin API key found in current_user.env")

        settings_env_variable['lamin_user_api_key'] = key_value
        settings_env_variable['lamin_project_name'] = self.app_name
        settings_env_variable['lamin_instance_name'] = os.environ.get("lamindb_instance_name")
        settings_env_variable['lamin_instance_owner'] = os.environ.get("lamindb_instance_owner")

        if image_url:
            image = (modal.Image.from_registry(image_url, add_python=python_version)
                    .pip_install(packages)
                    .env(settings_env_variable)
                    .add_local_dir(local_dir, remote_dir))
        else:
            image = (modal.Image.debian_slim(python_version=python_version)
                    .pip_install(packages)
                    .env(settings_env_variable)
                    .add_local_dir(self.local_mount_dir, self.remote_mount_dir))
        
        return image
    

    # def run_compute_flow(self, script_local_path):
    #     self.run(script_local_path)