"""Optional report renderer. Only this module requires Matplotlib."""

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
PANELS = [
    ("p01", "A   Reliable advice", ("trust", "metrology")),
    ("p03", "B   Two false reports", ("metrology", "logistics")),
    ("p05", "C   Clarification and repair", ("metrology", "closeness")),
    ("p07", "D   Rapport and expertise", ("closeness", "metrology")),
]
STYLES = {"trust": ("#303840", "-", "general trust"), "metrology": ("#0072B2", "-", "metrology trust"),
          "logistics": ("#CC7900", "--", "logistics trust"), "closeness": ("#A34B70", "--", "closeness")}


def main():
    with (HERE / "results" / "trajectory.csv").open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 130 and len({(r["person"], r["step"]) for r in rows}) == len(rows)
    plt.rcParams.update({"font.size": 9, "svg.fonttype": "none", "svg.hashsalt": "echo002"})
    fig, axes = plt.subplots(2, 2, figsize=(7.5, 5.0), sharey=True, sharex=True, layout="constrained")
    for ax, (person, title, columns) in zip(axes.flat, PANELS):
        data = [r for r in rows if r["person"] == person]
        x = [int(r["step"]) for r in data]
        assert x == list(range(13))
        ax.axhline(.5, color="0.62", lw=.8, ls=":")
        for i, col in enumerate(columns):
            color, ls, label = STYLES[col]
            y = [float(r[col]) for r in data]
            assert all(0 <= v <= 1 for v in y)
            ax.plot(x, y, color=color, ls=ls, lw=1.6, marker=["o", "s"][i], ms=2.8, label=label)
        ax.set(title=title, xlim=(-.25, 12.25), ylim=(-.04, 1.04), xticks=[0, 3, 6, 9, 12],
               yticks=[0, .25, .5, .75, 1])
        ax.legend(loc="lower left", frameon=False, fontsize=8)
        ax.spines[["right", "top"]].set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("Verified event number")
    for ax in axes[:, 0]:
        ax.set_ylabel("Model score")
    target = HERE / "trajectory.svg"
    fig.savefig(target, metadata={"Date": None})
    lines = target.read_text(encoding="utf-8").splitlines()
    target.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8")
    plt.close(fig)


if __name__ == "__main__":
    main()
