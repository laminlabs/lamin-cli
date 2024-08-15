import lamindb as ln

ln.settings.sync_git_repo = "https://github.com/laminlabs/lamin-cli"
ln.context.uid = "m5uCHTTpJnjQ0000"
ln.context.name = "My good script"


if __name__ == "__main__":
    # we're using new_run here to mock the notebook situation
    # and cover the look up of an existing run in the tests
    # new_run = True is trivial
    ln.context.track(new_run=False)

    print("hello!")

    ln.finish()
