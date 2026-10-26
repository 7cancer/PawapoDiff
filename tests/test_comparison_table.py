from copy import deepcopy
from pawapodiff.engine import compare
from pawapodiff.report import report, PALETTE
from PIL import Image


def shape(id, text="", kind="text"):
    return {
        "id": id,
        "kind": kind,
        "name": id,
        "text": text,
        "box": [10, 20, 30, 40],
        "rotation": 0,
        "style": None,
        "image": None,
    }


def document(slides):
    return {"width": 100, "height": 100, "slides": slides}


def test_all_texts_include_unchanged_missing_deleted_added_and_variants():
    left = document(
        [
            [
                shape("a", " 売上高\n"),
                shape("b", "変化なし"),
                shape("c", "削除文章"),
                shape("d", "訳抜け"),
            ],
            [shape("a", "売上高")],
        ]
    )
    right = document(
        [
            [
                shape("a", "Sales"),
                shape("b", "変化なし"),
                shape("d", ""),
                shape("extra", "Added only", "cell"),
            ],
            [shape("a", "Revenue")],
        ]
    )
    result = compare(left, right)
    rows = result["translations"]
    assert len(rows) == 6
    repeated = [r for r in rows if "売上高" in r["source"]]
    assert len(repeated) == 2 and all(r["inconsistent"] for r in repeated)
    assert all("表記揺らぎの可能性あり" in r["notes"] for r in repeated)
    assert repeated[0]["source"] == " 売上高\n"
    assert any(r["source"] == "変化なし" and r["target"] == "変化なし" for r in rows)
    assert any(
        r["source"] == "削除文章" and r["target"] == "" and r["uncertain"] for r in rows
    )
    assert any(r["source"] == "" and r["target"] == "Added only" for r in rows)
    assert any(r["source"] == "訳抜け" and "訳抜け候補" in r["notes"] for r in rows)


def test_whitespace_only_english_difference_is_not_variation():
    result = compare(
        document([[shape("a", "同じ日本語")], [shape("a", "同じ日本語")]]),
        document([[shape("a", " Same  English ")], [shape("a", "Same\nEnglish")]]),
    )
    assert not any(r["inconsistent"] for r in result["translations"])


def test_highlight_categories_separate_style_layout_and_both():
    left = [
        shape("format", "原文"),
        shape("layout", "原文"),
        shape("mixed", "原文"),
        shape("image", ""),
    ]
    right = deepcopy(left)
    right[0]["style"] = {"bold": True}
    right[1]["box"][0] += 2
    right[2]["style"] = {"bold": True}
    right[2]["box"][1] += 3
    right[3]["image"] = "new-image"
    result = compare(document([left]), document([right]))
    changes = {
        c["left"]["id"]: c
        for c in result["pages"][0]["changes"]
        if c["kind"] == "structure"
    }
    assert {k: c["highlight"] for k, c in changes.items()} == {
        "format": "format",
        "layout": "layout",
        "mixed": "mixed",
        "image": "other",
    }
    below = compare(document([left]), document([right]), threshold=5)
    mixed = next(c for c in below["pages"][0]["changes"] if c["left"]["id"] == "mixed")
    assert mixed["highlight"] == "format"


def test_html_report_contains_same_table_annotations_and_colored_markers(tmp_path):
    left = document([[shape("a", "同じ日本語"), shape("b", "同じ日本語")]])
    right = deepcopy(left)
    right["slides"][0][0]["text"] = "One"
    right["slides"][0][1]["text"] = "Two"
    right["slides"][0][0]["style"] = {"bold": True}
    right["slides"][0][1]["box"][0] += 4
    image = tmp_path / "image.png"
    Image.new("RGB", (100, 100)).save(image)
    content = report(compare(left, right), {"left": [image], "right": [image]})
    assert "box format" in content and "box layout" in content
    assert (
        content.index("翻訳漏れ・表記揺れチェック")
        < content.index("全文章の日英比較表")
        < content.index("ページ 1")
    )
    assert content.count("表記揺らぎの可能性あり") == 2
    for color, label in PALETTE.values():
        assert color in content and label in content
