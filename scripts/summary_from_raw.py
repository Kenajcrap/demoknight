from pathlib import Path
import numpy as np
from platform import system
import csv
import json
import sys


def main(argv):
    percentiles = [0.1, 1, 90, 99]
    raw_files = list(Path(argv[0]).glob("./*[0-9].csv"))
    summary = []
    start_delay = 2
    output_file = "./tests/1.3.0_super_long"
    format = "json"

    # Import mangohud/presentmon data and set some constants
    if system().startswith("Win"):
        usecols = (9, 7)
        skiprows = 1
        elapsed_second = 1
        frametime_ms = 1
    elif system().startswith("Linux"):
        usecols = (1, 11)
        skiprows = 3
        elapsed_second = 1000000000
        frametime_ms = 1000
    summary = []

    for t in range(2):
        entry = {
            "name": f"Test_{t}",
            "Average Frametime": [],
            "Variance of Frametime": [],
        }
        for n in percentiles:
            if n:
                entry[f"{n}% High of Frametime"] = []
        for i in range(int(len(raw_files) / 2) * t, int(len(raw_files) / 2) * (t + 1)):
            if i in (0, 1200):
                continue
            arr = np.loadtxt(
                raw_files[i], delimiter=",", usecols=usecols, skiprows=skiprows
            )
            # We actually start capturing 2 seconds before we need to, so get
            # rid of those rows
            arr[:, 1] -= start_delay * elapsed_second
            arr = arr[arr[..., 1] >= 0]
            entry["Average Frametime"].append(
                np.average(arr[:, 0] / frametime_ms, axis=0)
            )
            entry["Variance of Frametime"].append(
                np.var(arr[:, 0] / frametime_ms, axis=0)
            )
            for n in percentiles:
                if n:
                    entry[f"{n}% High of Frametime"].append(
                        np.percentile(arr[:, 0] / frametime_ms, 100 - n, axis=0)
                    )

        summary.append(entry)
    with open(f"{output_file}.{format}", "w", newline="", encoding="utf-8") as outfile:
        if format == "json":
            json.dump(summary, outfile)
        elif format == "csv":
            writer = csv.DictWriter(outfile, fieldnames=["name", "average", "variance"])
            writer.writeheader()
            for test in summary:
                test["name"] = [test["name"]] * len(test["average"])
                v2 = [dict(zip(test, t)) for t in zip(*test.values())]
                writer.writerows(v2)
    print("done")


if __name__ == "__main__":
    main(sys.argv[1:])
