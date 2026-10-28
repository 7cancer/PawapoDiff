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
            for index, change in enumerate(page["changes"]):
                item = change[side]
                if item:
                    x, y, w, h = item["box"]
                    category = change.get("highlight", "other")
                    if category not in PALETTE:
                        category = "other"
                    extra_class = " text-marker" if change["kind"] == "text" else ""
                    markers.append(
                        f'<button type="button" class="box {category}{extra_class}" data-change="p{page["page"]}-c{index}" aria-label="変更 {index + 1}: {esc(item["name"])}" title="{index + 1}. {esc(" / ".join(change["fields"]))}" style="left:{100 * x / result["width"]}%;top:{100 * y / result["height"]}%;width:{100 * w / result["width"]}%;height:{100 * h / result["height"]}%"><span class="mark-number">{index + 1}</span></button>'
                    )
            pictures.append(
                f'<div class="slide"><img alt="{side} p.{page["page"]}" src="data:image/png;base64,{data}">{"".join(markers)}</div>'
            )
        items = "".join(
            f'<li tabindex="0" data-change="p{page["page"]}-c{index}"><strong>{index + 1}.</strong> {esc((c["left"] or c["right"])["name"])}: {esc(" / ".join(c["fields"]))}{"（対応要確認）" if c["uncertain"] else ""}</li>'
            for index, c in enumerate(page["changes"])
        )
        sections.append(
            f'<section id="page-{page["page"]}"><h2>ページ {page["page"]}</h2><div class="pair">{"".join(pictures)}</div><ol class="changes">{items}</ol></section>'
        )
    issues = []
    for issue in result["issues"]:
        entries = issue.get("entries", [issue])
        text = " / ".join(f"p.{e['page']}: {e['target'] or '（空）'}" for e in entries)
        issues.append(
            f"<li>{esc(issue['kind'])} — {esc(issue['source'])} → {esc(text)}</li>"
        )
    warnings = [
        f"<li>{'日本語原本' if warning['side'] == 'left' else '英訳版'} p.{warning['page']} / 図形ID {esc(warning['shape_id'])}: {esc(warning['message'])}</li>"
        for warning in result.get("warnings", [])
    ]
    warning_html = (
        "<h2>解析が一部不完全です</h2><p>以下の要素は画像で確認してください。比較・翻訳チェックで未検出の可能性があります。</p><ul>"
        + "".join(warnings)
        + "</ul>"
        if warnings
        else ""
    )
    notices = [
        f"<li>{'日本語原本' if notice['side'] == 'left' else '英訳版'} {'p.' + str(notice['page']) if notice['page'] else '共通パーツ'} / 図形ID {esc(notice['shape_id'])}: {esc(notice['message'])}</li>"
        for notice in result.get("notices", [])
    ]
    notice_html = (
        "<h2>動画・音声は静止画として比較します</h2><p>動画本体の差分は判定しません。再生・外部参照・参照回復は行わず、元のファイルも変更しません。</p><ul>"
        + "".join(notices)
        + "</ul>"
        if notices
        else ""
    )
    rows = []
    for row in result.get("translations", []):
        css = "inconsistent" if row["inconsistent"] else ""
        page_links = " ".join(
            f'<a href="#page-{page}">p.{page}</a>'
            for page in row.get("pages", [row["page"]])
        )
        locations = "<br>".join(
            f"p.{entry['page']} / {esc(entry['shape'])} / {entry.get('source_line', 1)}行目"
            for entry in row.get("entries", [row])
        )
        variants = []
        for variant in row.get(
            "variants", [{"target": row["target"], "entries": [row]}]
        ):
            references = " ".join(
                f'<a href="#page-{page}">p.{page}</a>'
                for page in sorted({entry["page"] for entry in variant["entries"]})
            )
            variants.append(
                f'<div class="variant">{esc(variant["target"] or "（訳文なし）")}<br><small>{references}</small></div>'
            )
        rows.append(
            f'<tr class="{css}"><td>{page_links}<details><summary>要素と行を表示</summary>{locations}</details></td><td>{esc(row["source"])}</td><td>{"".join(variants)}</td><td>{esc(" / ".join(row["notes"]) or "—")}</td></tr>'
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
        + "button.box{padding:0;border-radius:0;min-width:6px;min-height:6px;cursor:pointer}.text-marker{visibility:hidden}.text-marker.active{visibility:visible}.mark-number{position:absolute;left:-2px;top:-2px;background:#243249;color:white;padding:2px 5px;font:10px sans-serif}.box.active .mark-number{z-index:4;background:#0891b2}.box.active{outline:3px solid #0891b2;outline-offset:2px;background:#22d3ee35}.changes{list-style:none;max-height:320px;overflow:auto;padding:0}.changes li{padding:10px;border-left:4px solid transparent}.changes li.active{background:#e0f7fa;border-left-color:#0891b2}.hide-marks .box{display:none}.variant+.variant{border-top:1px dashed #ccd;margin-top:10px;padding-top:10px}td details{font-size:12px}a{color:#126e77}.controls{position:sticky;top:0;z-index:5;background:white;padding:12px;border:1px solid #ccd}"
        + palette_css
        + "</style><h1>PawapoDiff 比較結果</h1><p>左：日本語原本 / 右：英訳版。翻訳検査は確認候補であり、誤訳の意味判定は行いません。画像内文字・SmartArt・グラフ内部は目視で確認してください。</p>"
        + '<div class="controls"><label><input id="marks-toggle" type="checkbox" checked> ハイライト表示 ON/OFF</label> ／ 図の番号または変更一覧にカーソルを合わせると対応箇所を強調します。</div>'
        + legend
        + warning_html
        + notice_html
        + "<h2>翻訳漏れ・表記揺れチェック</h2><ul>"
        + ("".join(issues) or "<li>候補はありません。</li>")
        + "</ul>"
        + "<h2>日英比較表</h2><p>改行ごとに分割し、原文側が2文字以下の行は省略します。同じ原文を全ページから1行にまとめ、英訳ごとに出現ページを表示します。行の対応が不明な場合は要確認と表示します。</p><table><thead><tr><th>ページ・要素</th><th>日本語原文</th><th>英訳</th><th>注釈</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + "".join(sections)
        + r"""<script>
(() => {
  const toggle = document.getElementById('marks-toggle');
  function clear() { document.querySelectorAll('[data-change].active').forEach(el => el.classList.remove('active')); }
  function activate(event) {
    const node = event.target.closest('[data-change]');
    if (!node || !toggle.checked) return;
    clear();
    document.querySelectorAll('[data-change]').forEach(el => { if (el.dataset.change === node.dataset.change) el.classList.add('active'); });
    if (node.classList.contains('box')) {
      const entry = document.querySelector('li[data-change="' + node.dataset.change + '"]');
      if (entry) {
        const list = entry.parentElement;
        const offset = entry.getBoundingClientRect().top - list.getBoundingClientRect().top;
        if (offset < 0 || offset + entry.offsetHeight > list.clientHeight) list.scrollTop += offset;
      }
    }
  }
  document.addEventListener('mouseover', activate);
  document.addEventListener('focusin', activate);
  document.addEventListener('mouseout', event => {
    const from = event.target.closest('[data-change]');
    if (from && (!event.relatedTarget || !from.contains(event.relatedTarget))) clear();
  });
  document.addEventListener('focusout', clear);
  toggle.addEventListener('change', () => { clear(); document.documentElement.classList.toggle('hide-marks', !toggle.checked); });
})();
</script></html>"""
    )
