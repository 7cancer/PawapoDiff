from pptx import Presentation
from pptx.util import Inches
from pawapodiff.consistency import check_consistency
from pawapodiff.engine import inspect, compare
from test_comparison_table import shape, document


def test_cross_page_variants_short_labels_and_missing():
    source = document(
        [[shape("a", "売上")], [shape("a", "売上")], [shape("a", "売上")]]
    )
    target = document([[shape("a", "Sales")], [shape("a", "Revenue")], []])
    row = check_consistency(source, target)["rows"][0]
    assert row["source"] == "売上" and row["inconsistent"] and row["missing"]
    assert [e["page"] for e in row["entries"]] == [1, 2, 3]


def test_single_page_duplicates_are_not_cross_page_matches():
    doc = document([[shape("a", "同じ文章"), shape("b", "同じ文章")]])
    assert not check_consistency(doc, doc)["rows"]


def test_english_wrap_and_line_endings_are_ignored():
    source = document([[shape("a", "同じ文章")], [shape("a", "同じ文章")]])
    target = document([[shape("a", "Same\r\nEnglish")], [shape("a", "Same\vEnglish")]])
    assert not check_consistency(source, target)["rows"][0]["inconsistent"]


def test_repeated_multiline_block_is_compared_without_line_alignment():
    source = document(
        [[shape("a", "最初の文章\n次の文章")], [shape("a", "最初の文章\r\n次の文章")]]
    )
    target = document(
        [
            [shape("a", "First English\nSecond English")],
            [shape("a", "First\nEnglish Second English")],
        ]
    )
    rows = compare(source, target)["translations"]
    assert len(rows) == 1 and not rows[0]["inconsistent"]


def test_deleted_placeholder_pasted_text_is_recovered(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    original = slide.placeholders[1]
    original.text = "同じ日本語"
    path = tmp_path / "original.pptx"
    prs.save(path)
    left = inspect(path)
    dimensions = (original.left, original.top, original.width, original.height)
    slide.shapes._spTree.remove(original._element)
    slide.shapes.add_textbox(*dimensions).text = "English translation"
    path = tmp_path / "target.pptx"
    prs.save(path)
    result = compare(left, inspect(path))
    row = result["translations"][0]
    assert row["target"] == "English translation" and row["uncertain"]
    assert not any(issue["kind"] == "訳抜け候補" for issue in result["issues"])
    assert any(
        "プレースホルダー削除" in change["fields"]
        for change in result["pages"][0]["changes"]
    )


def test_theme_font_is_resolved_and_explicit_equivalent_is_silent(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text = "同じ文章"
    path = tmp_path / "source.pptx"
    prs.save(path)
    left = inspect(path)
    name = left["slides"][0][0]["style"]["run_attributes"]["font"][0][0]
    assert name and not name.startswith("+")
    box.text_frame.paragraphs[0].runs[0].font.name = name
    target = tmp_path / "target.pptx"
    prs.save(target)
    assert not compare(left, inspect(target))["pages"][0]["changes"]


def test_layout_inherited_font_change_reports_actual_names(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.placeholders[1].text = "同じ文章"
    paragraph = slide.slide_layout.placeholders[1].text_frame.paragraphs[0]
    paragraph.font.name = "Arial"
    source = tmp_path / "source.pptx"
    prs.save(source)
    left = inspect(source)
    paragraph.font.name = "Calibri"
    target = tmp_path / "target.pptx"
    prs.save(target)
    fields = [
        field
        for change in compare(left, inspect(target))["pages"][0]["changes"]
        for field in change["fields"]
    ]
    assert any("Arial → Calibri" in field for field in fields)
    assert not any("指定なし" in field for field in fields)


def test_distant_same_kind_shape_does_not_steal_pasted_translation():
    from pawapodiff.engine import pair_text_shapes

    placeholder = shape("placeholder", "日本語の原文")
    placeholder.update(is_placeholder=True, placeholder={"idx": 1, "type": "BODY"})
    other = shape("other", "別の文章")
    other["box"] = [80, 80, 10, 10]
    pasted = shape("pasted", "English")
    pairs = pair_text_shapes([other, placeholder], [pasted])
    assert next(b for a, b, _ in pairs if a and a["id"] == "placeholder") == pasted
    assert next(b for a, b, _ in pairs if a and a["id"] == "other") is None
