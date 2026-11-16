"""Speaker-note text only: exclude slide thumbnails, page numbers and headers."""

from .translations import JP, normalize


def read_speaker_notes(slide):
    # Accessing notes_slide without this check would create a notes part.
    if not slide.has_notes_slide:
        return ""
    frame = slide.notes_slide.notes_text_frame
    return frame.text if frame is not None else ""


def compare_speaker_notes(left, right):
    count = len(left["slides"])
    source_notes = left.get("speaker_notes", [""] * count)
    target_notes = right.get("speaker_notes", [""] * count)
    rows = []
    for index in range(count):
        source, target = source_notes[index], target_notes[index]
        if source == "" and target == "":
            continue
        a, b = normalize(source or ""), normalize(target or "")
        if source is None or target is None:
            status = "解析要確認"
        elif a and not b:
            status = "英語なし"
        elif b and not a:
            status = "日本語なし"
        elif JP.search(b):
            status = "日本語残存候補"
        elif a == b:
            status = "同文"
        else:
            status = "日英の内容を確認"
        rows.append(
            {
                "page": index + 1,
                "source": source or "",
                "target": target or "",
                "status": status,
                "uncertain": source is None or target is None,
            }
        )
    return rows


def note_shapes(document, page):
    notes = document.get("speaker_notes", [])
    text = notes[page - 1] if page <= len(notes) else ""
    if not text:
        return []
    return [
        {
            "id": "speaker-notes",
            "kind": "notes",
            "name": "発表者ノート",
            "text": text,
            "box": None,
            "analysis_incomplete": False,
        }
    ]
