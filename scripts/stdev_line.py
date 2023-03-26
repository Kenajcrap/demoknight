from matplotlib import pyplot as pl
import numpy as np
import scipy
import json
import sys
from pathlib import Path


def main(argv):
    with open(Path(argv[0]).absolute(), encoding="utf-8") as outfile:
        results = json.loads(outfile.read())
        for k, v in results[0].items():
            if isinstance(v, list):
                fig, ax = pl.subplots()
                ax.plot([np.std(v[:i], ddof=1) for i in range(len(v))])
                pl.show()


if __name__ == "__main__":
    main(sys.argv[1:])
