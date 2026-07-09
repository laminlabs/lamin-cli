def main():
    import lamindb_setup as ln_setup
    from lamin_cli.hub import switch_and_create_fast_path
    from lamindb.setup import switch as switch_

    if ln_setup.settings.instance.is_managed_by_hub:
        switch_and_create_fast_path("test-branch-profiling")
    else:
        switch_("test-branch-profiling", create=True)


def cleanup():
    from lamindb import Branch

    Branch.get(name="test-branch-profiling").delete(permanent=True)


if __name__ == "__main__":
    main()
