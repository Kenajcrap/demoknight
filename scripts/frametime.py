from matplotlib import pyplot as pl
from matplotlib.ticker import (MultipleLocator, AutoMinorLocator)
import numpy as np
import scipy
import json
import sys
from platform import system
from pathlib import Path


def main(argv):
    if not argv:
        print("Averages")

    if system().startswith("Win"):
        usecols = (9, 7)
        skiprows = 1
        one_second = 1
    elif system().startswith("Linux"):
        usecols = (1, 11)
        skiprows = 3
        one_second = 1000000000
    _, s = pl.subplots(figsize=(400,10))
    for p in list(Path(argv[0]).glob("./*")):
        one_test = [[0,0]]
        for f in list(Path(p).glob("./*[0-9].csv")):
            arr = np.loadtxt(f, delimiter=",", usecols=usecols, skiprows=skiprows)
            arr[:, 1] -= 2 * one_second
            arr = arr[arr[..., 1] >= 0]
            one_test = np.concatenate((one_test, arr))
        one_test[:, 1] = np.floor(one_test[:, 1] * 500) / 500
        unique_values = np.unique(one_test[:, 1])
        result = np.array(
            [[np.mean(one_test[one_test[:, 1] == value, 0]), value] for value in unique_values]
        )
        result = result[result[:, 1].argsort()]
        s.plot(result[:, 1], result[:, 0], linewidth=0.3)
    #pl.legend([plt[0] for plt in s], [p.name for p in list(Path(argv[0]).glob("./*"))])
    pl.xlabel("Time (s)")
    pl.ylabel("Frametime (ms)")
    pl.xticks()
    s.margins(0,0.1)
    s.xaxis.set_major_locator(MultipleLocator(1))
    s.xaxis.set_minor_locator(AutoMinorLocator())
    pl.tight_layout()
    pl.savefig(f'{Path(argv[0]).name}.png')
    
    pl.show()


if __name__ == "__main__":
    main(sys.argv[1:])
