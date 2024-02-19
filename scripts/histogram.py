from matplotlib import pyplot as plt
import numpy as np
import scipy as sp
import json
import sys
from pathlib import Path
from platform import system


def main(argv):
    with open(Path(argv[0]).absolute(), encoding="utf-8") as outfile:
        file = json.loads(outfile.read())
        summary = []
        # Import mangohud/presentmon data and set some constants
        if system().startswith("Win"):
            usecols = (9, 7)
            skiprows = 1
            elapsed_second = 1
            frametime_ms = 1
        elif system().startswith("Linux"):
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
        for k, v in summary[0].items():
            if isinstance(v, list):
                fig, ax = plt.subplots(tight_layout=True)
                shape, location, scale = sp.stats.gamma.fit(v)

                ax.hist([res[k] for res in summary][0], density=True, bins="auto")
                # Plot the PDF.
                xmin, xmax = plt.xlim()
                x = np.linspace(xmin, xmax, 100)
                p = sp.stats.gamma.pdf(x, shape, location, scale)
                plt.plot(x, p, "k", linewidth=2)
                title = "Fit results: shape = %.2f,  location = %.2f, scale = %.2f" % (
                    shape,
                    location,
                    scale,
                )
                plt.title(title)
                plt.show()


if __name__ == "__main__":
    main(sys.argv[1:])
