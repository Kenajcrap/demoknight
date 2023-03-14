from matplotlib import pyplot as plt
import numpy as np
import scipy as sp
import json
import sys
from pathlib import Path

def main(argv):
    with open(Path(argv[0]).absolute(), encoding="utf-8") as outfile:
        results = json.loads(outfile.read())
        for k,v in results[0].items():
            if isinstance(v,list):
                fig, ax = plt.subplots(tight_layout=True)
                mu, std = sp.stats.norm.fit(v)
                
                ax.hist([res[k] for res in results], density=True, bins="auto")
                # Plot the PDF.
                xmin, xmax = plt.xlim()
                x = np.linspace(xmin, xmax, 100)
                p = sp.stats.norm.pdf(x, mu, std)
                plt.plot(x, p, 'k', linewidth=2)
                title = "Fit results: mu = %.2f,  std = %.2f" % (mu, std)
                plt.title(title)
                plt.show()
                
if __name__ == "__main__":
    main(sys.argv[1:])