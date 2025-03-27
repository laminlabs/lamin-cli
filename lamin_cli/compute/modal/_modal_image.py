import modal

"""
Maybe some abstraction of creating images needs to be done, its doable we just need agreement, I am not using this now but its mock code. 
"""


def create_image(
    base_type="debian_slim",
    python_version="3.10",
    pip_packages=None,
    apt_packages=None,
    env_vars=None,
    commands=None,
    local_dirs=None,
    local_files=None,
    local_python_modules=None,
    force_rebuild=False,
    gpu_type=None
):
    """
    Create a customized Modal image based on specified parameters.
    
    Parameters:
    -----------
    base_type : str
        The base image type to use. Options: "debian_slim", "micromamba", or a registry URL.
    python_version : str
        Python version to use (e.g., "3.10").
    pip_packages : list
        List of Python packages to install with pip.
    apt_packages : list
        List of system packages to install with apt.
    env_vars : dict
        Environment variables to set in the image.
    commands : list
        Shell commands to run during image building.
    local_dirs : dict
        Dictionary mapping local directory paths to remote paths.
    local_files : dict
        Dictionary mapping local file paths to remote paths.
    local_python_modules : list
        List of local Python modules to add to the image.
    force_rebuild : bool
        Whether to force rebuilding the image.
    gpu_type : str
        GPU type to use during image setup (e.g., "H100").
        
    Returns:
    --------
    modal.Image
        The configured Modal image.
    """
    # Initialize the base image based on the specified type
    if base_type == "debian_slim":
        image = modal.Image.debian_slim(python_version=python_version)
    elif base_type == "micromamba":
        image = modal.Image.micromamba()
    elif base_type.startswith("registry:"):
        # For registry images (format: "registry:image_name")
        registry_name = base_type[9:]
        image = modal.Image.from_registry(registry_name, add_python=python_version)
    else:
        # Default to debian_slim if not recognized
        image = modal.Image.debian_slim(python_version=python_version)
    
    # Install apt packages if specified
    if apt_packages:
        image = image.apt_install(*apt_packages, force_build=force_rebuild)
    
    # Install pip packages if specified
    if pip_packages:
        if gpu_type:
            image = image.pip_install(*pip_packages, force_build=force_rebuild, gpu=gpu_type)
        else:
            image = image.pip_install(*pip_packages, force_build=force_rebuild)
    
    # Set environment variables if specified
    if env_vars:
        image = image.env(env_vars)
    
    # Run commands if specified
    if commands:
        for cmd in commands:
            image = image.run_commands(cmd, force_build=force_rebuild)
    
    # Add local directories if specified
    if local_dirs:
        for local_path, remote_path in local_dirs.items():
            image = image.add_local_dir(local_path, remote_path=remote_path)
    
    # Add local files if specified
    if local_files:
        for local_path, remote_path in local_files.items():
            image = image.add_local_file(local_path, remote_path=remote_path)
    
    # Add local Python modules if specified
    if local_python_modules:
        for module in local_python_modules:
            image = image.add_local_python_source(module)
    
    return image