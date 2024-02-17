from matplotlib import pyplot as pl
from matplotlib import patches as mpatches
import numpy as np
import scipy
import json
import sys
from platform import system
from pathlib import Path


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
                if (i % file["passes"]<=0) and not file["keep_first_pass"]:
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
                data = [res[k] for res in summary]
                # [d.sort() for d in data]
                # [[d.pop() for _ in range(5)] for d in data]
                fig, ax = pl.subplots()
                bp = ax.boxplot(
                    data,
                    autorange=True,
                    widths=0.4,
                    labels=[res["name"] + (f" ({len(res[k])} samples)" if not all(len(d) for d in data) else "") for res in summary],
                    meanline=True,
                    showmeans=True,
                )
                # Add the mean values to the plot
                for i, d in enumerate(data):
                    mean = np.mean(d)
                    ax.text(i + 1, mean, f"{mean:.2f}", ha="center", va="bottom")

                # Calculate the positions of the mean lines of the boxes
                mean_lines = [
                    np.mean(bp["means"][i].get_ydata()) for i in range(len(data))
                ]

                for i in range(len(data) - 1):
                    t, p = scipy.stats.ttest_ind(
                        data[i], data[i + 1], alternative="greater"
                    )

                    # Find the index of the neighboring boxes
                    box1_index = i
                    box2_index = i + 1
                    box1_mean = mean_lines[box1_index]
                    box2_mean = mean_lines[box2_index]

                    y_pos = (box1_mean + box2_mean) / 2
                    x_pos = i + (1 / 2) + 1

                    pl.text(
                        x_pos,
                        y_pos,
                        f"p:{round(p,3)}",
                        ha="center",
                        va="bottom",
                    )

                    ax.add_patch(
                        mpatches.FancyArrowPatch(
                            (x_pos - 0.25, y_pos - (box2_mean - box1_mean) / 4),
                            (x_pos + 0.25, y_pos + (box2_mean - box1_mean) / 4),
                            mutation_scale=20,
                            arrowstyle="->",
                            color="green",
                        )
                    )
                pl.legend([bp["means"][0], bp["medians"][0]], ["Mean", "Median"])
                pl.title(f"{k}" + (f"({len(data[0])} samples)" if all(len(d) for d in data) else ""))
                pl.xticks(rotation=10, ha="right")
                ax.annotate(
                    f"{file['system']['OS']}, {file['system']['CPU']}, {file['system']['GPU']},\n{file['comment']}, {file['demo_path']} (start-tick {file['start_tick']}, {file['duration']} seconds duration)",
                    xy=(1, -0.3),
                    xycoords="axes fraction",
                    ha="right",
                    va="bottom",
                    fontsize=6,
                )
                pl.xlabel("Version")
                pl.ylabel("Miliseconds")
                fig.set_size_inches(3.5 + (0.5 * len(data)), 4.8)
                pl.tight_layout()
                pl.show()


if __name__ == "__main__":
    main(sys.argv[1:])
