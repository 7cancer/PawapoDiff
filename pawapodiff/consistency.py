"""Cross-page terminology review without rendering or modifying presentations."""

from collections import defaultdict
from .engine import pair_text_shapes
from .errors import InputError
from .translations import JP, normalize
from .speaker_notes import note_shapes, compare_speaker_notes


def check_consistency(japanese, english):
    if len(japanese["slides"]) != len(english["slides"]):
        raise InputError(
            "日本語版と英語版のページ数が一致しません。", code="PAGE_COUNT_MISMATCH"
        )
    if (japanese["width"], japanese["height"]) != (english["width"], english["height"]):
        raise InputError(
            "日本語版と英語版のスライド寸法が一致しません。", code="SLIDE_SIZE_MISMATCH"
        )
    groups = defaultdict(list)
    for page, (left, right) in enumerate(zip(japanese["slides"], english["slides"]), 1):
        for scope, pairs in (
            ("slide", pair_text_shapes(left, right)),
            (
                "notes",
                [
                    (
                        next(iter(note_shapes(japanese, page)), None),
                        next(iter(note_shapes(english, page)), None),
                        False,
                    )
                ],
            ),
        ):
            for a, b, uncertain in pairs:
                if not a or not JP.search(a.get("text", "")):
                    continue
                source = a["text"]
                target = b.get("text", "") if b else ""
                # Whole blocks survive wrapping differences. Individual Japanese
                # lines are also candidates, but English lines are never guessed.
                block = normalize(source)
                candidates = {block: False}
                for line in source.splitlines():
                    key = normalize(line)
                    if key and JP.search(key) and key != block:
                        candidates[key] = True
                for key, partial in candidates.items():
                    groups[(scope, key)].append(
                        {
                            "page": page,
                            "shape": a["name"],
                            "scope": scope,
                            "source": source,
                            "target": normalize(target),
                            "box": a["box"],
                            "uncertain": uncertain
                            or partial
                            or bool(a.get("analysis_incomplete"))
                            or bool(b and b.get("analysis_incomplete")),
                            "note": (
                                "英語は対応するボックス全体（行の対応は要確認）"
                                if partial
                                else "位置による対応付けは要確認" if uncertain else ""
                            ),
                        }
                    )
    rows = []
    for (scope, source), entries in groups.items():
        if len({entry["page"] for entry in entries}) < 2:
            continue
        variants = {entry["target"] for entry in entries if entry["target"]}
        missing = any(not entry["target"] for entry in entries)
        rows.append(
            {
                "scope": scope,
                "source": source,
                "entries": entries,
                "inconsistent": len(variants) > 1,
                "missing": missing,
                "uncertain": any(entry["uncertain"] for entry in entries),
            }
        )
    rows.sort(
        key=lambda row: (
            not (row["inconsistent"] or row["missing"]),
            row["entries"][0]["page"],
            row["source"],
        )
    )
    return {
        "rows": rows,
        "speaker_notes": compare_speaker_notes(japanese, english),
        "warnings": [
            dict(w, side=side)
            for side, doc in (("日本語", japanese), ("英語", english))
            for w in doc.get("warnings", [])
        ],
        "notices": [
            dict(n, side=side)
            for side, doc in (("日本語", japanese), ("英語", english))
            for n in doc.get("notices", [])
        ],
    }
