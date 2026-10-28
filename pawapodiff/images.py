"""Keep usable embedded pictures and remove broken image-only references.

This never fetches external resources or invokes Office repair. GIF binaries are
left intact; the normal static slide export remains the rendering path.
"""

import posixpath
from lxml import etree
from .media import A, P, R, NS, owner_part, target_part, parse, serialize, placeholder

IMAGE_TYPES = {"image", "hdphoto", "animatedgif", "svg"}


def shape_key(element):
    parts = []
    for node in [element, *element.iterancestors()]:
        if not isinstance(node.tag, str) or etree.QName(node).namespace != P:
            continue
        if etree.QName(node).localname in {"pic", "sp", "grpSp", "graphicFrame"}:
            props = node.find(".//p:cNvPr", NS)
            if props is not None:
                parts.append(props.get("id", "image"))
    return "/".join(reversed(parts)) or "image"


def sanitize_images(archive, plan, pages):
    names = set(archive.namelist())
    for rels in sorted(name for name in names if name.endswith(".rels")):
        owner = owner_part(rels)
        if owner not in names or not owner.startswith(
            (
                "ppt/slides/",
                "ppt/slideLayouts/",
                "ppt/slideMasters/",
                "ppt/notesSlides/",
                "ppt/notesMasters/",
            )
        ):
            continue
        root = parse(
            plan.replacements[rels]
            if rels in plan.replacements
            else archive.read(rels),
            rels,
        )
        document = parse(
            plan.replacements[owner]
            if owner in plan.replacements
            else archive.read(owner),
            owner,
        )
        relationships = {r.get("Id"): r for r in root if isinstance(r.tag, str)}
        bad = set()
        changed = False
        notices = []

        def notice(element, message, missing=False):
            notices.append(
                {
                    "code": "IMAGE_MISSING" if missing else "IMAGE_FALLBACK",
                    "page": pages.get(owner),
                    "shape_id": shape_key(element),
                    "message": message,
                }
            )

        def usable(rid):
            relationship = relationships.get(rid)
            return (
                relationship is not None
                and rid not in bad
                and relationship.get("TargetMode") != "External"
                and relationship.get("Type", "").rsplit("/", 1)[-1].lower()
                in IMAGE_TYPES
            )

        for rid, relationship in relationships.items():
            if (
                relationship.get("Type", "").rsplit("/", 1)[-1].lower()
                not in IMAGE_TYPES
                or relationship.get("TargetMode") == "External"
            ):
                continue
            target = target_part(rels, relationship.get("Target", ""), names)
            if target not in names or archive.getinfo(target).file_size == 0:
                bad.add(rid)
                if target not in names:
                    plan.removed.add(target)
            else:
                relative = posixpath.relpath(target, posixpath.dirname(owner))
                if relationship.get("Target") != relative:
                    relationship.set("Target", relative)
                    changed = True
                    notice(
                        document,
                        "画像参照のURI表記を作業用コピー内の画像名に合わせました。外部参照・参照回復は行いません。",
                    )
        # Some exports retain an r:embed whose relationship itself is absent.
        for blip in document.iter():
            if not isinstance(blip.tag, str) or etree.QName(blip).localname not in {
                "blip",
                "svgBlip",
                "imgLayer",
                "gifBlip",
            }:
                continue
            for key in (f"{{{R}}}embed", f"{{{R}}}link"):
                rid = blip.get(key)
                if rid and rid not in relationships:
                    bad.add(rid)
        if not bad and not changed:
            continue
        for pic in list(document.findall(".//p:pic", NS)):
            primary = pic.find("p:blipFill/a:blip", NS)
            if primary is None:
                continue
            embed, link = primary.get(f"{{{R}}}embed"), primary.get(f"{{{R}}}link")
            if embed not in bad and link not in bad:
                continue
            selected = embed if usable(embed) else link if usable(link) else None
            if selected is None:
                # A cached raster alternative is useful when the GIF itself is
                # gone. WDP and SVG extension data are not promoted to a raster.
                for node in pic.iter():
                    for key, rid in node.attrib.items():
                        if key.startswith(f"{{{R}}}") and usable(rid):
                            part = target_part(
                                rels, relationships[rid].get("Target", ""), names
                            )
                            if posixpath.splitext(part)[1].lower() in {
                                ".png",
                                ".jpg",
                                ".jpeg",
                                ".gif",
                                ".bmp",
                                ".tif",
                                ".tiff",
                                ".emf",
                                ".wmf",
                            }:
                                selected = rid
                                break
                    if selected:
                        break
            if selected:
                primary.set(f"{{{R}}}embed", selected)
                primary.attrib.pop(f"{{{R}}}link", None)
                notice(
                    pic,
                    "欠落したGIF・画像参照を除外し、取得できる埋め込み済みの代替画像で比較します。画像内容は要確認です。",
                )
            else:
                notice(
                    pic,
                    "GIF・画像本体が欠落しているため、同じ位置・寸法の代替枠で比較します。この画像の内容は確認できません。",
                    missing=True,
                )
                pic.getparent().replace(pic, placeholder(pic))
            changed = True
        # Broken processing/SVG/GIF alternatives must not invalidate an existing
        # primary raster/GIF. Drop only the containing optional extension.
        for node in list(document.iter()):
            refs = {
                value
                for key, value in node.attrib.items()
                if key.startswith(f"{{{R}}}")
            }
            if not refs.intersection(bad):
                continue
            extension = next(
                (
                    parent
                    for parent in [node, *node.iterancestors()]
                    if isinstance(parent.tag, str)
                    and etree.QName(parent).localname == "ext"
                    and parent.get("uri") is not None
                ),
                None,
            )
            if extension is not None:
                if extension.getparent() is not None:
                    notice(
                        extension,
                        "GIF・画像の欠落した代替画像・補助情報を除外しました。取得できる主画像はそのまま比較します。",
                    )
                    extension.getparent().remove(extension)
                    changed = True
                continue
            # A picture fill (including backgrounds) can be missing independently
            # of text or geometry; keep those and use a neutral fill.
            fill = next(
                (
                    parent
                    for parent in [node, *node.iterancestors()]
                    if parent.tag == f"{{{A}}}blipFill"
                ),
                None,
            )
            if fill is not None and fill.getparent() is not None:
                notice(
                    fill,
                    "背景・図形の塗りつぶし画像が欠落しているため、無地で比較します。画像内容は確認できません。",
                    missing=True,
                )
                replacement = etree.Element(f"{{{A}}}solidFill")
                etree.SubElement(replacement, f"{{{A}}}srgbClr", val="E2E8F0")
                fill.getparent().replace(fill, replacement)
                changed = True
        for rid in bad:
            relationship = relationships.get(rid)
            if relationship is not None:
                root.remove(relationship)
                changed = True
        if bad and not notices:
            notice(
                document, "使われていない欠落画像の参照を作業用コピーから除外しました。"
            )
        # Remove empty extension containers, never DrawingML geometry a:ext.
        for node in list(document.iter())[::-1]:
            if (
                isinstance(node.tag, str)
                and etree.QName(node).localname == "extLst"
                and not len(node)
                and node.getparent() is not None
            ):
                node.getparent().remove(node)
        if changed:
            for entry in notices:
                entry["part"] = owner
                entry["missing_relationships"] = sorted(bad)
                entry["missing_parts"] = sorted(
                    {
                        target_part(rels, relationships[rid].get("Target", ""), names)
                        for rid in bad
                        if rid in relationships
                    }
                )
            plan.replacements[owner] = serialize(document)
            plan.replacements[rels] = serialize(root)
            plan.notices.extend(notices)
