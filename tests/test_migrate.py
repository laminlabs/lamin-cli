import os


def test_migrate_create():
    exit_status = os.system("lamin migrate create")
    assert exit_status == 0


def test_migrate_deploy():
    exit_status = os.system("lamin load https://lamin.ai/laminlabs/static-testinstance1")
    assert exit_status == 0
    exit_status = os.system("lamin migrate deploy")
    assert exit_status == 0


# def test_migrate_squash():
#     exit_status = os.system(
#         "yes | lamin migrate squash --package-name lnschema_core --end-number 0023"
#     )
#     assert exit_status == 0
