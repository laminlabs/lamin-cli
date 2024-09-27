import lamindb as ln

ln.context.name = "My good script 2"
ln.track("VFYCIuaw2GsX0000")


if __name__ == "__main__":
    # we're using new_run here to mock the notebook situation
    # and cover the look up of an existing run in the tests
    # new_run = True is trivial
    ln.track(new_run=False)

    print("hello!")

    ln.finish()
