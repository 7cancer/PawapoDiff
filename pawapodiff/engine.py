"""Read-only OOXML inspection; geometry is expressed in slide points."""

from collections import defaultdict
from io import BytesIO
from hashlib import sha256
import re
from zipfile import ZipFile, BadZipFile
from lxml import etree
from pptx import Presentation
from .limits import MAX_EXPANDED_BYTES, MAX_XML_PART_BYTES, MAX_XML_TOTAL_BYTES

EMU = 12700
JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


class InputError(ValueError):
    pass


def normalize(text):
    return " ".join(text.split())


def validate(path):
    try:
        with ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_EXPANDED_BYTES:
                raise InputError("展開後のサイズが60GBを超えています。")
            xml_parts = [
                i for i in z.infolist() if i.filename.endswith((".xml", ".rels"))
            ]
            if (
                any(i.file_size > MAX_XML_PART_BYTES for i in xml_parts)
                or sum(i.file_size for i in xml_parts) > MAX_XML_TOTAL_BYTES
            ):
                raise InputError(
                    "XML構造情報が上限（1パーツ64MB・合計512MB）を超えています。"
                )
            names = z.namelist()
            if "ppt/presentation.xml" not in names:
                raise InputError("PowerPointのpptxファイルではありません。")
            for name in names:
                if "vbaproject" in name.lower() or "activex" in name.lower():
                    raise InputError(
                        "マクロ・ActiveXを含むファイルには対応していません。"
                    )
                if name.endswith(".rels"):
                    root = etree.fromstring(
                        z.read(name),
                        etree.XMLParser(resolve_entities=False, no_network=True),
                    )
                    # External media/OLE are not opened. Hyperlinks are safe to render without following.
                    if any(
                        r.get("TargetMode") == "External"
                        and not r.get("Type", "").endswith("/hyperlink")
                        for r in root
                    ):
                        raise InputError(
                            "外部参照を含む資料です。PowerPointでリンクを解除して保存してください。"
                        )
    except BadZipFile as e:
        raise InputError(
            "破損ファイル、または暗号化されたファイルです。暗号化を解除したpptxを指定してください。"
        ) from e
    except etree.XMLSyntaxError as e:
        raise InputError("PowerPoint内部のXMLが破損しています。") from e


def color_value(color):
    if color.type is None:
        return None
    try:
        return str(color.rgb)
    except AttributeError:
        return f"theme:{color.theme_color}:{color.brightness}"


def format_xml(element):
    """Keep formatting and geometry, ignoring text content and proofing metadata."""
    if element is None:
        return ""
    copy = etree.fromstring(etree.tostring(element))
    for node in copy.iter():
        if etree.QName(node).localname in ("t", "fld"):
            node.text = ""
        for attr in list(node.attrib):
            if etree.QName(attr).localname in (
                "lang",
                "altLang",
                "dirty",
                "err",
                "smtClean",
                "smtId",
            ):
                del node.attrib[attr]
    return etree.tostring(copy).decode()


def inherited_signature(slide):
    parts = [
        format_xml(slide.slide_layout._element),
        format_xml(slide.slide_layout.slide_master._element),
    ]
    for rel in slide.slide_layout.slide_master.part.rels.values():
        if not rel.is_external and rel.reltype.endswith("/theme"):
            parts.append(sha256(rel.target_part.blob).hexdigest())
    return sha256("".join(parts).encode()).hexdigest()


def text_style(frame):
    paragraphs = []
    for p in frame.paragraphs:
        runs = []
        for r in p.runs:
            if not r.text.strip():
                continue
            f = r.font
            style = (
                f.name,
                f.size.pt if f.size else None,
                f.bold,
                f.italic,
                color_value(f.color),
            )
            # Compare transitions, not translated run lengths or word counts.
            if not runs or runs[-1] != style:
                runs.append(style)
        paragraphs.append(
            {
                "alignment": str(p.alignment),
                "level": p.level,
                "runs": runs,
                "defaults": format_xml(p._p.pPr),
            }
        )
    # Paragraph count may naturally change on translation; preserve distinct style patterns.
    distinct = []
    for p in paragraphs:
        if p not in distinct:
            distinct.append(p)
    return {
        "patterns": distinct,
        "anchor": str(frame.vertical_anchor),
        "margins": [
            getattr(frame, "margin_" + side) / EMU
            for side in ("left", "top", "right", "bottom")
        ],
        "wrap": frame.word_wrap,
        "body": format_xml(frame._txBody.bodyPr),
    }


