from matplotlib import pyplot as pl
import json
import sys
from pathlib import Path


def main(argv):
    with open(Path(argv[0]).absolute(), encoding="utf-8") as outfile:
        results = json.loads(outfile.read())
        for k,v in results[0].items():
            if isinstance(v,list):
                pl.boxplot(
                    [res[k] for res in results],
                    autorange=True,
                    labels=[res["name"] for res in results],
                    widths=0.3,
                    patch_artist=True,
                )
                pl.xticks(rotation=45, ha="right")
                pl.title("test")
                pl.tight_layout()
                pl.show()


if __name__ == "__main__":
    main(sys.argv[1:])
