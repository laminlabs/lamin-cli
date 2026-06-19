def main():
    from lamin_cli.hub import create_branch

    branch_data = create_branch(name="test-branch-profiling")
    print(branch_data)


def cleanup():
    from lamindb import Branch

    Branch.get(name="test-branch-profiling").delete(permanent=True)


if __name__ == "__main__":
    main()
