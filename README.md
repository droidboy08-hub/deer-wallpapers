# Deer wallpapers

Wallpapers for [Deer](https://github.com/droidboy08-hub/deer). Deer shows them in its background
picker (Settings › Home and background, and the Background button on Home), and downloads one only
when you choose it.

## Adding wallpapers

**The easy way (the owner):** drag photos into the `upload` folder of the private repository
droidboy08-hub/deer-wallpapers-upload on github.com and commit. Its Action runs
`add.py --uploads` on them (as the owner's own photos, no credit), pushes the result here and
clears the folder; Deer has them a few minutes later. The originals never reach this public
repository, so their location data stays private.

**On a PC** (also for someone else's photo, which needs a credit). Needs Python and Pillow
(`python -m pip install pillow`; `pillow-heif` too for iPhone HEIC photos).

```
python add.py C:\Photos\lake.jpg C:\Photos\hills.jpg
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

- **Photos the owner of this repository took.** Add them with no `--author` or `--license`;
  Deer shows no credit for them. They are © the repository owner, all rights reserved, and
  shared only for use as wallpapers in Deer.
- **Someone else's photo whose licence lets anyone share it** (for example CC0-1.0 or
  CC-BY-4.0): add it with `--author "Photographer" --license CC-BY-4.0` (and `--source <link>`);
  Deer shows the credit, which CC-BY requires.

Nothing else: no pictures downloaded from wallpaper sites, no game, film or anime art, no
Windows or Bing wallpapers. Those sites do not own the pictures and give no right to share them.

## How Deer uses this repository

Deer reads `wallpapers.json` from this repository's GitHub Pages site
(`https://droidboy08-hub.github.io/deer-wallpapers/`) while its background picker shows photos (at
most once every 10 minutes), shows the thumbnails, and downloads the full picture when you choose
it. Every file must match the size and SHA-256 listed in `wallpapers.json` (and be a JPEG of the
listed width and height), or Deer does not use it; an entry Deer cannot read is left out. The
requests carry no cookie or identifier. A wallpaper you take out stays on Home for anyone who
already chose it.

## Licence

`add.py` and this README: Mozilla Public License 2.0 (`LICENSE`). A photo with a licence listed
in `wallpapers.json` keeps that licence; a photo with none is the repository owner's, all rights
reserved, shared only for use as a wallpaper in Deer.
