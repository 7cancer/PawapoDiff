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
    assert len(rows) == 4
    repeated = [r for r in rows if "売上高" in r["source"]]
    assert len(repeated) == 1 and repeated[0]["inconsistent"]
    assert repeated[0]["pages"] == [1, 2]
    assert {v["target"] for v in repeated[0]["variants"]} == {"Sales", "Revenue"}
    assert len(repeated[0]["entries"]) == 2
    assert all("表記揺らぎの可能性あり" in r["notes"] for r in repeated)
    assert repeated[0]["source"] == "売上高"
    assert any(r["source"] == "変化なし" and r["target"] == "変化なし" for r in rows)
    assert any(
        r["source"] == "削除文章" and r["target"] == "" and r["uncertain"] for r in rows
    )
    assert not any(r["source"] == "" for r in rows)
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
    assert "翻訳漏れ・表記揺れチェック" not in content
    assert content.index("日英比較表") < content.index("ページ 1")
    assert content.count("表記揺らぎの可能性あり") == 1
    for color, label in PALETTE.values():
        assert color in content and label in content


def test_lines_are_independent_grouped_across_pages_and_short_source_is_table_only():
    left = document(
        [
            [shape("box", "売上高\n利益率\v短文\nあ\n日 本\n長い文章")],
            [shape("cell", " 売上高\n別の文章", "cell")],
        ]
    )
    right = document(
        [
            [shape("box", "Sales\nMargin\vShort\nA\nJapan\nLong text")],
            [shape("cell", "Revenue\nAnother sentence", "cell")],
        ]
    )
    rows = compare(left, right)["translations"]
    assert {row["source"] for row in rows} == {
        "売上高",
        "利益率",
        "長い文章",
        "別の文章",
    }
    sales = next(row for row in rows if row["source"] == "売上高")
    assert sales["pages"] == [1, 2] and sales["inconsistent"]
    assert {
        v["target"]: [e["page"] for e in v["entries"]] for v in sales["variants"]
    } == {"Sales": [1], "Revenue": [2]}
    assert {e["source_line"] for e in sales["entries"]} == {1}
    long = next(row for row in rows if row["source"] == "長い文章")
    assert long["variants"][0]["target"] == "Long text"
    assert long["entries"][0]["source_line"] == 6


def test_short_missing_text_still_checked_and_empty_target_not_called_variant():
    left = document(
        [[shape("a", "短文"), shape("b", "長い原文")], [shape("b", "長い原文")]]
    )
    right = document([[shape("a", ""), shape("b", "")], [shape("b", "English")]])
    result = compare(left, right)
    assert len(result["translations"]) == 1
    row = result["translations"][0]
    assert not row["inconsistent"]
    assert {variant["target"] for variant in row["variants"]} == {"", "English"}
    assert any(
        issue["source"] == "短文" and issue["kind"] == "訳抜け候補"
        for issue in result["issues"]
    )


def test_mismatched_line_counts_do_not_guess_translation_alignment():
    result = compare(
        document([[shape("a", "最初の文章\n次の文章")]]),
        document([[shape("a", "One combined translation")]]),
    )
    rows = result["translations"]
    assert len(rows) == 2
    assert {row["target"] for row in rows} == {"One combined translation"}
    assert all(
        row["uncertain"] and any("改行数が異なる" in note for note in row["notes"])
        for row in rows
    )


def test_blank_lines_keep_corresponding_line_numbers_and_duplicate_occurrences():
    result = compare(
        document([[shape("a", "同じ文章\r\n\r\n同じ文章")]]),
        document([[shape("a", "Same\n\nSame")]]),
    )
    row = result["translations"][0]
    assert len(result["translations"]) == 1
    assert not row["inconsistent"] and not row["uncertain"]
    assert [entry["source_line"] for entry in row["entries"]] == [1, 3]
    assert len(row["variants"]) == 1 and len(row["variants"][0]["entries"]) == 2


def test_leading_blank_lines_preserve_source_line_number():
    row = compare(
        document([[shape("a", "\n\n原文の文章\n")]]),
        document([[shape("a", "English")]]),
    )["translations"][0]
    assert row["entries"][0]["source_line"] == 3 and not row["uncertain"]
