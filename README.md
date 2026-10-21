# Deer wallpapers

Wallpapers for [Deer](https://github.com/droidboy08-hub/deer): photos, and live wallpapers (videos
Deer plays muted and looping). In Deer's background picker (Settings › Home and background, and the
Background button on Home) the Photo tab's "Deer wallpapers" group has **Get wallpapers** and the
Video tab's **Get live wallpapers**: a click downloads the photos or videos of this collection that
are not on the computer yet (Deer 0.1.9 and later).

## Adding and removing wallpapers

The collection follows the owner's private repository droidboy08-hub/deer-wallpapers-upload: its
`wallpapers` (photos) and `live-wallpapers` (videos) folders are the master list. Drag a file into
one of them on github.com and commit, and a few minutes later it is here; delete it there and it goes
from here; rename it and its title changes. That repository's Action runs `add.py --sync` on it and
pushes the result here. The originals never come here (so their location data stays private), only
clean copies. Someone else's photo or video needs a credit, given in that repository's `credits.json`.

Do not change the collection here by hand: the next sync puts it back as the upload folders say.

## What is here

| | |
| --- | --- |
| `index.json` | the list Deer reads: `{"version": 2, "wallpapers": [...], "live": [...], "retired": [...]}` |
| `wallpapers/<id>.jpg` | a photo: turned the right way up, sRGB, no metadata (EXIF, GPS location, camera details, comments: the JPEG holds its picture and nothing else), at most 3840 px on the long side |
| `wallpapers/thumbs/<id>.jpg` | its thumbnail, 480 px wide |
| `live-wallpapers/<id>.mp4` | a video: picture only (no sound, no metadata), H.264, at most 2560 x 1440, any length but under 95 MB (GitHub's limit is 100 MB a file) |
| `live-wallpapers/thumbs/<id>.jpg` | a frame from its first second, 480 px wide |
| `CREDITS.md` | title, author, licence and source of everything by someone else |

An entry in `index.json`: `id`, `title`, `added`, `color` (the mean colour, which Deer paints while
the picture loads), `width`, `height`, `duration` (videos, in seconds), `file`, `bytes`, `sha256`,
`thumb`, `thumbBytes`, `thumbSha256`, `originSha256` (the SHA-256 of the original in the upload
repository, which is how a sync knows it), and `author`, `license` and `source` for someone else's
work. Each list is newest first. An id is used once, in either list, and never again once it is in
`retired`.

**Deer 0.1.5 to 0.1.8** read `wallpapers.json` (version 1) with `full/` and `thumbs/`. These are frozen
as they were on 2026-10-21 (Deer updates itself, so they are only needed for a while): nothing new goes
there, and nothing is taken out of them any more.

`python add.py --check` (needs Python and Pillow: `python -m pip install pillow`) checks all of it:
every file against its size and SHA-256, every JPEG's markers (nothing but the picture), the folders
against `index.json`, `CREDITS.md`, and
`wallpapers.json` against `full/` and `thumbs/`. The Action runs it before every push. To try a sync on
a PC, clone both repositories side by side and run `python add.py --sync ..\deer-wallpapers-upload`
(also needs `pillow-heif` for iPhone HEIC photos, and FFmpeg for videos: `winget install Gyan.FFmpeg`,
or set `FFMPEG_DIR` to the folder holding ffmpeg and ffprobe).

## What may go in

- **Photos and videos the owner of this repository took.** They need no credit, and Deer shows none.
  They are © the repository owner, all rights reserved, and shared only for use as wallpapers in Deer.
- **Someone else's photo or video whose licence lets anyone share it** (public domain, CC0-1.0,
  CC-BY-4.0): with its author, licence and source in `credits.json`. Deer and `CREDITS.md` show the
  credit, which CC-BY requires.

Nothing else: no pictures downloaded from wallpaper sites, no game, film or anime art, no Windows or
Bing wallpapers. Those sites do not own the pictures and give no right to share them.

## How Deer uses this repository

Deer reads `index.json` from this repository's GitHub Pages site
(`https://droidboy08-hub.github.io/deer-wallpapers/`) only when Get wallpapers or Get live wallpapers
is clicked, and then downloads the thumbnails and files it does not have yet. Every file must match
the size and SHA-256 listed (a photo must also be a JPEG, a video an MP4), or Deer does not keep it;
an entry Deer cannot read is left out. The requests carry no cookie or identifier. Anyone can remove a
downloaded wallpaper from their own computer (right-click its tile, Remove). A wallpaper taken out of
the collection goes from Deer at the next Get, except while it is someone's background.

## Licence

`add.py` and this README: Mozilla Public License 2.0 (`LICENSE`). A wallpaper with a licence listed
in `index.json` and `CREDITS.md` keeps that licence; one with none is the repository owner's, all
rights reserved, shared only for use as a wallpaper in Deer.
