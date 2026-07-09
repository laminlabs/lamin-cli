def main():
    from lamin_cli.hub import switch_branch

    switch_branch("test-branch-profiling", create=True)


def cleanup():
    from lamindb import Branch

    Branch.get(name="test-branch-profiling").delete(permanent=True)


if __name__ == "__main__":
    main()
