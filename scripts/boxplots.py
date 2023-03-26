from matplotlib import pyplot as pl
from matplotlib import patches as mpatches
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
                data = [res[k] for res in results]
                # [d.sort() for d in data]
                # [[d.pop() for _ in range(5)] for d in data]
                fig, ax = pl.subplots()
                bp = ax.boxplot(
                    data,
                    autorange=True,
                    labels=[res["name"] for res in results],
                    widths=0.3,
                    meanline=True,
                    showmeans=True,
                )
                # Add the mean values to the plot
                for i, mean in enumerate(np.mean(data, axis=1)):
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
                        f"t: {round(t,3)}\np: {round(p,3)}",
                        ha="center",
                        va="center",
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
                pl.title(f"{k} ({len(data[0])} samples)")
                ax.annotate(
                    "Windows 10 build 19041.vb_release.191206-1406, GTX 1660 Super, AMD Ryzen 5 3600,\n1920x1080 Highest, gorge1.dem (tick 466, 20 seconds duration)",
                    xy=(1.0, -0.2),
                    xycoords="axes fraction",
                    ha="right",
                    va="center",
                    fontsize=6,
                )
                pl.xlabel("Version")
                pl.ylabel("Miliseconds")
                pl.tight_layout()
                pl.show()


if __name__ == "__main__":
    main(sys.argv[1:])
