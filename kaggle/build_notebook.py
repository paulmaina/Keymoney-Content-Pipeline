"""Rebuild upscale.ipynb from upscale.py, so the two never drift apart.

Cells are separated by "# %%" lines; "# %% [markdown]" cells are markdown with "# " stripped.
Usage: .venv\\Scripts\\python kaggle\\build_notebook.py
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def cells_from_py(source):
    cells = []
    for chunk in re.split(r"^# %%", source, flags=re.M)[1:]:
        header, _, body = chunk.partition("\n")
        body = body.strip("\n")
        if header.strip() == "[markdown]":
            text = "\n".join(line[2:] if line.startswith("# ") else line.lstrip("#") for line in body.splitlines())
            cells.append({"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)})
        else:
            cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                          "source": body.splitlines(keepends=True)})
    return cells


def build(py=HERE / "upscale.py"):
    notebook = {
        "cells": cells_from_py(py.read_text(encoding="utf-8")),
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python"},
                     "kaggle": {"accelerator": "nvidiaTeslaT4", "isInternetEnabled": True, "isGpuEnabled": True}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    out = py.with_suffix(".ipynb")
    out.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(f"wrote {build()}")
