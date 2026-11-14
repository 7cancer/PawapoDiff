"""Describe explicit slide text formatting without comparing translated text."""

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
EMU = 12700
LABELS = {
    "bold": "太字（ボールド）",
    "size": "文字サイズ",
    "font": "フォント名",
    "font_ea": "フォント名（東アジア文字）",
    "font_cs": "フォント名（複合文字）",
    "italic": "斜体",
    "color": "文字色",
    "underline": "下線",
}


def unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def transitions(values):
    result = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def format_xml(element):
    if element is None:
        return ""
    copy = etree.fromstring(etree.tostring(element))
    for node in copy.iter():
        if not isinstance(node.tag, str):
            continue
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
    try:
        return etree.tostring(
            copy, method="c14n", exclusive=True, with_comments=False
        ).decode()
    except etree.C14NError:
        return etree.tostring(copy).decode()


def color_value(color):
    return None if color.type is None else format_xml(color._xFill)


def font_values(font):
    def typeface(script):
        node = font._rPr.find(f"{{{A}}}{script}")
        return node.get("typeface") if node is not None else None

    return {
        "font": font.name,
        "font_ea": typeface("ea"),
        "font_cs": typeface("cs"),
        "size": font.size.pt if font.size else None,
        "bold": font.bold,
        "italic": font.italic,
        "color": color_value(font.color),
        "underline": font.underline,
    }


def residual_xml(element, *, attributes=(), children=()):
    if element is None:
        return ""
    copy = etree.fromstring(etree.tostring(element))
    for attribute in attributes:
        copy.attrib.pop(attribute, None)
    for node in list(copy):
        if isinstance(node.tag, str) and etree.QName(node).localname in children:
            copy.remove(node)
    if not len(copy) and not copy.attrib and not (copy.text or "").strip():
        return ""
    return format_xml(copy)


def inherited_font(frame, paragraph, shape, slide):
    """Resolve nearest DrawingML defaults, layout/master and theme typefaces."""
    from pptx.text.text import Font

    defaults = {key: None for key in LABELS}
    defaults.update(bold=False, italic=False, underline=False)
    if slide is None or shape is None:
        return {key: None for key in LABELS}
    level = paragraph.level + 1
    nodes = []
    # The last node wins; start at presentation and progress to local defaults.
    presentation = slide.part.package.presentation_part._element
    nodes.extend(presentation.xpath(f"./p:defaultTextStyle/a:lvl{level}pPr/a:defRPr"))
    master = slide.slide_layout.slide_master
    placeholder_type = (
        str(shape.placeholder_format.type) if shape.is_placeholder else ""
    )
    category = (
        "titleStyle"
        if "TITLE" in placeholder_type
        else (
            "bodyStyle"
            if shape.is_placeholder
            and any(t in placeholder_type for t in ("BODY", "OBJECT", "SUBTITLE"))
            else "otherStyle"
        )
    )
    nodes.extend(
        master._element.xpath(f"./p:txStyles/p:{category}/a:lvl{level}pPr/a:defRPr")
    )
    inherited_shapes = []
    if shape.is_placeholder:
        layout_shape = next(
            (
                item
                for item in slide.slide_layout.placeholders
                if item.placeholder_format.idx == shape.placeholder_format.idx
            ),
            None,
        )
        master_shape = next(
            (
                item
                for item in master.placeholders
                if layout_shape is not None
                and item.placeholder_format.type == layout_shape.placeholder_format.type
            ),
            None,
        )
        inherited_shapes = [
            item for item in (master_shape, layout_shape) if item is not None
        ]
    font_ref = None
    for item in [*inherited_shapes, shape]:
        refs = item._element.xpath("./p:style/a:fontRef")
        if refs:
            font_ref = refs[0].get("idx")
        if item is shape:
            body = frame._txBody
        elif item.has_text_frame:
            body = item.text_frame._txBody
        else:
            continue
        nodes.extend(body.xpath(f"./a:lstStyle/a:lvl{level}pPr/a:defRPr"))
        if item is not shape:
            nodes.extend(body.xpath("./a:p/a:pPr/a:defRPr")[:1])
    nodes.extend(paragraph._p.xpath("./a:pPr/a:defRPr"))
    for node in nodes:
        for key, value in font_values(Font(node)).items():
            if value is not None:
                defaults[key] = value
    theme = next(
        (
            rel.target_part
            for rel in master.part.rels.values()
            if not rel.is_external and rel.reltype.endswith("/theme")
        ),
        None,
    )
    if theme is not None:
        root = etree.fromstring(theme.blob)

        def themed(token, script):
            family = "majorFont" if token.startswith("+mj") else "minorFont"
            group = root.find(f".//{{{A}}}fontScheme/{{{A}}}{family}")
            if group is None:
                return None
            leaf = group.find(f"{{{A}}}{script}")
            name = leaf.get("typeface") if leaf is not None else None
            if not name and script == "ea":
                supplemental = group.find(f"{{{A}}}font[@script='Jpan']")
                name = (
                    supplemental.get("typeface") if supplemental is not None else None
                )
            return name

        for key, script, suffix in (
            ("font", "latin", "lt"),
            ("font_ea", "ea", "ea"),
            ("font_cs", "cs", "cs"),
        ):
            value = defaults[key]
            if value is None:
                value = ("+mj-" if font_ref == "major" else "+mn-") + suffix
            if value.startswith(("+mj-", "+mn-")):
                defaults[key] = themed(value, script)
    return defaults