def inspect(path):
    validate(path)
    source_media = None
    try:
        # python-pptx eagerly loads every binary part. Give it a metadata-only
        # package, and hash the original media in bounded chunks when needed.
        metadata = BytesIO()
        with ZipFile(path) as source, ZipFile(metadata, "w") as target:
            for info in source.infolist():
                target.writestr(
                    info.filename,
                    (
                        source.read(info.filename)
                        if info.filename.endswith((".xml", ".rels"))
                        else b""
                    ),
                )
        metadata.seek(0)
        prs = Presentation(metadata)
        source_media = ZipFile(path)
        image_hashes = {}
        slides = []
        inherited = []

        def walk(shapes, prefix="", transform=(1, 1, 0, 0)):
            result = []
            sx, sy, ox, oy = transform
            for sh in shapes:
                key = f"{prefix}{sh.shape_id}"
                box = [
                    sh.left / EMU * sx + ox,
                    sh.top / EMU * sy + oy,
                    sh.width / EMU * sx,
                    sh.height / EMU * sy,
                ]
                if sh.shape_type == 6:
                    x = sh._element.grpSpPr.xfrm
                    gx = sh.width / x.chExt.cx if x.chExt.cx else 1
                    gy = sh.height / x.chExt.cy if x.chExt.cy else 1
                    result.append(
                        {
                            "id": key,
                            "kind": "group",
                            "name": sh.name,
                            "box": box,
                            "rotation": sh.rotation,
                            "text": "",
                            "style": None,
                            "image": None,
                        }
                    )
                    result.extend(
                        walk(
                            sh.shapes,
                            key + "/",
                            (
                                sx * gx,
                                sy * gy,
                                box[0] - x.chOff.x / EMU * sx * gx,
                                box[1] - x.chOff.y / EMU * sy * gy,
                            ),
                        )
                    )
                    continue
                item = {
                    "id": key,
                    "kind": str(sh.shape_type),
                    "name": sh.name,
                    "box": box,
                    "rotation": sh.rotation,
                    "text": "",
                    "style": None,
                    "image": None,
                }
                if sh.has_text_frame:
                    item.update(text=sh.text, style=text_style(sh.text_frame))
                if sh.shape_type == 13:
                    rid = sh._pic.blip_rId
                    name = str(sh.part.related_part(rid).partname).lstrip("/")
                    if name not in image_hashes:
                        digest = sha256()
                        with source_media.open(name) as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        image_hashes[name] = digest.hexdigest()
                    item["image"] = image_hashes[name]
                result.append(item)
                if sh.has_table:
                    table = sh.table
                    y = box[1]
                    for row_i, row in enumerate(table.rows):
                        x = box[0]
                        for col_i, cell in enumerate(row.cells):
                            w, h = (
                                table.columns[col_i].width / EMU * sx,
                                row.height / EMU * sy,
                            )
                            if not cell.is_spanned:
                                cw = (
                                    sum(
                                        table.columns[c].width
                                        for c in range(col_i, col_i + cell.span_width)
                                    )
                                    / EMU
                                    * sx
                                )
                                ch = (
                                    sum(
                                        table.rows[r].height
                                        for r in range(row_i, row_i + cell.span_height)
                                    )
                                    / EMU
                                    * sy
                                )
                                result.append(
                                    {
                                        "id": f"{key}/cell/{row_i}/{col_i}",
                                        "kind": "cell",
                                        "name": f"{sh.name} ({row_i+1},{col_i+1})",
                                        "box": [x, y, cw, ch],
                                        "rotation": 0,
                                        "text": cell.text,
                                        "style": text_style(cell.text_frame),
                                        "image": None,
                                    }
                                )
                            x += w
                        y += row.height / EMU * sy
            return result

        for slide in prs.slides:
            slides.append(walk(slide.shapes))
            inherited.append(inherited_signature(slide))
        return {
            "width": prs.slide_width / EMU,
            "height": prs.slide_height / EMU,
            "slides": slides,
            "inherited": inherited,
        }
    except InputError:
        raise
    except Exception as e:
        raise InputError(
            "スライドを解析できません。PowerPointで開き直してpptxとして保存してください。"
        ) from e
    finally:
        if source_media is not None:
            source_media.close()


