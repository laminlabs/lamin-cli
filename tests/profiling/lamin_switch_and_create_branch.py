def main():
    from lamindb.setup import switch

    switch("test-branch-profiling", create=True)


def cleanup():
    from lamindb import Branch

    Branch.get(name="test-branch-profiling").delete(permanent=True)


if __name__ == "__main__":
    main()