def text_style(frame, *, shape=None, slide=None):
    paragraphs = []
    for paragraph in frame.paragraphs:
        defaults = inherited_font(frame, paragraph, shape, slide)
        if shape is None or slide is None:
            defaults.update(
                {
                    key: value
                    for key, value in font_values(paragraph.font).items()
                    if value is not None
                }
            )
        runs = []
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            values = font_values(run.font)
            for key in ("font", "font_ea", "font_cs"):
                if (
                    values[key]
                    and values[key].startswith(("+mj-", "+mn-"))
                    and slide is not None
                ):
                    # Resolve the run's own major/minor theme selection.
                    master = slide.slide_layout.slide_master
                    theme = next(
                        (
                            rel.target_part
                            for rel in master.part.rels.values()
                            if not rel.is_external and rel.reltype.endswith("/theme")
                        ),
                        None,
                    )
                    if theme is not None:
                        root = etree.fromstring(theme.blob)
                        family = (
                            "majorFont"
                            if values[key].startswith("+mj-")
                            else "minorFont"
                        )
                        script = {"font": "latin", "font_ea": "ea", "font_cs": "cs"}[
                            key
                        ]
                        node = root.find(
                            f".//{{{A}}}fontScheme/{{{A}}}{family}/{{{A}}}{script}"
                        )
                        name = node.get("typeface") if node is not None else None
                        if not name and script == "ea":
                            node = root.find(
                                f".//{{{A}}}fontScheme/{{{A}}}{family}/{{{A}}}font[@script='Jpan']"
                            )
                            name = node.get("typeface") if node is not None else None
                        values[key] = name or defaults[key]
            runs.append(
                {
                    key: value if value is not None else defaults[key]
                    for key, value in values.items()
                }
            )
        paragraphs.append(
            {
                "runs": runs,
                "defaults": defaults,
                "alignment": str(paragraph.alignment),
                "level": paragraph.level,
                "other": residual_xml(
                    paragraph._p.pPr, attributes=("algn", "lvl"), children=("defRPr",)
                ),
            }
        )
    autofit = frame._txBody.bodyPr.find(f"{{{A}}}normAutofit")
    return {
        "run_attributes": {
            key: unique(
                transitions([run[key] for run in paragraph["runs"]])
                for paragraph in paragraphs
                if paragraph["runs"]
            )
            for key in LABELS
        },
        "default_attributes": {
            key: unique([paragraph["defaults"][key]] for paragraph in paragraphs)
            for key in LABELS
        },
        "joint": unique(
            transitions([list(run.values()) for run in paragraph["runs"]])
            for paragraph in paragraphs
            if paragraph["runs"]
        ),
        "alignment": unique(paragraph["alignment"] for paragraph in paragraphs),
        "level": unique(paragraph["level"] for paragraph in paragraphs),
        "paragraph_other": unique(paragraph["other"] for paragraph in paragraphs),
        "anchor": str(frame.vertical_anchor),
        "margins": [
            getattr(frame, "margin_" + side) / EMU
            for side in ("left", "top", "right", "bottom")
        ],
        "wrap": frame.word_wrap,
        "autofit": str(frame.auto_size),
        "font_scale": (
            float(autofit.get("fontScale", "100000")) / 1000
            if autofit is not None
            else None
        ),
        "line_reduction": (
            float(autofit.get("lnSpcReduction", "0")) / 1000
            if autofit is not None
            else None
        ),
        "body_other": residual_xml(
            frame._txBody.bodyPr,
            attributes=("anchor", "lIns", "tIns", "rIns", "bIns", "wrap"),
            children=("noAutofit", "normAutofit", "spAutoFit"),
        ),
    }


