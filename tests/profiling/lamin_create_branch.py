def main():
    from lamindb import Branch

    Branch(name="test-branch-profiling").save()


def cleanup():
    from lamindb import Branch

    Branch.get(name="test-branch-profiling").delete(permanent=True)


if __name__ == "__main__":
    main()
