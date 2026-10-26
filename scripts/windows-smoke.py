"""Run on Windows with desktop PowerPoint. Produces no repository artifacts."""

import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pptx import Presentation
from pptx.util import Inches, Pt
from pawapodiff.engine import inspect, compare
from pawapodiff.render import render
from pawapodiff.report import report


def main():
    if sys.platform != "win32":
        raise SystemExit(
            "This acceptance check requires Windows and Microsoft PowerPoint."
        )
    with tempfile.TemporaryDirectory(prefix="PawapoDiff-acceptance-") as directory:
        root = Path(directory)
        for translated in (False, True):
            p = Presentation()
            p.slide_width = Inches(12)
            p.slide_height = Inches(6.75)
            for i in range(2):
                s = p.slides.add_slide(p.slide_layouts[6])
                t = s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
                t.text = ["Sales", "Revenue"][i] if translated else "売上高"
                t.text_frame.paragraphs[0].runs[0].font.size = Pt(24)
                if translated:
                    t.left += Pt(3)
            p.save(root / ("right.pptx" if translated else "left.pptx"))
        result = compare(inspect(root / "left.pptx"), inspect(root / "right.pptx"))
        images = {
            side: render(root / f"{side}.pptx", root / side)
            for side in ("left", "right")
        }
        assert all(len(paths) == 2 for paths in images.values())
        assert any(i["kind"] == "表記揺れ候補" for i in result["issues"])
        assert len(result["translations"]) == 2
        assert all(row["inconsistent"] for row in result["translations"])
        html = report(result, images)
        assert html.count("data:image/png;base64,") == 4
        print(
            "PASS: PowerPoint rendering, comparison, translation table, embedded HTML."
        )
        print(
            "Packaged EXE and existing PowerPoint process behavior still need the README manual checks."
        )


if __name__ == "__main__":
    main()
