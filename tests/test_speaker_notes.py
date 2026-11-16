from hashlib import sha256
from pptx import Presentation
from pptx.util import Inches
from PIL import Image
from pawapodiff.engine import inspect, compare
from pawapodiff.consistency import check_consistency
from pawapodiff.report import report


def deck(path, notes):
    presentation = Presentation()
    for note in notes:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text = (
            "同じ原文"
        )
        if note is not None:
            slide.notes_slide.notes_text_frame.text = note
            # Slide-number placeholder text must not leak into speaker notes.
            for shape in slide.notes_slide.placeholders:
                if shape.placeholder_format.type == 13:
                    shape.text = "999"
    presentation.save(path)


def test_notes_are_read_without_modifying_source_or_creating_parts(tmp_path):
    path = tmp_path / "notes.pptx"
    deck(path, [None, "説明の文章\n次の説明"])
    digest = sha256(path.read_bytes()).digest()
    doc = inspect(path)
    assert doc["speaker_notes"] == ["", "説明の文章\n次の説明"]
    assert sha256(path.read_bytes()).digest() == digest
    assert not Presentation(path).slides[0].has_notes_slide


def test_note_variants_do_not_mix_with_slide_text(tmp_path):
    a, b = tmp_path / "ja.pptx", tmp_path / "en.pptx"
    deck(a, ["同じ原文", "同じ原文", "訳がないノート"])
    deck(b, ["Explanation", "Different wording", None])
    result = check_consistency(inspect(a), inspect(b))
    matching = [r for r in result["rows"] if r["source"] == "同じ原文"]
    assert len(matching) == 2
    assert next(r for r in matching if r["scope"] == "notes")["inconsistent"]
    assert not next(r for r in matching if r["scope"] == "slide")["inconsistent"]
    assert result["speaker_notes"][2]["status"] == "英語なし"


def test_page_notes_include_target_only_and_ignore_break_codes(tmp_path):
    a, b = tmp_path / "ja.pptx", tmp_path / "en.pptx"
    deck(a, [None, "Same\nnotes", "残っている文章"])
    deck(b, ["Extra note", "Same\vnotes", "残っている文章"])
    result = compare(inspect(a), inspect(b))
    assert [row["status"] for row in result["speaker_notes"]] == [
        "日本語なし",
        "同文",
        "日本語残存候補",
    ]


def test_notes_are_downloaded_instead_of_embedded_in_report(tmp_path):
    a, b = tmp_path / "ja.pptx", tmp_path / "en.pptx"
    deck(a, ["説明 <script>alert(1)</script>"])
    deck(b, ["English <b>note</b>"])
    result = compare(inspect(a), inspect(b))
    image = tmp_path / "image.png"
    Image.new("RGB", (100, 100)).save(image)
    output = report(result, {"left": [image], "right": [image]})
    assert "発表者ノート・日英比較" not in output
    assert "English &lt;b&gt;note&lt;/b&gt;" not in output
    assert "<script>alert(1)</script>" not in output


def test_notes_api_recalculation_and_export(tmp_path):
    import time
    from fastapi.testclient import TestClient
    from pawapodiff.server import create_app

    a, b = tmp_path / "ja.pptx", tmp_path / "en.pptx"
    deck(a, ["発表者への説明"])
    deck(b, ["Speaker instructions"])

    def renderer(path, dest):
        dest.mkdir()
        image = dest / "0001.png"
        Image.new("RGB", (100, 100)).save(image)
        return [image]

    with TestClient(
        create_app("notes-token", tmp_path / "work", renderer),
        base_url="http://127.0.0.1",
    ) as client:
        headers = {"X-Pawapo-Token": "notes-token"}
        response = client.post(
            "/api/jobs",
            headers=headers,
            files={
                "left": ("ja.pptx", a.read_bytes()),
                "right": ("en.pptx", b.read_bytes()),
            },
        )
        assert response.status_code == 200
        key = response.json()["id"]
        for _ in range(200):
            job = client.get(f"/api/jobs/{key}", headers=headers).json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        assert job["status"] == "done", job
        notes = job["result"]["speaker_notes"]
        assert notes[0]["source"] == "発表者への説明"
        assert notes[0]["target"] == "Speaker instructions"
        updated = client.post(
            f"/api/jobs/{key}/threshold", headers=headers, json={"threshold": 2}
        ).json()
        assert updated["speaker_notes"] == notes
        html = client.get(f"/api/jobs/{key}/report", headers=headers).text
        assert (
            "発表者ノート・日英比較" not in html and "Speaker instructions" not in html
        )
        response = client.get(f"/api/jobs/{key}/notes.csv", headers=headers)
        assert response.status_code == 200
        assert "Speaker instructions" in response.content.decode("utf-8-sig")
        assert "発表者への説明" in response.content.decode("utf-8-sig")
