"""UTF-8 CSV downloads; never let slide text become spreadsheet formulas."""

import csv
from io import StringIO


def csv_bytes(header, rows):
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow(
            [
                (
                    "'" + v
                    if isinstance(v, str)
                    and v.lstrip().startswith(("=", "+", "-", "@"))
                    else v
                )
                for v in row
            ]
        )
    return out.getvalue().encode("utf-8-sig")


def notes_csv(notes):
    return csv_bytes(
        ("ページ", "日本語ノート", "英語ノート", "判定"),
        ((n["page"], n["source"], n["target"], n["status"]) for n in notes),
    )


def consistency_csv(result):
    return csv_bytes(
        (
            "対象",
            "日本語",
            "ページ",
            "場所",
            "英語",
            "表記差分",
            "英語なし",
            "対応要確認",
        ),
        (
            (
                row["scope"],
                row["source"],
                e["page"],
                e["shape"],
                e["target"],
                row["inconsistent"],
                row["missing"],
                row["uncertain"],
            )
            for row in result["rows"]
            for e in row["entries"]
        ),
    )
