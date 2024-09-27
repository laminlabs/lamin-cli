import argparse
import lamindb as ln


# this section should typically be outside of the script
ln.Param(name="dataset_key", dtype="str").save()
ln.Param(name="learning_rate", dtype="float").save()
ln.Param(name="downsample", dtype="bool").save()
ln.Param(name="split", dtype="str").save()


def main():
    parser = argparse.ArgumentParser(description="A demo script.")
    parser.add_argument(
        "--dataset-key",
        type=str,
    )
    parser.add_argument("--downsample", action="store_true", help="Downsample")
    parser.add_argument(
        "--split",
        choices=["train", "test", "validation"],
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
    )
    args = parser.parse_args()

    params = {
        "dataset_key": args.dataset_key,
        "learning_rate": args.learning_rate,
        "downsample": args.downsample,
        "split": args.split,
    }

    ln.track("JjRF4mACd9m00000", params=params)

    # actually do stuff

    ln.finish()


if __name__ == "__main__":
    main()