def display(value, key):
    if value is None or value == "None":
        return "継承（指定なし）"
    if key in {"bold", "italic"}:
        return "オン" if value else "オフ"
    if key == "underline" and isinstance(value, bool):
        return "オン" if value else "オフ"
    if key in {"font_scale", "line_reduction"}:
        return f"{value:g}%"
    if key == "size":
        return f"{value:g} pt"
    if key == "color":
        root = etree.fromstring(value.encode())
        node = next((node for node in root if isinstance(node.tag, str)), None)
        if node is None:
            return "色指定なし"
        name, color = etree.QName(node).localname, node.get("val", "")
        return {
            "srgbClr": "#" + color,
            "schemeClr": "テーマ色 " + color,
            "sysClr": "システム色 "
            + color
            + (" (#" + node.get("lastClr") + ")" if node.get("lastClr") else ""),
            "prstClr": "プリセット色 " + color,
        }.get(name, "色指定 " + name)
    if key == "alignment":
        return {
            "LEFT (1)": "左揃え",
            "CENTER (2)": "中央揃え",
            "RIGHT (3)": "右揃え",
            "JUSTIFY (4)": "両端揃え",
        }.get(str(value), str(value))
    if key == "anchor":
        return {
            "TOP (1)": "上揃え",
            "MIDDLE (3)": "中央揃え",
            "BOTTOM (4)": "下揃え",
        }.get(str(value), str(value))
    if key == "wrap":
        return "折り返す" if value else "折り返さない"
    return str(value)


def describe_patterns(patterns, key):
    values = unique(value for pattern in patterns for value in pattern)
    shown = " / ".join(display(value, key) for value in values[:6]) or "指定なし"
    return shown + (" / …" if len(values) > 6 else "")


def style_changes(left, right, *, compare_runs=True):
    if left == right:
        return []
    if not left or not right:
        return ["文字書式の指定が変更（詳細は要確認）"] if compare_runs else []
    if "run_attributes" not in left or "run_attributes" not in right:
        # Tolerate older/simple data consumers without falling back to raw XML.
        fields = [
            f"{label}: {display(left.get(key), key)} → {display(right.get(key), key)}"
            for key, label in LABELS.items()
            if left.get(key) != right.get(key)
        ]
        return fields or ["文字書式の構成変更（詳細は要確認）"]
    fields = []
    for key, label in LABELS.items():
        old, new = left["run_attributes"][key], right["run_attributes"][key]
        run_changed = compare_runs and old != new
        defaults_changed = (
            left["default_attributes"][key] != right["default_attributes"][key]
        )
        # Effective runs override unused paragraph defaults. A change to a
        # default is user-visible only for empty text or inherited run values.
        has_runs = bool(left["run_attributes"][key] and right["run_attributes"][key])
        if compare_runs and has_runs and not run_changed:
            continue
        if not run_changed and not defaults_changed:
            continue
        if not run_changed:
            old, new = left["default_attributes"][key], right["default_attributes"][key]
        before, after = describe_patterns(old, key), describe_patterns(new, key)
        detail = f"{label}: {before} → {after}"
        if before == after:
            detail = f"{label}の構成・指定が変更（{before}）"
        elif any(len(pattern) > 1 for pattern in old + new):
            detail += "（部分書式を含む）"
        if not run_changed and defaults_changed:
            detail += "（段落の既定書式）"
        fields.append(detail)
    if compare_runs and not fields and left["joint"] != right["joint"]:
        labels = [
            label
            for key, label in LABELS.items()
            if len(
                unique(
                    value
                    for data in (left, right)
                    for pattern in data["run_attributes"][key]
                    for value in pattern
                )
            )
            > 1
        ]
        fields.append("部分書式の組み合わせが変更: " + "・".join(labels))
    for key, label in (
        ("alignment", "段落の配置"),
        ("level", "段落レベル"),
        ("anchor", "文字の縦位置"),
        ("wrap", "折り返し"),
        ("autofit", "文字の自動調整"),
        ("font_scale", "自動調整の文字倍率"),
        ("line_reduction", "自動調整の行間縮小率"),
    ):
        if left[key] != right[key]:
            old = left[key] if isinstance(left[key], list) else [left[key]]
            new = right[key] if isinstance(right[key], list) else [right[key]]
            fields.append(
                f"{label}: {' / '.join(display(value, key) for value in old)} → {' / '.join(display(value, key) for value in new)}"
            )
    for index, side in enumerate(("左", "上", "右", "下")):
        if left["margins"][index] != right["margins"][index]:
            fields.append(
                f"文字ボックスの{side}余白: {left['margins'][index]:g} pt → {right['margins'][index]:g} pt"
            )
    for key, label in (
        ("paragraph_other", "段落間隔・箇条書きの設定"),
        ("body_other", "文字ボックスの設定"),
    ):
        if left[key] != right[key]:
            fields.append(label + "が変更")
    return fields
