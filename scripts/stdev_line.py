from matplotlib import pyplot as pl
import numpy as np
import scipy
import json
import sys
from pathlib import Path
from platform import system


def main(argv):
    with open(Path(argv[0]).absolute(), encoding="utf-8") as outfile:
        file = json.loads(outfile.read())
        summary = []
        # Import mangohud/presentmon data and set some constants
        if file["system"]["OS"].startswith("Win"):
            usecols = (9, 7)
            skiprows = 1
            elapsed_second = 1
            frametime_ms = 1
        elif file["system"]["OS"].startswith("Linux"):
            usecols = (1, 13)
            skiprows = 3
            elapsed_second = 1000000
            frametime_ms = 1
        for test in file["tests"]:
            entry = {
                "name": test["name"],
                "Average Frametime": [],
                "Variance of Frametime": [],
            }
            for n in argv[1:]:
                try:
                    prcnt = float(n)
                except:
                    raise ValueError(
                        "Percentages must be floats, with '.' as decimal separator"
                    )
                if prcnt <= 0:
                    raise ValueError("Percentages must be positive integers")
                entry[f"{n}% High of Frametime"] = []
            for i, res in enumerate(test["results"]):
                if (i % file["passes"] <= (file["discard_passes"] - 1)) and file[
                    "discard_passes"
                ]:
                    continue
                arr = np.loadtxt(res, delimiter=",", usecols=usecols, skiprows=skiprows)
                # We actually start capturing 2 seconds before we need to, so get
                # rid of those rows
                arr[:, 1] -= file["start_buffer"] * elapsed_second
                arr = arr[arr[..., 1] >= 0]
                entry["Average Frametime"].append(
                    np.average(arr[:, 0] / frametime_ms, axis=0)
                )
                entry["Variance of Frametime"].append(
                    np.var(arr[:, 0] / frametime_ms, axis=0)
                )
                for n in argv[1:]:
                    if n:
                        entry[f"{n}% High of Frametime"].append(
                            np.percentile(
                                arr[:, 0] / frametime_ms, 100 - float(n), axis=0
                            )
                        )

            summary.append(entry)
        for key in summary[0].keys():
            if key != "name":
                pl.figure()
                pl.title(f"Standard Deviation of {key} over passes")
                for item in summary:
                    pl.plot(
                        [np.std(item[key][:i], ddof=1) for i in range(len(item[key]))],
                        label=item["name"],
                    )
                pl.legend()
        pl.show()


if __name__ == "__main__":
    main(sys.argv[1:])
