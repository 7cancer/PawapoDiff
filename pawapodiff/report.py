import base64
import html
from pathlib import Path


def report(result, images):
    esc = lambda value: html.escape(str(value))
    sections = []
    for page in result["pages"]:
        pictures = []
        for side in ("left", "right"):
            path = images[side][page["page"] - 1]
            data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            markers = []
            for change in page["changes"]:
                item = change[side]
                if item and change["kind"] == "structure":
                    x, y, w, h = item["box"]
                    markers.append(
                        f'<span class="box" style="left:{100*x/result["width"]}%;top:{100*y/result["height"]}%;width:{100*w/result["width"]}%;height:{100*h/result["height"]}%"></span>'
                    )
            pictures.append(
                f'<div class="slide"><img src="data:image/png;base64,{data}">{"".join(markers)}</div>'
            )
        items = "".join(
            f'<li>{esc((c["left"] or c["right"])["name"])}: {esc(" / ".join(c["fields"]))}{"（対応要確認）" if c["uncertain"] else ""}</li>'
            for c in page["changes"]
        )
        sections.append(
            f'<section><h2>ページ {page["page"]}</h2><div class="pair">{"".join(pictures)}</div><ul>{items}</ul></section>'
        )
    issues = []
    for issue in result["issues"]:
        entries = issue.get("entries", [issue])
        text = " / ".join(f'p.{e["page"]}: {e["target"] or "（空）"}' for e in entries)
        issues.append(
            f'<li>{esc(issue["kind"])} — {esc(issue["source"])} → {esc(text)}</li>'
        )
    return (
        '<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PawapoDiff 比較結果</title><style>body{font:15px sans-serif;margin:32px;color:#17243a}section{break-inside:avoid;border-top:1px solid #ccd;padding:20px 0}.pair{display:flex;gap:12px}.slide{position:relative;width:50%}img{width:100%;display:block}.box{position:absolute;box-sizing:border-box;border:2px solid #e95833;background:#ff9e3022}li{margin:8px}</style><h1>PawapoDiff 比較結果</h1><p>左：原本 / 右：英訳版。翻訳検査は確認候補であり、誤訳の意味判定は行っていません。画像内文字・SmartArt・グラフ内部は目視で確認してください。</p><h2>翻訳検査</h2><ul>'
        + "".join(issues)
        + "</ul>"
        + "".join(sections)
        + "</html>"
    )
