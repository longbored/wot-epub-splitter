# wot-epub-splitter

Splits the Wheel of Time omnibus EPUB into 14 individual EPUBs, one per book.

## Background

In 2014, the entire Wheel of Time series was nominated as a single work for the Hugo Award for Best Novel. As part of the Hugo voter packet, Tor Books made a DRM-free omnibus EPUB of all 14 books available to supporting members. This script targets that specific file.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended), or pip

## Setup

```bash
# With uv
uv sync

# With pip
pip install beautifulsoup4 lxml
```

## Usage

```bash
# With uv
uv run split_wot_epub.py "The Wheel of Time.epub"

# With Python directly
python split_wot_epub.py "The Wheel of Time.epub"
```

Output EPUBs are written to `wot_split/` by default. Pass a second argument to choose a different directory:

```bash
uv run split_wot_epub.py "The Wheel of Time.epub" ~/Books/WoT
```

Use `--dry-run` to preview assignments without writing any files:

```bash
uv run split_wot_epub.py "The Wheel of Time.epub" --dry-run
```

## Cover images

Several books in the omnibus either lack a proper cover image or only have a title-page illustration. The script supports supplying your own cover images via the `covers/` directory.

Name the file after the book using standard series numbering and place it in `covers/`:

```
covers/
  03 - The Dragon Reborn.jpg
  08 - The Path of Daggers.jpg
  12 - The Gathering Storm.jpg
```

JPEG and PNG are both supported. Any file present in `covers/` will override the embedded cover for that book. Files sourced elsewhere (e.g. from the individual ebook editions) work well.

**Do not commit cover images to your fork** — cover art is copyrighted.

## Notes

The `COVER_IMAGE_IDS` dictionary in the script contains manifest item IDs specific to the Hugo voter packet edition of the omnibus. If you have a different edition, the embedded cover mappings may not match and you may need to supply covers manually via the `covers/` directory.
