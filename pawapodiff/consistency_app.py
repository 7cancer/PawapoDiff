"""Standalone Windows GUI; processing stays local and never edits input files."""

import csv
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from .consistency import check_consistency
from .engine import inspect


def main():
    import sys

    if "--check-runtime" in sys.argv:
        # Exercise bundled Tcl/Tk and the actual analysis path in the Windows build.
        import tempfile
        from pathlib import Path
        from pptx import Presentation
        from pptx.util import Inches

        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("ja.pptx", "en.pptx")]
            for path, texts in zip(paths, (("売上高", "売上高"), ("Sales", "Revenue"))):
                presentation = Presentation()
                for text in texts:
                    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
                    slide.shapes.add_textbox(
                        Inches(1), Inches(1), Inches(3), Inches(1)
                    ).text = text
                presentation.save(path)
            result = check_consistency(inspect(paths[0]), inspect(paths[1]))
            assert len(result["rows"]) == 1 and result["rows"][0]["inconsistent"]
        probe = tk.Tk()
        probe.withdraw()
        ttk.Treeview(probe)
        probe.update_idletasks()
        probe.destroy()
        return
    root = tk.Tk()
    root.title("PawapoConsistency — 表記ゆれ検知")
    root.geometry("1100x740")
    paths = [tk.StringVar(), tk.StringVar()]
    results = []
    messages = queue.Queue()
    status = tk.StringVar(
        value="日本語版と英語版のPPTXを選択してください。同じページ番号同士を対応付けます。"
    )
    only_differences = tk.BooleanVar(value=True)
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    for index, label in enumerate(("日本語版", "英語版")):
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=10).pack(side="left")
        ttk.Entry(row, textvariable=paths[index]).pack(
            side="left", fill="x", expand=True
        )

        def choose(i=index):
            path = filedialog.askopenfilename(filetypes=[("PowerPoint", "*.pptx")])
            if path:
                paths[i].set(path)

        ttk.Button(row, text="選択", command=choose).pack(side="left", padx=8)
    ttk.Label(
        frame,
        text="日本語が複数ページで一致する箇所の英語を比較します。空白・改行コードの違いは無視します。",
    ).pack(anchor="w", pady=8)
    ttk.Label(
        frame,
        text="行単位の候補では英語ボックス全体を表示します。位置から推定した対応や複雑なレイアウトは要確認です。",
    ).pack(anchor="w")
    toolbar = ttk.Frame(frame)
    toolbar.pack(fill="x", pady=12)
    columns = ("source", "page", "shape", "target", "state")
    table_frame = ttk.Frame(frame)
    table_frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(table_frame, columns=columns, show="headings")
    for column, label, width in zip(
        columns,
        ("日本語の同一表記", "ページ", "場所", "英語表記", "判定"),
        (240, 70, 160, 350, 180),
    ):
        tree.heading(column, text=label)
        tree.column(column, width=width)
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    table_frame.rowconfigure(0, weight=1)
    table_frame.columnconfigure(0, weight=1)
    detail = tk.Text(frame, height=7, wrap="word", state="disabled")
    detail.pack(fill="x", pady=8)
    ttk.Label(frame, textvariable=status, wraplength=1000).pack(anchor="w")

    def state(row):
        return (
            "・".join(
                label
                for flag, label in (
                    (row["inconsistent"], "表記差分"),
                    (row["missing"], "英語なし"),
                    (row["uncertain"], "対応要確認"),
                )
                if flag
            )
            or "一致"
        )

    def visible_rows():
        for row in results:
            if only_differences.get() and not (
                row["inconsistent"] or row["missing"] or row["uncertain"]
            ):
                continue
            for entry in row["entries"]:
                yield row, entry

    def refresh():
        tree.delete(*tree.get_children())
        for row, entry in visible_rows():
            tree.insert(
                "",
                "end",
                values=(
                    row["source"],
                    entry["page"],
                    entry["shape"],
                    entry["target"],
                    state(row),
                ),
                tags=(str(results.index(row)), str(row["entries"].index(entry))),
            )

    def selected(event):
        selection = tree.selection()
        if not selection:
            return
        row_id, entry_id = tree.item(selection[0], "tags")
        row = results[int(row_id)]
        entry = row["entries"][int(entry_id)]
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert(
            "1.0",
            f"日本語: {entry['source']}\n英語: {entry['target']}\nページ: {entry['page']} / {entry['shape']} / 位置・寸法(pt): {entry['box']}\n{entry['note']}",
        )
        detail.configure(state="disabled")

    tree.bind("<<TreeviewSelect>>", selected)

    def run():
        inputs = [path.get() for path in paths]
        if not all(inputs):
            messagebox.showerror(
                "ファイル未選択", "日本語版・英語版を両方選択してください。"
            )
            return
        button.configure(state="disabled")
        results.clear()
        refresh()
        export_button.configure(state="disabled")
        status.set("解析中…")

        def worker():
            try:
                messages.put(
                    ("done", check_consistency(inspect(inputs[0]), inspect(inputs[1])))
                )
            except Exception as error:
                messages.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            kind, value = messages.get_nowait()
        except queue.Empty:
            pass
        else:
            button.configure(state="normal")
            if kind == "error":
                status.set("解析に失敗しました。")
                messagebox.showerror("解析エラー", value)
            else:
                results.extend(value["rows"])
                refresh()
                export_button.configure(state="normal")
                count = sum(row["inconsistent"] for row in results)
                warning_count = len(value["warnings"])
                status.set(
                    f'完了：繰り返し表記 {len(results)}件 / 英語の表記差分 {count}件 / 解析要確認 {warning_count}件 / メディア等の通知 {len(value["notices"])}件'
                )
                if warning_count:
                    messagebox.showwarning(
                        "解析要確認",
                        "\n".join(
                            f"{w['side']} {w.get('page', '')}ページ: {w.get('message', '')}"
                            for w in value["warnings"]
                        ),
                    )
        root.after(100, poll)

    def export():
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")]
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("日本語", "ページ", "場所", "英語", "判定", "注意"))
                for row, entry in visible_rows():
                    values = (
                        row["source"],
                        entry["page"],
                        entry["shape"],
                        entry["target"],
                        state(row),
                        entry["note"],
                    )
                    writer.writerow(
                        [
                            (
                                "'" + value
                                if isinstance(value, str)
                                and value.lstrip().startswith(("=", "+", "-", "@"))
                                else value
                            )
                            for value in values
                        ]
                    )
        except OSError as error:
            messagebox.showerror("保存エラー", str(error))

    button = ttk.Button(toolbar, text="表記ゆれを検知", command=run)
    button.pack(side="left")
    ttk.Checkbutton(
        toolbar,
        text="差分・英語なし・対応要確認のみ",
        variable=only_differences,
        command=refresh,
    ).pack(side="left", padx=16)
    export_button = ttk.Button(
        toolbar, text="表示中の結果をCSV保存", command=export, state="disabled"
    )
    export_button.pack(side="right")
    root.after(100, poll)
    root.mainloop()


if __name__ == "__main__":
    main()
