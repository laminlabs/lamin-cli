import os

# from lamindb_setup.core._hub_crud import sb_select_instance_by_id
# from lamindb_setup.core._hub_client import call_with_fallback_auth
# import lamindb_setup as ln_setup


def test_migrate_create():
    exit_status = os.system("lamin migrate create")
    assert exit_status == 0


def test_migrate_deploy():
    os.system("lamin login testuser1")
    import lamindb as ln

    instance_slug = ln.setup.settings.instance.slug
    # exit_status = os.system(
    #     "lamin connect testuser1/static-test-instance-private-sqlite"
    # )
    # assert exit_status == 0
    # exit_status = os.system("lamin migrate deploy")
    # assert exit_status == 0
    # now test that the hub got populated with the correct lamindb version
    # test it once we integrated it in the CLI output
    # instance = call_with_fallback_auth(
    #     sb_select_instance_by_id,
    #     instance_id=ln_setup.settings.instance.id.hex,
    # )
    # import lamindb
    # assert instance["lamindb_version"] == lamindb.__version__
    exit_status = os.system(f"lamin connect {instance_slug}")
    assert exit_status == 0


# def test_migrate_squash():
#     exit_status = os.system(
#         "yes | lamin migrate squash --package-name lamindb --end-number 0023"
#     )
#     assert exit_status == 0
