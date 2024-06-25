import lamindb as ln

ln.settings.transform.stem_uid = "VFYCIuaw2GsX"
ln.settings.transform.version = "1"
ln.settings.transform.name = "My good script 2"

if __name__ == "__main__":
    # we're using new_run here to mock the notebook situation
    # and cover the look up of an existing run in the tests
    # new_run = True is trivial
    ln.track(new_run=False)

    print("hello!")

    ln.finish()
