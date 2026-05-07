#!/usr/bin/env python3
"""
split_wot_epub.py

Splits the Wheel of Time omnibus EPUB into individual EPUBs,
one per book, by parsing the TOC to determine which files
belong to each book.

Usage:
    python split_wot_epub.py <input.epub> [output_dir]

Output:
    One .epub file per book in output_dir (default: ./wot_split/)
"""

import argparse
import copy
import re
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

# Type alias for the manifest structure used throughout.
Manifest = dict[str, dict[str, str]]

# ---------------------------------------------------------------------------
# Book metadata — in TOC order. Used to label output files and match covers.
# ---------------------------------------------------------------------------
# (book_num, file_label)  — file_label is used for the output filename.
# The OPF title strips the leading "NN - " prefix (see build_opf).
BOOK_TITLES = [
    (2, "01 - The Eye of the World"),
    (3, "02 - The Great Hunt"),
    (4, "03 - The Dragon Reborn"),
    (5, "04 - The Shadow Rising"),
    (6, "05 - The Fires of Heaven"),
    (7, "06 - Lord of Chaos"),
    (8, "07 - A Crown of Swords"),
    (9, "08 - The Path of Daggers"),
    (10, "09 - Winter's Heart"),
    (11, "10 - Crossroads of Twilight"),
    (12, "11 - Knife of Dreams"),
    (13, "12 - The Gathering Storm"),
    (14, "13 - Towers of Midnight"),
    (15, "14 - A Memory of Light"),
]

# ---------------------------------------------------------------------------
# Cover images.
#
# Values are either a manifest item ID (str) for images embedded in the
# omnibus, or a Path to a file in the covers/ directory for external art.
#
# Drop any file named "<book-label>.<ext>" into covers/ (where <book-label>
# matches the second element of a BOOK_TITLES entry, e.g.
# "08 - The Path of Daggers.jpg") to supply or override a cover image.
# ---------------------------------------------------------------------------

_COVERS_DIR = Path(__file__).parent / "covers"

COVER_IMAGE_IDS: dict[int, str | Path] = {
    2: "my9781429959810_2",
    3: "my9781429960137_3",
    4: "my9781429960168_4",
    5: "mye9781429960199_i0001",
    6: "my9781429960373_6",
    7: "my9781429960533_7",
    8: "my9781429960571_8",
    9: "co9781429960595_9",
    10: "my9781429960687_10",
    11: "my9781429960748_11",
    12: "my9781429960816_12",
    13: "my9781429960830_13",
    14: "mytp_14",
    15: "my9781429997171_15",
}

# Overlay covers/ files: any file whose stem matches a book label replaces
# (or fills in) the entry for that book.
if _COVERS_DIR.is_dir():
    _by_stem = {p.stem: p for p in _COVERS_DIR.iterdir() if p.is_file()}
    for _book_num, _label in BOOK_TITLES:
        if _label in _by_stem:
            COVER_IMAGE_IDS[_book_num] = _by_stem[_label]

# Compiled once; used in parse_toc_assignments to identify book title pages.
_BOOK_TITLE_PAGE = re.compile(r"^(title_\d+|[^/]*_tp01)\.html$")

