"""Run the real Tk UI with demo files; capture both tabs and verify CSV export."""

import argparse
import csv
import json
from pathlib import Path
import runpy
import sys
import time
import tkinter as tk
from tkinter import font, ttk
from unittest.mock import patch
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pawapodiff.consistency_app import create_window
from pawapodiff.windows_runtime import configure_console


def smoke(directory):
    configure_console()
    directory = Path(directory).resolve()
    create_demo = runpy.run_path(str(Path(__file__).with_name("create-demo.py")))[
        "create_demo"
    ]
    paths = create_demo(directory)
    root = tk.Tk()
    if sys.platform != "win32":
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont"):
            font.nametofont(name).configure(family="Noto Sans CJK JP", size=11)
    app = create_window(root)
    root.geometry("1400x900+0+0")
    ttk.Style(root).configure("Treeview", rowheight=32)
    try:
        for variable, path in zip(app["paths"], paths):
            variable.set(str(path))
        app["button"].invoke()
        deadline = time.monotonic() + 30
        while not app["status"].get().startswith("完了"):
            root.update()
            if time.monotonic() > deadline:
                raise AssertionError(
                    "GUI analysis did not finish: " + app["status"].get()
                )
            time.sleep(0.02)
        assert len(app["notes"]) == 4
        assert app["notes"][2]["status"] == "英語なし"
        assert app["notes"][3]["status"] == "日本語残存候補"
        assert any(
            row["scope"] == "notes" and row["inconsistent"] for row in app["results"]
        )
        assert any(
            row["scope"] == "slide" and row["inconsistent"] for row in app["results"]
        )
        assert not any(
            row["source"] == "営業利益" and row["inconsistent"]
            for row in app["results"]
        )
        for child in app["tree"].get_children():
            if "ノート /" in str(app["tree"].item(child, "values")):
                app["tree"].selection_set(child)
                app["tree"].event_generate("<<TreeviewSelect>>")
                break
        root.update()
        bbox = (
            root.winfo_rootx(),
            root.winfo_rooty(),
            root.winfo_rootx() + root.winfo_width(),
            root.winfo_rooty() + root.winfo_height(),
        )
        ImageGrab.grab(bbox=bbox).save(directory / "01-consistency.png")
        csv_path = directory / "consistency.csv"
        with patch(
            "pawapodiff.consistency_app.filedialog.asksaveasfilename",
            return_value=str(csv_path),
        ):
            app["export"].invoke()
        assert "発表者ノート" in csv_path.read_text(encoding="utf-8-sig")
        app["notebook"].select(1)
        app["notes_tree"].selection_set("2")
        app["notes_tree"].event_generate("<<TreeviewSelect>>")
        root.update()
        ImageGrab.grab(bbox=bbox).save(directory / "02-speaker-notes.png")
        csv_path = directory / "speaker-notes.csv"
        with patch(
            "pawapodiff.consistency_app.filedialog.asksaveasfilename",
            return_value=str(csv_path),
        ):
            app["export"].invoke()
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        assert len(rows) == 5 and rows[3][4] == "英語なし"
        (directory / "verification.json").write_text(
            json.dumps(
                {
                    "status": app["status"].get(),
                    "rows": app["results"],
                    "speaker_notes": app["notes"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(app["status"].get())
        print("PASS: real GUI analysis, both tabs, full note text and CSV export")
    finally:
        root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    smoke(parser.parse_args().directory)
