"""Create bilingual PPTX samples with deliberate slide and note review cases."""

import argparse
from pathlib import Path
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt


def create_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    notes_ja = [
        "売上高は前年同期比で増加しました。",
        "売上高は前年同期比で増加しました。",
        "次の四半期も品質の改善を継続します。",
        "質疑応答の時間です。",
    ]
    notes_en = [
        "Revenue grew year over year.",
        "Sales increased year on year.",
        "",
        "質疑応答の時間です。",
    ]
    outputs = []
    for language in ("ja", "en"):
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        for index in range(4):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor.from_string("F5F7FB")
            title = slide.shapes.title
            title.text = "業績ハイライト" if language == "ja" else "Business highlights"
            title.left, title.top, title.width, title.height = (
                Inches(0.6),
                Inches(0.5),
                Inches(12),
                Inches(0.8),
            )
            body = slide.placeholders[1]
            body.left, body.top, body.width, body.height = (
                Inches(0.8),
                Inches(2),
                Inches(5),
                Inches(1),
            )
            body.text = (
                "売上高"
                if language == "ja"
                else ["Sales", "Revenue", "Sales", "Sales"][index]
            )
            if language == "en" and index == 1:
                dimensions = (body.left, body.top, body.width, body.height)
                slide.shapes._spTree.remove(body._element)
                body = slide.shapes.add_textbox(*dimensions)
                body.text = "Revenue"
                body.name = "貼り直した英語テキスト"
            profit = slide.shapes.add_textbox(
                Inches(0.8), Inches(3.5), Inches(5), Inches(1.2)
            )
            profit.text = (
                "営業利益"
                if language == "ja"
                else "Operating\vincome" if index == 2 else "Operating income"
            )
            value = slide.shapes.add_textbox(Inches(7), Inches(2), Inches(5), Inches(2))
            value.text = (
                f"{120 + index * 15} 億円"
                if language == "ja"
                else f"JPY {12 + index * 1.5:g} billion"
            )
            footer = slide.shapes.add_textbox(
                Inches(0.8), Inches(6.5), Inches(11.7), Inches(0.4)
            )
            footer.text = (
                f"比較デモ / {index + 1}ページ"
                if language == "ja"
                else f"Comparison demo / page {index + 1}"
            )
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            run.font.name = "Yu Gothic"
                            run.font.size = Pt(28 if shape is title else 22)
                            run.font.color.rgb = RGBColor.from_string("18324F")
            slide.notes_slide.notes_text_frame.text = (
                notes_ja if language == "ja" else notes_en
            )[index]
        output = directory / f"demo-{language}.pptx"
        prs.save(output)
        outputs.append(output)
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default="demo")
    for path in create_demo(parser.parse_args().directory):
        print(path)