_BOOK_NUMS = frozenset(t[0] for t in BOOK_TITLES)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Split WoT omnibus EPUB into individual books.")
    p.add_argument("input_epub", help="Path to the omnibus .epub file")
    p.add_argument(
        "output_dir",
        nargs="?",
        default="wot_split",
        help="Directory to write split EPUBs (default: wot_split/)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Parse and print assignments without writing files"
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Step 1: Locate key files inside the EPUB zip
# ---------------------------------------------------------------------------


def find_opf_path(zf: zipfile.ZipFile) -> str:
    """Read META-INF/container.xml to find the content.opf path."""
    container_xml = zf.read("META-INF/container.xml").decode("utf-8")
    soup = BeautifulSoup(container_xml, "xml")
    rootfile = soup.find("rootfile")
    if not rootfile:
        raise RuntimeError("Could not find rootfile in container.xml")
    return rootfile["full-path"]


def find_toc_path(zf: zipfile.ZipFile, opf_path: str, opf_soup: BeautifulSoup) -> str:
    """
    Find the TOC HTML file. Tries:
      1. Manifest item whose id or href contains 'toc' with an xhtml media-type.
      2. Any file named toc.html or toc.xhtml in the zip.
    """
    opf_dir = str(Path(opf_path).parent)

    for item in opf_soup.find_all("item"):
        href = item.get("href", "")
        media = item.get("media-type", "")
        item_id = item.get("id", "")
        if ("toc" in item_id.lower() or "toc" in href.lower()) and "xhtml" in media:
            return str(Path(opf_dir) / href) if opf_dir != "." else href

    for name in zf.namelist():
        base = Path(name).name.lower()
        if base in ("toc.html", "toc.xhtml"):
            return name

    raise RuntimeError("Could not locate TOC file in EPUB.")


# ---------------------------------------------------------------------------
# Step 2: Parse TOC to assign HTML files to books
# ---------------------------------------------------------------------------


def fill_unassigned(assignments: dict[str, int], manifest: Manifest) -> int:
    """Assign HTML manifest items that the TOC walk missed.

    Two heuristics, tried in order:
      1. Filename ends with _N.html where N is a known book number.
      2. Filename shares an ISBN prefix with an already-assigned *_tp01.html.

    Returns the count of newly assigned items.
    """
    # Build isbn_prefix → book_num from tp01 files already in assignments
    isbn_map: dict[str, int] = {}
    for path, book_num in assignments.items():
        name = Path(path).name
        if name.endswith("_tp01.html"):
            prefix = name[: -len("_tp01.html")]
            isbn_map[prefix + "_"] = book_num

    added = 0
    for item in manifest.values():
        if item["media-type"] not in ("application/xhtml+xml", "text/html"):
            continue
        fp = item["full_path"]
        if fp in assignments:
            continue

        name = Path(fp).name

        # Heuristic 1: _N.html suffix
        m = re.search(r"_(\d+)\.html$", name)
        if m and int(m.group(1)) in _BOOK_NUMS:
            assignments[fp] = int(m.group(1))
            added += 1
            continue

        # Heuristic 2: ISBN prefix shared with a known title-page file
        for prefix, book_num in isbn_map.items():
            if name.startswith(prefix):
                assignments[fp] = book_num
                added += 1
                break

    return added


def parse_toc_assignments(zf: zipfile.ZipFile, toc_path: str) -> dict[str, int]:
    """
    Walk the TOC HTML and assign each href to a book number.

    Returns: {filename_in_zip: book_number}
    'book_number' matches the values in BOOK_TITLES (2..15).
    Files before the first book entry get book_number=1 (front matter).
    """
    toc_html = zf.read(toc_path).decode("utf-8")
    soup = BeautifulSoup(toc_html, "html.parser")
    toc_dir = str(Path(toc_path).parent)

    assignments: dict[str, int] = {}
    current_book = 1  # front matter / title page before first book
    book_idx = 0  # index into BOOK_TITLES

    for p in soup.find_all("p", class_=re.compile(r"toc-entry")):
        is_top = "toc-entry" in (p.get("class") or []) and "toc-entrya" not in (
            p.get("class") or []
        )
        links = p.find_all("a", href=True)

        if is_top:
            # Only advance the book counter when the entry links to a real
            # book title page (title_N.html or *_tp01.html).  This skips the
            # omnibus "Title Page" (fulltp.html) and any trailing back-matter
            # entries that also carry class="toc-entry".
            top_href = links[0]["href"].split("#")[0] if links else ""
            if _BOOK_TITLE_PAGE.match(top_href) and book_idx < len(BOOK_TITLES):
                current_book = BOOK_TITLES[book_idx][0]
                book_idx += 1

        for a in links:
            href = unquote(a["href"].split("#")[0])
            if not href:
                continue
            full_path = f"{toc_dir}/{href}" if toc_dir and toc_dir != "." else href
            if full_path not in assignments:
                assignments[full_path] = current_book

    return assignments


# ---------------------------------------------------------------------------
# Step 3: Parse content.opf to get the full manifest and spine
# ---------------------------------------------------------------------------


def parse_opf(zf: zipfile.ZipFile, opf_path: str) -> tuple[BeautifulSoup, str, Manifest, list[str]]:
    """
    Returns (opf_soup, opf_dir, manifest, spine_idrefs)
      manifest: {id: {href, media-type, full_path, properties}}
      spine_idrefs: [idref, ...]  in reading order
    """
    opf_dir = str(Path(opf_path).parent)
    opf_xml = zf.read(opf_path).decode("utf-8")
    opf_soup = BeautifulSoup(opf_xml, "xml")

    manifest: Manifest = {}
    for item in opf_soup.find_all("item"):
        item_id = item.get("id", "")
        href = unquote(item.get("href", ""))
        media_type = item.get("media-type", "")
        full_path = f"{opf_dir}/{href}" if opf_dir and opf_dir != "." else href
        manifest[item_id] = {
            "href": href,
            "media-type": media_type,
            "full_path": full_path,
            "properties": item.get("properties", ""),
        }

    spine_idrefs = [itemref["idref"] for itemref in opf_soup.find("spine").find_all("itemref")]

    return opf_soup, opf_dir, manifest, spine_idrefs


# ---------------------------------------------------------------------------
# Step 4: For each book, determine which manifest items to include
# ---------------------------------------------------------------------------


def collect_book_items(
    book_num: int,
    assignments: dict[str, int],
    manifest: Manifest,
    spine_idrefs: list[str],
    zf: zipfile.ZipFile,
) -> tuple[list[str], set[str]]:
    """
    Returns:
      spine_ids:  manifest item IDs that belong in this book's spine (ordered)
      extra_ids:  manifest item IDs for referenced assets (images, css, fonts, ncx)
    """
    spine_ids = [
        idref
        for idref in spine_idrefs
        if (item := manifest.get(idref)) and assignments.get(item["full_path"]) == book_num
    ]

    # Collect assets referenced from those spine HTML files
    referenced_assets: set[str] = set()
    for idref in spine_ids:
        item = manifest[idref]
        try:
            html = zf.read(item["full_path"]).decode("utf-8", errors="replace")
        except KeyError:
            print(f"  WARNING: file not found in zip: {item['full_path']}")
            continue
        soup = BeautifulSoup(html, "html.parser")

        for link in soup.find_all("link", rel="stylesheet"):
            referenced_assets.add(link.get("href", "").split("#")[0])
        for img in soup.find_all(["img", "image"]):
            for attr in ("src", "href", "{http://www.w3.org/1999/xlink}href"):
                val = img.get(attr, "")
                if val:
                    referenced_assets.add(unquote(val).split("#")[0])
        for tag in soup.find_all(href=True):
            href = tag.get("href", "").split("#")[0]
            if href and not href.startswith("http"):
                referenced_assets.add(unquote(href))

    # Match referenced asset filenames to manifest items
    extra_ids: set[str] = set()
    for asset_href in referenced_assets:
        if not asset_href:
            continue
        asset_name = Path(asset_href).name
        for mid, mitem in manifest.items():
            if mid in spine_ids:
                continue
            if Path(mitem["href"]).name == asset_name or mitem["href"].endswith(asset_href):
                extra_ids.add(mid)

    # Always include NCX / nav items
    for mid, mitem in manifest.items():
        if "ncx" in mitem["media-type"] or mitem["properties"] in ("nav",):
            extra_ids.add(mid)

    return spine_ids, extra_ids


# ---------------------------------------------------------------------------
# Step 5: Build and write a new EPUB for one book
# ---------------------------------------------------------------------------


def build_epub(
    book_num: int,
    book_label: str,
    spine_ids: list[str],
    extra_ids: set[str],
    manifest: Manifest,
    opf_soup: BeautifulSoup,
    opf_dir: str,
    zf_in: zipfile.ZipFile,
    out_path: Path,
) -> None:
    """Write a new EPUB zip for one book."""
    ext_cover_id = "cover-image"
    cover_val = COVER_IMAGE_IDS.get(book_num)
    cover_mid: str | None = None
    ext_cover_path: Path | None = None

    if isinstance(cover_val, Path):
        if cover_val.is_file():
            ext = cover_val.suffix.lower()
            cover_mid = ext_cover_id
            ext_cover_path = cover_val
            manifest[ext_cover_id] = {
                "href": f"Images/cover{ext}",
                "media-type": "image/png" if ext == ".png" else "image/jpeg",
                "full_path": f"{opf_dir}/Images/cover{ext}",
                "properties": "",
            }
        else:
            print(f"  WARNING: cover file not found: {cover_val}")
    elif isinstance(cover_val, str):
        if cover_val in manifest:
            cover_mid = cover_val
        else:
            print(f"  WARNING: cover manifest ID not found: {cover_val}")

    all_ids = list(spine_ids) + [e for e in extra_ids if e not in spine_ids]
    if cover_mid and cover_mid in manifest and cover_mid not in all_ids:
        all_ids.append(cover_mid)

    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
            # mimetype must be first and uncompressed
            zf_out.writestr(
                zipfile.ZipInfo("mimetype"),
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )

            opf_rel = "OEBPS/content.opf" if opf_dir else "content.opf"
            container = f"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="{opf_rel}" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
            zf_out.writestr("META-INF/container.xml", container)

            copied: set[str] = set()
            if cover_mid and cover_mid in manifest:
                img_href = manifest[cover_mid]["href"]
                cover_html = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml">\n'
                    "<head><title>Cover</title></head>\n"
                    '<body style="margin:0;padding:0;text-align:center;">\n'
                    f'<img src="{img_href}" alt="Cover"'
                    ' style="max-width:100%;height:auto;"/>\n'
                    "</body>\n</html>"
                )
                zf_out.writestr(
                    f"{opf_dir}/cover.html" if opf_dir else "cover.html",
                    cover_html,
                )
                if ext_cover_path:
                    img_zip_path = manifest[ext_cover_id]["full_path"]
                    zf_out.writestr(img_zip_path, ext_cover_path.read_bytes())
                    copied.add(img_zip_path)

            for mid in all_ids:
                item = manifest.get(mid)
                if not item:
                    continue
                src_path = item["full_path"]
                if src_path in copied:
                    continue
                try:
                    data = zf_in.read(src_path)
                    zf_out.writestr(src_path, data)
                    copied.add(src_path)
                except KeyError:
                    print(f"  WARNING: could not copy {src_path}")

            zf_out.writestr(
                opf_rel,
                build_opf(book_label, spine_ids, extra_ids, manifest, opf_soup, cover_mid),
            )
    finally:
        manifest.pop(ext_cover_id, None)


