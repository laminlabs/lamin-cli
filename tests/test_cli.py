import os


def test_entrypoint():
    exit_status = os.system("lamin --help")
    assert exit_status == 0


def test_login():
    exit_status = os.system("lamin login testuser1")
    assert exit_status == 0

    exit_status = os.system(
        "lamin login testuser1 --key cEvcwMJFX4OwbsYVaMt2Os6GxxGgDUlBGILs2RyS"
    )
    assert exit_status == 0

    # backward compat
    exit_status = os.system(
        "lamin login testuser1 --password cEvcwMJFX4OwbsYVaMt2Os6GxxGgDUlBGILs2RyS"
    )
    assert exit_status == 0
