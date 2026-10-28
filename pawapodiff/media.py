"""Remove playback only from private copies; never open or repair media links."""

from copy import deepcopy
from dataclasses import dataclass, field
import posixpath
import shutil
from urllib.parse import unquote
from zipfile import ZipFile, ZIP_DEFLATED, ZIP64_LIMIT

from lxml import etree
from .errors import InputError

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MEDIA_TYPES = {"video", "audio", "media"}
NS = {"p": P, "a": A, "r": R}


def media_marker(node):
    if not isinstance(node.tag, str):
        return False
    name = etree.QName(node)
    return name.localname in {"videoFile", "audioFile", "wavAudioFile", "media"} and (
        name.namespace in {P, A}
        or (name.namespace or "").startswith("http://schemas.microsoft.com/office/")
    )


def parse(data, part):
    try:
        root = etree.fromstring(
            data, etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except etree.XMLSyntaxError as error:
        raise InputError(
            "PowerPoint内部のXMLを読み取れません。",
            code="INVALID_XML",
            part=part,
            technical=f"{type(error).__name__}: line {error.lineno}, column {error.position[1]}",
        ) from error
    if root.getroottree().docinfo.doctype:
        raise InputError(
            "DTDを含むXMLは安全のため読み込みません。", code="XML_DTD", part=part
        )
    return root


def serialize(root):
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def owner_part(rels):
    return posixpath.join(
        posixpath.dirname(posixpath.dirname(rels)), posixpath.basename(rels)[:-5]
    )


def target_part(rels, target, names=None):
    base = posixpath.dirname(owner_part(rels)) if rels != "_rels/.rels" else ""
    path = target.split("#", 1)[0]
    literal = posixpath.normpath(posixpath.join(base, path)).lstrip("/")
    decoded = posixpath.normpath(posixpath.join(base, unquote(path))).lstrip("/")
    # OPC producers use both escaped ZIP names and URI-escaped relationship
    # targets. Check the archive before assuming a decoded name is missing.
    return literal if names is not None and literal in names else decoded


@dataclass
class MediaPlan:
    replacements: dict = field(default_factory=dict)
    removed: set = field(default_factory=set)
    notices: list = field(default_factory=list)


def placeholder(pic):
    """A neutral static shape with the same id and transform, and no synthetic text."""
    shape = etree.Element(f"{{{P}}}sp")
    nv = etree.SubElement(shape, f"{{{P}}}nvSpPr")
    props = pic.find("p:nvPicPr/p:cNvPr", NS)
    nv.append(
        deepcopy(props)
        if props is not None
        else etree.Element(f"{{{P}}}cNvPr", id="0", name="Media")
    )
    etree.SubElement(nv, f"{{{P}}}cNvSpPr")
    etree.SubElement(nv, f"{{{P}}}nvPr")
    sp = etree.SubElement(shape, f"{{{P}}}spPr")
    transform = pic.find("p:spPr/a:xfrm", NS)
    if transform is not None:
        sp.append(deepcopy(transform))
    geometry = etree.SubElement(sp, f"{{{A}}}prstGeom", prst="rect")
    etree.SubElement(geometry, f"{{{A}}}avLst")
    fill = etree.SubElement(sp, f"{{{A}}}solidFill")
    etree.SubElement(fill, f"{{{A}}}srgbClr", val="E2E8F0")
    return shape


def plan_media(archive):
    plan = MediaPlan()
    names = set(archive.namelist())
    roots = {}
    candidates = set()
    # Use the declared slide order, not slide filenames, for notice page numbers.
    pages = {}
    if "ppt/_rels/presentation.xml.rels" in names:
        relationships = parse(
            archive.read("ppt/_rels/presentation.xml.rels"),
            "ppt/_rels/presentation.xml.rels",
        )
        targets = {
            r.get("Id"): target_part(
                "ppt/_rels/presentation.xml.rels", r.get("Target", ""), names
            )
            for r in relationships
            if isinstance(r.tag, str) and r.get("TargetMode") != "External"
        }
        presentation = parse(
            archive.read("ppt/presentation.xml"), "ppt/presentation.xml"
        )
        pages = {
            targets.get(slide.get(f"{{{R}}}id")): index
            for index, slide in enumerate(
                presentation.findall(f"{{{P}}}sldIdLst/{{{P}}}sldId"), 1
            )
        }
    for rels in sorted(name for name in names if name.endswith(".rels")):
        root = parse(archive.read(rels), rels)
        roots[rels] = root
        owner = owner_part(rels)
        media = [
            r
            for r in root
            if isinstance(r.tag, str)
            and r.get("Type", "").rsplit("/", 1)[-1] in MEDIA_TYPES
        ]
        document = None
        if owner in names and owner.startswith(
            ("ppt/slides/", "ppt/slideLayouts/", "ppt/slideMasters/")
        ):
            document = parse(archive.read(owner), owner)
        markers = (
            [node for node in document.iter() if media_marker(node)]
            if document is not None
            else []
        )
        if not media and not markers:
            continue
        ids = {r.get("Id") for r in media}
        marker_ids = {
            value
            for marker in markers
            for key, value in marker.attrib.items()
            if key.startswith(f"{{{R}}}")
        }
        # Legacy video files can use hyperlink relationships, or have a marker
        # whose relationship is already missing. Both are playback-only.
        media.extend(
            r
            for r in root
            if r.get("Id") in marker_ids
            and r not in media
            and r.get("Type", "").rsplit("/", 1)[-1] == "hyperlink"
        )
        ids.update(marker_ids)
        for relationship in media:
            if relationship.get("TargetMode") != "External":
                candidates.add(target_part(rels, relationship.get("Target", ""), names))
            root.remove(relationship)
        if owner not in names:
            continue  # Normal validation will report a missing mandatory owner.
        if document is None:
            document = parse(archive.read(owner), owner)
        media_pics = []
        for pic in document.findall(".//p:pic", NS):
            if any(
                media_marker(node)
                or any(value in ids for value in node.attrib.values())
                for node in pic.iter()
                if isinstance(node.tag, str)
            ):
                media_pics.append(pic)
        # Remove all playback schedules on affected parts; static slide rendering
        # does not use animation timing, and must never attempt media playback.
        for timing in document.findall("p:timing", NS):
            document.remove(timing)
        for node in list(document.iter()):
            if not isinstance(node.tag, str) or node is document:
                continue
            local = etree.QName(node).localname
            if (
                media_marker(node)
                or (
                    local == "snd"
                    and any(
                        value in ids
                        for key, value in node.attrib.items()
                        if key.startswith(f"{{{R}}}")
                    )
                )
                or (
                    local in {"hlinkClick", "hlinkHover"}
                    and node.get("action", "").lower().startswith("ppaction://media")
                )
            ):
                if node.getparent() is not None:
                    node.getparent().remove(node)
            else:
                for key, value in list(node.attrib.items()):
                    if key.startswith(f"{{{R}}}") and value in ids:
                        del node.attrib[key]
        for pic in media_pics:
            props = pic.find("p:nvPicPr/p:cNvPr", NS)
            key = props.get("id", "media") if props is not None else "media"
            groups = [
                ancestor.find("p:nvGrpSpPr/p:cNvPr", NS)
                for ancestor in pic.iterancestors(f"{{{P}}}grpSp")
            ]
            key = "/".join(
                [
                    group.get("id", "group")
                    for group in reversed(groups)
                    if group is not None
                ]
                + [key]
            )
            blips = pic.findall(".//a:blip", NS)
            poster_ok = bool(blips)
            for blip in blips:
                image_id = blip.get(f"{{{R}}}embed") or blip.get(f"{{{R}}}link")
                relationship = next((r for r in root if r.get("Id") == image_id), None)
                if (
                    relationship is None
                    or relationship.get("TargetMode") == "External"
                    or target_part(rels, relationship.get("Target", ""), names)
                    not in names
                    or archive.getinfo(
                        target_part(rels, relationship.get("Target", ""), names)
                    ).file_size
                    == 0
                ):
                    poster_ok = False
                    if relationship is not None:
                        root.remove(relationship)
            if not poster_ok:
                pic.getparent().replace(pic, placeholder(pic))
            plan.notices.append(
                {
                    "code": "MEDIA_IGNORED",
                    "page": pages.get(owner),
                    "shape_id": key,
                    "message": "動画・音声の再生情報と参照を作業用コピーから除外しました。"
                    + (
                        "保存済みの静止画像で比較します。"
                        if poster_ok
                        else "静止画像がないため同じ位置の代替枠で比較します。"
                    ),
                }
            )
        if not media_pics:
            plan.notices.append(
                {
                    "code": "MEDIA_IGNORED",
                    "page": pages.get(owner),
                    "shape_id": "media",
                    "message": "動画・音声の参照を除外しました。参照回復・外部ファイルの読み込みは行いません。",
                }
            )
        for node in list(document.iter())[::-1]:
            if (
                isinstance(node.tag, str)
                and (
                    etree.QName(node).localname in {"extLst", "sndAc", "stSnd"}
                    or (
                        etree.QName(node).localname == "ext"
                        and node.get("uri") is not None
                    )
                )
                and not len(node)
                and node.getparent() is not None
            ):
                node.getparent().remove(node)
        plan.replacements[owner] = serialize(document)
        plan.replacements[rels] = serialize(root)
    # Image fallbacks are handled before the final reference validation, including
    # GIF alternatives and images in a part that also contains a movie.
    from .images import sanitize_images

    sanitize_images(archive, plan, pages)
    for rels, original in roots.items():
        owner = owner_part(rels)
        if owner not in plan.replacements:
            continue
        relationships = (
            parse(plan.replacements[rels], rels)
            if rels in plan.replacements
            else original
        )
        defined = {r.get("Id") for r in relationships if isinstance(r.tag, str)}
        for node in parse(plan.replacements[owner], owner).iter():
            for key, value in node.attrib.items():
                if key.startswith(f"{{{R}}}") and value not in defined:
                    raise InputError(
                        "内部参照が欠落しています。",
                        code="MISSING_PART",
                        part=owner,
                        technical=f"Undefined relationship: {value}",
                    )
    # Retain binaries that also have a legitimate remaining non-media reference.
    kept = {
        target_part(name, r.get("Target", ""), names)
        for name, root in roots.items()
        for r in root
        if isinstance(r.tag, str) and r.get("TargetMode") != "External"
    }
    plan.removed |= (candidates - kept) & names
    if plan.removed and "[Content_Types].xml" in names:
        content = parse(archive.read("[Content_Types].xml"), "[Content_Types].xml")
        for node in list(content):
            if node.get("PartName", "").lstrip("/") in plan.removed:
                content.remove(node)
        plan.replacements["[Content_Types].xml"] = serialize(content)
    return plan


def prepare_copy(source, destination, plan):
    if not plan.replacements and not plan.removed:
        shutil.copyfile(source, destination)
        return
    with (
        ZipFile(source) as original,
        ZipFile(destination, "w", compression=ZIP_DEFLATED, allowZip64=True) as output,
    ):
        for info in original.infolist():
            if info.filename in plan.removed:
                continue
            if info.filename in plan.replacements:
                output.writestr(info, plan.replacements[info.filename])
            else:
                with (
                    original.open(info) as reader,
                    output.open(
                        info, "w", force_zip64=info.file_size > ZIP64_LIMIT
                    ) as writer,
                ):
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