def build_opf(
    book_label: str,
    spine_ids: list[str],
    extra_ids: set[str],
    manifest: Manifest,
    opf_soup: BeautifulSoup,
    cover_mid: str | None = None,
) -> str:
    """Generate a minimal content.opf for the split book."""
    all_ids = list(spine_ids) + [e for e in extra_ids if e not in spine_ids]
    if cover_mid and cover_mid in manifest and cover_mid not in all_ids:
        all_ids.append(cover_mid)

    clean_title = re.sub(r"^\d+\s*-\s*", "", book_label)

    manifest_items = []
    if cover_mid and cover_mid in manifest:
        manifest_items.append(
            '    <item id="cover-html" href="cover.html" media-type="application/xhtml+xml"/>'
        )
    for mid in all_ids:
        item = manifest[mid]
        props = f' properties="{item["properties"]}"' if item["properties"] else ""
        manifest_items.append(
            f'    <item id="{mid}" href="{item["href"]}" media-type="{item["media-type"]}"{props}/>'
        )

    cover_spine = (
        '    <itemref idref="cover-html" linear="no"/>\n'
        if cover_mid and cover_mid in manifest
        else ""
    )
    spine_items = cover_spine + "\n".join(f'    <itemref idref="{sid}"/>' for sid in spine_ids)

    # Work on a deep copy so mutations don't affect subsequent books
    local_soup = copy.deepcopy(opf_soup)
    meta_block = ""
    orig_meta = local_soup.find("metadata")
    if orig_meta:
        orig_title = orig_meta.find("dc:title")
        if orig_title:
            orig_title.string = clean_title
        cover_tag = orig_meta.find(lambda tag: tag.name == "meta" and tag.get("name") == "cover")
        if cover_mid:
            if cover_tag:
                cover_tag["content"] = cover_mid
            else:
                new_tag = local_soup.new_tag("meta")
                new_tag["name"] = "cover"
                new_tag["content"] = cover_mid
                orig_meta.append(new_tag)
        elif cover_tag:
            cover_tag.decompose()
        meta_block = str(orig_meta)
    else:
        cover_meta_line = f'\n    <meta name="cover" content="{cover_mid}"/>' if cover_mid else ""
        uid = str(uuid.uuid4())
        meta_block = f"""  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{clean_title}</dc:title>
    <dc:identifier id="uid">{uid}</dc:identifier>
    <dc:language>en</dc:language>{cover_meta_line}
  </metadata>"""

    ncx_id = next(
        (mid for mid, m in manifest.items() if "ncx" in m["media-type"]),
        "ncx",
    )

    guide_block = ""
    if cover_mid and cover_mid in manifest:
        guide_block = (
            '  <guide>\n    <reference type="cover" title="Cover" href="cover.html"/>\n  </guide>\n'
        )

    nl = "\n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf"
         unique-identifier="uid">
{meta_block}
  <manifest>
{nl.join(manifest_items)}
  </manifest>
  <spine toc="{ncx_id}">
{spine_items}
  </spine>
{guide_block}</package>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_epub)
    output_dir = Path(args.output_dir)

    if not input_path.is_file():
        print(f"ERROR: File not found: {input_path}")
        raise SystemExit(1)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Opening: {input_path}")
    with zipfile.ZipFile(input_path, "r") as zf:
        opf_path = find_opf_path(zf)
        print(f"OPF:     {opf_path}")

        opf_soup, opf_dir, manifest, spine_idrefs = parse_opf(zf, opf_path)
        print(f"Manifest items: {len(manifest)}, Spine items: {len(spine_idrefs)}")

        toc_path = find_toc_path(zf, opf_path, opf_soup)
        print(f"TOC:     {toc_path}")

        assignments = parse_toc_assignments(zf, toc_path)
        filled = fill_unassigned(assignments, manifest)
        print(
            f"TOC href assignments: {len(assignments) - filled} from TOC, "
            f"{filled} inferred from filename ({len(assignments)} total)"
        )

        unassigned = [
            manifest[idref]["full_path"]
            for idref in spine_idrefs
            if (item := manifest.get(idref)) and item["full_path"] not in assignments
        ]
        if unassigned:
            print(f"\nWARNING: {len(unassigned)} spine items not found in TOC:")
            for u in unassigned[:10]:
                print(f"  {u}")
            if len(unassigned) > 10:
                print(f"  ... and {len(unassigned) - 10} more")

        print()

        for book_num, book_label in BOOK_TITLES:
            spine_ids, extra_ids = collect_book_items(
                book_num, assignments, manifest, spine_idrefs, zf
            )

            if not spine_ids:
                print(f"SKIPPING {book_label}: no spine items found (check TOC assignments)")
                continue

            cover_val = COVER_IMAGE_IDS.get(book_num)
            if isinstance(cover_val, Path):
                cover_note = f", cover: {cover_val.name} (file)"
            elif isinstance(cover_val, str) and cover_val in manifest:
                cover_note = f", cover: {manifest[cover_val]['href']}"
            else:
                cover_note = ", cover: none"
            print(f"Book {book_num:2d}: {book_label}")
            print(
                f"         Spine items: {len(spine_ids)}, Asset items: {len(extra_ids)}{cover_note}"
            )

            if args.dry_run:
                for sid in spine_ids[:3]:
                    print(f"           {manifest[sid]['full_path']}")
                if len(spine_ids) > 3:
                    print(f"           ... and {len(spine_ids) - 3} more")
                continue

            safe_label = re.sub(r'[\\/:*?"<>|]', "_", book_label)
            out_path = output_dir / f"{safe_label}.epub"

            build_epub(
                book_num,
                book_label,
                spine_ids,
                extra_ids,
                manifest,
                opf_soup,
                opf_dir,
                zf,
                out_path,
            )
            print(f"         Written: {out_path} ({out_path.stat().st_size / 1_048_576:.1f} MB)")

    print("\nDone.")


if __name__ == "__main__":
    main()
