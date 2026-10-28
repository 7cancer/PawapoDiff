"""Line-level candidates and one row per normalized source across all pages."""

from collections import defaultdict
import re

JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def normalize(text):
    return " ".join(text.split())


def lines(text):
    # python-pptx uses vertical tab for a soft line break and LF for paragraphs.
    result = text.splitlines()
    while result and not result[0].strip():
        result.pop(0)
    while result and not result[-1].strip():
        result.pop()
    return result


def translation_table(text_pairs):
    groups = defaultdict(list)
    issues = []
    for pair in text_pairs:
        source_lines, target_lines = lines(pair["source"]), lines(pair["target"])
        source_offset = next(
            (i for i, line in enumerate(pair["source"].splitlines()) if line.strip()), 0
        )
        aligned = len(source_lines) == len(target_lines)
        # Never guess which English line translates a Japanese line when counts
        # differ. Keep the whole target and explicitly flag uncertain alignment.
        for index, source in enumerate(source_lines):
            source = normalize(source)
            if not source:
                continue
            target = (
                normalize(target_lines[index]) if aligned else pair["target"].strip()
            )
            notes = list(pair["notes"])
            uncertain = pair["uncertain"] or (not aligned and bool(target_lines))
            if not aligned and target_lines:
                notes.append("改行数が異なるため行の対応は要確認（英訳はボックス全体）")
            entry = {
                "page": pair["page"],
                "shape": pair["shape"],
                "source_line": source_offset + index + 1,
                "target": target,
                "uncertain": uncertain,
                "notes": notes,
            }
            groups[source].append(entry)
            if not normalize(target):
                notes.append("訳抜け候補")
                issues.append(dict(entry, source=source, kind="訳抜け候補"))
            elif JP.search(target):
                notes.append("日本語の残存候補")
                issues.append(dict(entry, source=source, kind="日本語の残存候補"))
        # A target-only element can still contain untranslated Japanese, even
        # though it has no source text to display in the Japanese comparison table.
        if not source_lines and JP.search(pair["target"]):
            issues.append(dict(pair, kind="日本語の残存候補"))
    rows = []
    for source, entries in groups.items():
        variants = {}
        for entry in entries:
            key = normalize(entry["target"])
            if key not in variants:
                variants[key] = {"target": entry["target"], "entries": []}
            variants[key]["entries"].append(entry)
        inconsistent = (
            bool(JP.search(source)) and len([key for key in variants if key]) > 1
        )
        if inconsistent:
            issues.append(
                {"kind": "表記揺れ候補", "source": source, "entries": entries}
            )
        # This filter applies to the table only. Short text still participates in
        # missing-translation / residual-Japanese checks above.
        if len("".join(source.split())) <= 2:
            continue
        notes = list(
            dict.fromkeys(note for entry in entries for note in entry["notes"])
        )
        if inconsistent:
            notes.append("表記揺らぎの可能性あり")
        rows.append(
            {
                "source": source,
                "page": entries[0]["page"],
                "shape": entries[0]["shape"],
                "pages": sorted({entry["page"] for entry in entries}),
                "entries": entries,
                "variants": list(variants.values()),
                "target": "\n".join(variant["target"] for variant in variants.values()),
                "uncertain": any(entry["uncertain"] for entry in entries),
                "inconsistent": inconsistent,
                "notes": notes,
            }
        )
    return rows, issues
