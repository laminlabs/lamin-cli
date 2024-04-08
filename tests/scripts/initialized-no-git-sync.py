import lamindb as ln

ln.connect("lamindb-unit-tests")

if __name__ == "__main__":
    # we're using new_run here to mock the notebook situation
    # and cover the look up of an existing run in the tests
    # new_run = True is trivial
    print("hello!")
