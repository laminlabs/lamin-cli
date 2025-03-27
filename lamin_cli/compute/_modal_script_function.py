def run_script_from_path(script_remote_path):
    import sys
    import traceback
    import os
    from io import StringIO
    
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    redirected_output = StringIO()
    redirected_error = StringIO()
    sys.stdout = redirected_output
    sys.stderr = redirected_error
    
    result = {
        "success": False,
        "output": "",
        "error": ""
    }
    
    try:
        # Check if the file exists
        if not os.path.exists(script_remote_path):
            raise FileNotFoundError(f"Script file not found: {script_remote_path}")
        
        # Read the script content
        with open(script_remote_path, 'r') as file:
            script_content = file.read()
        
        # Execute the script content
        exec(script_content)
        result["success"] = True
        result["output"] = redirected_output.getvalue()
    except Exception as e:
        result["error"] = str(e) + "\n" + traceback.format_exc()
    
    # Restore stdout and stderr
    sys.stdout = old_stdout
    sys.stderr = old_stderr
    print(result)
    return result