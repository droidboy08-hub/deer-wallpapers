# Deer wallpapers

Wallpapers for [Deer](https://github.com/droidboy08-hub/deer). Deer shows them in
Settings › Home and background, and downloads one only when you choose it.

## Adding wallpapers

Needs Python and Pillow (`python -m pip install pillow`).

```
python add.py C:\Photos\lake.jpg C:\Photos\hills.jpg --author "Your Name" --license CC0-1.0
python add.py --check
git add -A
git commit -m "Add wallpapers"
git push
```

`add.py` turns each photo the right way up, removes its metadata (EXIF, GPS location, camera
details), makes `full/<id>.jpg` (at most 3840 px on the long side) and `thumbs/<id>.jpg`
(480 px wide), and adds it to `wallpapers.json`. The title comes from the file name unless you
give `--title` (with one photo). Wide photos at least 1920 px wide look best.

To take one out: `python add.py --remove <id>`, then commit and push. Its id is never used again.

## What may go in

Only photos you took yourself, or photos whose licence lets anyone share them (for example
CC0-1.0 or CC-BY-4.0). Put the photographer in `--author` and the licence in `--license`;
Deer shows both. No pictures from Windows, Bing or other apps and sites that do not allow
sharing them.

## How Deer uses this repository

Deer reads `wallpapers.json` from this repository's GitHub Pages site
(`https://droidboy08-hub.github.io/deer-wallpapers/`) when Settings › Home and background is
open, shows the thumbnails, and downloads the full picture when you choose it. Every file must
match the size and SHA-256 listed in `wallpapers.json`, or Deer does not use it. The requests
carry no cookie or identifier.

## Licence

`add.py` and this README: Mozilla Public License 2.0 (`LICENSE`). Each photo keeps the licence
listed with it in `wallpapers.json`.
