def command_output(stdout: str) -> str:
    """Remove the optional startup banner from the isolated pytest environment."""
    first, _, rest = stdout.partition("\n")
    if first.endswith(" loaded LAMIN_ENV: pytest"):
        return rest
    return stdout
