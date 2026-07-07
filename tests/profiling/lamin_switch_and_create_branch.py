def main():
    from lamindb.setup import switch

    branch_data = switch(name="test-branch-profiling", create=True)
    print(branch_data)


def cleanup():
    from lamindb import Branch

    Branch.get(name="test-branch-profiling").delete(permanent=True)


if __name__ == "__main__":
    main()