def pair_shapes(left, right):
    available = {b["id"]: b for b in right}
    pairs, remaining = [], []
    for a in left:
        b = available.get(a["id"])
        if b and a["kind"] == b["kind"]:
            pairs.append((a, available.pop(a["id"]), False))
        else:
            remaining.append(a)
    for a in remaining:
        candidates = [b for b in available.values() if b["kind"] == a["kind"]]
        b = min(
            candidates,
            key=lambda b: sum(abs(x - y) for x, y in zip(a["box"], b["box"])),
            default=None,
        )
        if b:
            available.pop(b["id"])
        pairs.append((a, b, True))
    pairs.extend((None, b, True) for b in available.values())
    return pairs


def compare(left, right, threshold=1):
    if len(left["slides"]) != len(right["slides"]):
        raise InputError(
            "ページ数が一致しません。ページ数が同じ資料を指定してください。"
        )
    if (left["width"], left["height"]) != (right["width"], right["height"]):
        raise InputError("スライドの寸法が一致しません。")
    pages, groups, issues, rows = [], defaultdict(list), [], []
    for page, (aa, bb) in enumerate(zip(left["slides"], right["slides"]), 1):
        changes = []
        if (
            left.get("inherited", [None] * len(left["slides"]))[page - 1]
            != right.get("inherited", [None] * len(right["slides"]))[page - 1]
        ):
            marker = {
                "id": "inherited",
                "name": "テーマ・マスター・レイアウト",
                "box": [0, 0, left["width"], left["height"]],
            }
            changes.append(
                {
                    "kind": "structure",
                    "highlight": "other",
                    "fields": ["継承された書式・レイアウトの変更（要確認）"],
                    "left": marker,
                    "right": marker,
                    "uncertain": True,
                }
            )
        for a, b, uncertain in pair_shapes(aa, bb):
            source = a.get("text", "") if a else ""
            target = b.get("text", "") if b else ""
            source_key, target_key = normalize(source), normalize(target)
            if source_key or target_key:
                notes = []
                if uncertain:
                    notes.append("対応付け要確認")
                if not source_key:
                    notes.append("対応する原文なし")
                row = {
                    "page": page,
                    "shape": (a or b)["name"],
                    "source": source,
                    "target": target,
                    "uncertain": uncertain,
                    "inconsistent": False,
                    "notes": notes,
                }
                rows.append(row)
                if source_key:
                    groups[source_key].append(row)
                    if not target_key:
                        notes.append("訳抜け候補")
                        issues.append(dict(row, kind="訳抜け候補"))
                if target_key and JP.search(target_key):
                    notes.append("日本語の残存候補")
                    issues.append(dict(row, kind="日本語の残存候補"))
            fields = []
            delta = None
            layout = formatting = False
            if not a or not b:
                fields.append("要素の追加" if b else "要素の削除")
            else:
                delta = [round(y - x, 4) for x, y in zip(a["box"], b["box"])]
                for i, label in enumerate(["横位置", "縦位置", "幅", "高さ"]):
                    if abs(delta[i]) > 0.00001 and abs(delta[i]) >= threshold:
                        fields.append(f"{label}: {delta[i]:+.2f} pt")
                        layout = True
                if abs(a["rotation"] - b["rotation"]) > 0.001:
                    fields.append("回転")
                    layout = True
                if a["style"] != b["style"]:
                    fields.append("文字書式・配置")
                    formatting = True
                if a["image"] != b["image"]:
                    fields.append("画像")
                if source_key != target_key:
                    changes.append(
                        {
                            "kind": "text",
                            "fields": ["文字内容（翻訳を含む）"],
                            "left": a,
                            "right": b,
                            "uncertain": uncertain,
                        }
                    )
            if fields or uncertain:
                highlight = (
                    "mixed"
                    if layout and formatting
                    else "layout" if layout else "format" if formatting else "other"
                )
                changes.append(
                    {
                        "kind": "structure",
                        "highlight": highlight,
                        "fields": fields or ["対応付け要確認"],
                        "delta": delta,
                        "left": a,
                        "right": b,
                        "uncertain": uncertain,
                    }
                )
        pages.append({"page": page, "changes": changes})
    for source, entries in groups.items():
        if (
            JP.search(source)
            and len({normalize(e["target"]) for e in entries if normalize(e["target"])})
            > 1
        ):
            for entry in entries:
                entry["inconsistent"] = True
                entry["notes"].append("表記揺らぎの可能性あり")
            issues.append(
                {"kind": "表記揺れ候補", "source": source, "entries": entries}
            )
    return {
        "width": left["width"],
        "height": left["height"],
        "pages": pages,
        "issues": issues,
        "translations": rows,
        "threshold": threshold,
    }
