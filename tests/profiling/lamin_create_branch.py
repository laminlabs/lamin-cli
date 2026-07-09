def main():
    from lamin_cli.hub import create_branch

    create_branch(name="test-branch-profiling")


def cleanup():
    from lamindb import Branch

    Branch.get(name="test-branch-profiling").delete(permanent=True)


if __name__ == "__main__":
    main()
