"""Run on Windows with desktop PowerPoint. Produces no repository artifacts."""

import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pptx import Presentation
from pptx.util import Inches, Pt
from PIL import Image
from pawapodiff.engine import inspect, compare
from pawapodiff.render import render, export_images
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
        for preset, size in [
            ("HD", (1280, 720)),
            ("FHD", (1920, 1080)),
            ("4K", (3840, 2160)),
        ]:
            paths = export_images(images["left"], root / preset, preset)
            assert len(paths) == 2
            with Image.open(paths[0]) as im:
                assert im.size == size, (preset, im.size)
        paths = export_images(
            images["left"], root / "crop", "custom", [0, 0, 1, 0.8], 1920
        )
        with Image.open(paths[0]) as im:
            assert im.size == (1920, 864)
        html = report(result, images)
        assert html.count("data:image/png;base64,") == 4
        print(
            "PASS: PowerPoint rendering, comparison, PNG resolutions/crop, embedded HTML."
        )
        print(
            "Packaged EXE and existing PowerPoint process behavior still need the README manual checks."
        )


if __name__ == "__main__":
    main()
