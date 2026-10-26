import base64
import html
from pathlib import Path

PALETTE = {
    "format": ("#2563eb", "書式のみ"),
    "layout": ("#ea580c", "配置・サイズ・回転"),
    "mixed": ("#9333ea", "書式と配置"),
    "other": ("#64748b", "画像・追加削除・要確認"),
}


def report(result, images):
    esc = lambda value: html.escape(str(value))
    sections = []
    for page in result["pages"]:
        pictures = []
        for side in ("left", "right"):
            data = base64.b64encode(
                Path(images[side][page["page"] - 1]).read_bytes()
            ).decode("ascii")
            markers = []
            for change in page["changes"]:
                item = change[side]
                if item and change["kind"] == "structure":
                    x, y, w, h = item["box"]
                    category = change.get("highlight", "other")
                    if category not in PALETTE:
                        category = "other"
                    markers.append(
                        f'<span class="box {category}" title="{esc(" / ".join(change["fields"]))}" style="left:{100*x/result["width"]}%;top:{100*y/result["height"]}%;width:{100*w/result["width"]}%;height:{100*h/result["height"]}%"></span>'
                    )
            pictures.append(
                f'<div class="slide"><img alt="{side} p.{page["page"]}" src="data:image/png;base64,{data}">{"".join(markers)}</div>'
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
    rows = []
    for row in result.get("translations", []):
        css = "inconsistent" if row["inconsistent"] else ""
        rows.append(
            f'<tr class="{css}"><td>p.{row["page"]}<br>{esc(row["shape"])}</td><td>{esc(row["source"] or "（原文なし）")}</td><td>{esc(row["target"] or "（訳文なし）")}</td><td>{esc(" / ".join(row["notes"]) or "—")}</td></tr>'
        )
    palette_css = "".join(
        f".box.{key}{{border-color:{color};background:{color}18}}.legend .{key}{{border-left:5px solid {color};padding-left:6px}}"
        for key, (color, label) in PALETTE.items()
    )
    legend = (
        '<div class="legend">'
        + "".join(
            f'<span class="{key}">{label}</span>'
            for key, (color, label) in PALETTE.items()
        )
        + "</div>"
    )
    return (
        '<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PawapoDiff 比較結果</title><style>'
        "body{font:15px sans-serif;margin:32px;color:#17243a}section{break-inside:avoid;border-top:1px solid #ccd;padding:20px 0}.pair{display:flex;gap:12px}.slide{position:relative;width:50%}img{width:100%;display:block}.box{position:absolute;box-sizing:border-box;border:2px solid}li{margin:8px}.legend{display:flex;gap:20px;flex-wrap:wrap;margin:20px 0}table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{border:1px solid #dce3ec;padding:10px;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#f1f5f9}th:first-child{width:16%}.inconsistent{background:#fff8e8}"
        + palette_css
        + "</style><h1>PawapoDiff 比較結果</h1><p>左：日本語原本 / 右：英訳版。翻訳検査は確認候補であり、誤訳の意味判定は行いません。画像内文字・SmartArt・グラフ内部は目視で確認してください。</p>"
        + legend
        + "<h2>翻訳漏れ・表記揺れチェック</h2><ul>"
        + ("".join(issues) or "<li>候補はありません。</li>")
        + "</ul>"
        + "<h2>全文章の日英比較表</h2><p>全ページのテキストボックス・表セル単位で表示します。</p><table><thead><tr><th>ページ・要素</th><th>日本語原文</th><th>英訳</th><th>注釈</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + "".join(sections)
        + "</html>"
    )
