"""Keep Deer's wallpaper collection (photos, and live wallpapers, i.e. videos) in step with the owner's
private upload repository, and check it before a push.

  python add.py --sync PRIVATE [--report FILE]   the upload Action: PRIVATE is a checkout of the private
                                                  repository droidboy08-hub/deer-wallpapers-upload
  python add.py --check                           check everything before pushing
  python add.py --migrate PRIVATE                 one-off (2026-10-21): the old layout to the new one

The collection, as Deer 0.1.9 and later read it:
  index.json                        {"version": 2, "wallpapers": [photos], "live": [videos], "retired": [ids]}
  wallpapers/<id>.jpg               a photo, at most 3840 px on the long side
  wallpapers/thumbs/<id>.jpg        its thumbnail, 480 px wide
  live-wallpapers/<id>.mp4          a video: picture only, H.264, at most 2560 x 1440, under 95 MB
  live-wallpapers/thumbs/<id>.jpg   a frame from near its start, 480 px wide
  CREDITS.md                        title, author, licence and source of every credited item (made here)
Each list is newest first. An entry: id, title, added, color (the mean colour, which Deer paints while
the picture loads), width, height, duration (videos, seconds), file, bytes, sha256, thumb, thumbBytes,
thumbSha256, originSha256, and author, license, source for someone else's work. An id is used once, in
either list, and never again once retired (nor one of the old wallpapers.json's).

--sync makes the collection match the private repository's folders wallpapers/ (photos) and
live-wallpapers/ (videos): they are the master list. A file is known by the SHA-256 of its bytes
(originSha256; never its name or path, which may hold personal details and stay private):
  - a file not published yet is published;
  - an item whose file is gone from its folder is removed: its files deleted, its id retired;
  - a renamed file keeps its wallpaper (same id); only the title changes;
  - a file whose content changed is a new item, and the old one is removed;
  - a photo in live-wallpapers/, a video in wallpapers/, a photo or video outside both folders, or a
    file that is no photo or video or cannot be read is reported (exit status 1, and in --report) and
    not published; the others still are. Anything else going wrong stops the sync with exit status 2
    (and the error in --report): what was done until then is consistent, and the Action publishes none
    of it.
The title comes from the file name ("Lake at dawn.jpg" -> "Lake at dawn", "lake_at-dawn.jpg" too;
camera names such as IMG_2034.jpg or VID_20261012.mp4 -> "Photo" / "Video"). Someone else's photo or
video carries the credit the private credits.json gives its file name:
  {"Lake at dawn.jpg": {"author": "Jane Doe", "license": "CC-BY-4.0", "source": "https://..."}}
(author and license together, source optional; null takes a credit off). A published item whose file has
no entry there keeps the credit it has. Nothing in PRIVATE is ever written, moved or deleted.

A photo is turned the right way up (EXIF orientation), converted to sRGB and stripped of every bit of
metadata (EXIF, GPS location, camera serial numbers, colour profile, comments): the JPEG written holds
nothing but its picture (--check walks its markers to be sure); iPhone HEIC needs pillow-heif.
A video (needs ffmpeg and ffprobe on PATH, or FFMPEG_DIR naming their folder) keeps the picture only (no
sound, metadata, chapters or subtitles), turned upright, HDR (iPhone HLG / Dolby Vision, HEVC) mapped to
ordinary SDR colours, H.264 at most 2560 x 1440 (never enlarged) and 60 frames a second, any length;
GitHub refuses files over 100 MB, so one still over 95 MB after that is not published.

Deer 0.1.5 to 0.1.8 read wallpapers.json (version 1) with full/<id>.jpg|mp4 and thumbs/<id>.jpg. That is
frozen since 2026-10-21 for them (--migrate retired the r07qxo and teshiisan items from it and moved its
licence-checked photos, 49 entries but 48 pictures, into the new layout); nothing writes it any more,
--check still checks it.
Needs Pillow (python -m pip install pillow; pillow-heif too for HEIC).
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import unicodedata
import urllib.parse

try:
    from PIL import Image, ImageCms, ImageStat
except ImportError:
    sys.exit('Pillow is needed: python -m pip install pillow')

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='backslashreplace')  # titles are not all cp1252 on a Windows console
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, 'index.json')
CREDITS = os.path.join(HERE, 'CREDITS.md')
LEGACY = os.path.join(HERE, 'wallpapers.json')   # Deer 0.1.5-0.1.8, frozen

# Each kind: its list in index.json, its folder here and in the private repository, its file type.
KINDS = {
    'photo': {'list': 'wallpapers', 'dir': 'wallpapers', 'ext': 'jpg'},
    'video': {'list': 'live', 'dir': 'live-wallpapers', 'ext': 'mp4'},
}
ENTRY_KEYS = ('id', 'title', 'added', 'color', 'width', 'height', 'duration', 'file', 'bytes', 'sha256',
              'thumb', 'thumbBytes', 'thumbSha256', 'originSha256', 'author', 'license', 'source')

FULL_MAX = 3840          # long side of a photo
THUMB_WIDTH = 480
MIN_WIDTH = 1920         # smaller photos look soft as a background
MAX_FULL_BYTES = 25 * 1024 * 1024   # Deer refuses bigger photos
MAX_THUMB_BYTES = 512 * 1024
MAX_VIDEO_BYTES = 95 * 1000 * 1000  # GitHub refuses files over 100 MB (Deer accepts up to 100 MiB)
VIDEO_BOX = (2560, 1440)
# What Deer accepts (modules/wallpapers/manifest.ts): sides, pixels, the index's size and length.
MAX_SIDE, MAX_VIDEO_SIDE, MIN_SIDE, MAX_PIXELS, MAX_THUMB_SIDE = 8192, 3840, 16, 40_000_000, 2048
MAX_INDEX_BYTES = 1024 * 1024
MAX_ENTRIES = 500
ID_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
COLOR_RE = re.compile(r'^#[0-9a-f]{6}$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# Deer skips an entry whose texts are longer than these or hold control or direction characters. Deer
# counts as JavaScript does, in UTF-16 units (an emoji is two): u16len().
TEXT_MAX = {'title': 120, 'author': 120, 'license': 64}
SOURCE_MAX = 500
UNSAFE = re.compile('[\u0000-\u001f\u007f-\u009f؜‎‏‪-‮⁦-⁩]')

Image.MAX_IMAGE_PIXELS = 300_000_000
PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp', '.heic', '.heif')
VIDEO_EXT = ('.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.3gp')
# Files in the private folders that are not wallpapers and say nothing.
QUIET = {'readme.md', 'thumbs.db', 'desktop.ini', '.ds_store', '.gitkeep'}
# File names cameras and phones give, which make poor titles.
CAMERA_NAME = re.compile(r'^(img|dsc|dscn|dscf|pxl|mvimg|vid|mov|video|photo|image|screenshot|screen recording|whatsapp (image|video)|signal|pict?)[\s_-]*\d|^\d{6,}', re.I)
# The 'ftyp' brands of an MP4 or QuickTime movie, and of still pictures in the same box format.
VIDEO_BRANDS = {b'isom', b'iso2', b'iso3', b'iso4', b'iso5', b'iso6', b'iso7', b'iso8', b'iso9', b'mp41', b'mp42', b'avc1', b'M4V ', b'qt  '}
IMAGE_BRANDS = {b'avif', b'avis', b'heic', b'heix', b'heim', b'heis', b'hevc', b'hevx', b'mif1', b'mif2', b'msf1', b'miaf'}
HDR_TRANSFER = {'arib-std-b67', 'smpte2084'}   # HLG (iPhone), PQ

WARNINGS = []


def warn(msg):
    WARNINGS.append(msg)
    print('warning:', msg)


# ---------------------------------------------------------------- files and JSON

def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_text(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def rel_path(kind, wid, thumb=False):
    k = KINDS[kind]
    return '%s/thumbs/%s.jpg' % (k['dir'], wid) if thumb else '%s/%s.%s' % (k['dir'], wid, k['ext'])


def load_index():
    if not os.path.isfile(INDEX):
        sys.exit('index.json is missing (python add.py --migrate PRIVATE makes it, once)')
    data = read_json(INDEX)
    if (not isinstance(data, dict) or data.get('version') != 2 or not isinstance(data.get('wallpapers'), list)
            or not isinstance(data.get('live'), list) or not isinstance(data.get('retired', []), list)):
        sys.exit('index.json is not a version 2 index')
    for k in KINDS.values():
        for e in data[k['list']]:
            # --sync knows an item only by it: one without would read as deleted.
            if not isinstance(e, dict) or not isinstance(e.get('id'), str) or not HEX64.match(str(e.get('originSha256'))):
                sys.exit('index.json: %s has no id or originSha256, so nothing was changed' % (e.get('id') if isinstance(e, dict) else e))
    data.setdefault('retired', [])
    return data


def save_index(index):
    """index.json, then CREDITS.md made from it (after every change, so a failure later leaves both right)."""
    write_json(INDEX, {'version': 2, 'wallpapers': index['wallpapers'], 'live': index['live'],
                       'retired': sorted(set(index['retired']))})
    write_text(CREDITS, credits_md(index))


def load_legacy():
    """wallpapers.json (Deer 0.1.5-0.1.8), or None once it is gone."""
    if not os.path.isfile(LEGACY):
        return None
    data = read_json(LEGACY)
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('wallpapers'), list):
        sys.exit('wallpapers.json is not a version 1 list')
    return data


def ids_taken(index, legacy):
    """Every id given out so far: listed or retired, here or in the old wallpapers.json."""
    taken = {e.get('id') for k in KINDS.values() for e in index[k['list']]} | set(index['retired'])
    if legacy:
        taken |= {w.get('id') for w in legacy['wallpapers']} | set(legacy.get('retired', []))
    return taken


def ordered(entry):
    return {k: entry[k] for k in ENTRY_KEYS if k in entry}


# ---------------------------------------------------------------- titles, ids, credits

def is_video(path):
    return path.lower().endswith(VIDEO_EXT)


def u16len(s):
    """The length Deer sees: UTF-16 units, as JavaScript's String length (a character past U+FFFF is two)."""
    return len(s.encode('utf-16-le')) // 2


def cut16(s, most):
    """s cut to at most `most` UTF-16 units, never inside a character."""
    out, n = [], 0
    for ch in s:
        n += 2 if ord(ch) > 0xFFFF else 1
        if n > most:
            break
        out.append(ch)
    return ''.join(out)


def clean_title(t, default):
    return cut16(UNSAFE.sub('', unicodedata.normalize('NFC', t)).strip(), TEXT_MAX['title']).strip() or default


def title_from(name):
    """
    "Lake at dawn.jpg" -> "Lake at dawn"; "lake_at-dawn.jpg" -> "Lake at dawn" (a name without spaces
    is split at - _ and .); "Star-forming pillar.jpg" keeps its dash; IMG_2034.jpg -> "Photo".
    """
    stem = unicodedata.normalize('NFC', os.path.splitext(os.path.basename(name))[0]).strip()
    video = is_video(name)
    if CAMERA_NAME.match(stem):
        return 'Video' if video else 'Photo'
    words = stem.replace('_', ' ') if ' ' in stem else re.sub(r'[_\-.]+', ' ', stem)
    words = re.sub(r'\s+', ' ', words).strip()
    words = words[:1].upper() + words[1:]
    return clean_title(words, 'Live wallpaper' if video else 'Wallpaper')


def slug(title):
    # "Serranía" -> "serrania", "Moon's" -> "moons"
    s = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii').lower()
    s = re.sub(r"['’]", '', s)
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s[:64].strip('-') or 'wallpaper'


def unique_id(base, taken):
    if base not in taken:
        return base
    n = 2
    while True:
        tail = '-%d' % n
        cand = base[:64 - len(tail)].strip('-') + tail
        if cand not in taken:
            return cand
        n += 1


def source_problem(v):
    if not isinstance(v, str) or not re.match(r'^https?://[^\s/]+\.[^\s/]+(/\S*)?$', v):
        return 'source must be a web address (https://...)'
    if len(v) > SOURCE_MAX or UNSAFE.search(v):
        return 'source is longer than %d characters or holds control characters' % SOURCE_MAX
    return None


def credit_from(c):
    """A credits.json value: {"author", "license", "source"?} as stored, or why it cannot be used."""
    if not isinstance(c, dict):
        return 'give {"author": ..., "license": ..., "source": ...} (or null)'
    extra = set(c) - {'author', 'license', 'source'}
    if extra:
        return 'unknown key(s) %s (author, license, source)' % ', '.join(sorted(extra))
    out = {}
    for key in ('author', 'license'):
        v = c.get(key)
        if not isinstance(v, str) or not v.strip():
            return 'author and license are both needed'
        v = unicodedata.normalize('NFC', v.strip())
        if u16len(v) > TEXT_MAX[key] or UNSAFE.search(v):
            return '%s is longer than %d characters or holds control characters' % (key, TEXT_MAX[key])
        out[key] = v
    if c.get('source') not in (None, ''):
        why = source_problem(c['source'].strip() if isinstance(c['source'], str) else c['source'])
        if why:
            return why
        out['source'] = c['source'].strip()
    return out


def read_credits(root):
    """credits.json of the private repository: {file name: credit dict, None (no credit) or why not usable}."""
    path = os.path.join(root, 'credits.json')
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8-sig') as f:
            raw = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        sys.exit('credits.json cannot be read, so nothing was changed: %s' % e)
    if not isinstance(raw, dict):
        sys.exit('credits.json must be {"<file name>": {"author": ..., "license": ..., "source": ...}}; nothing was changed')
    return {unicodedata.normalize('NFC', k).strip(): (None if v is None else credit_from(v)) for k, v in raw.items()}


def credit_of(entry):
    return {k: entry[k] for k in ('author', 'license', 'source') if entry.get(k)}


# ---------------------------------------------------------------- CREDITS.md

def md(s):
    return re.sub(r'([\\`*_\[\]<>|~#])', r'\\\1', s)


def md_url(u):
    return urllib.parse.quote(u, safe=":/?#@!$&'*+,;=%-._~")


def licence_md(name):
    m = re.match(r'^CC-(BY(?:-SA|-ND|-NC|-NC-SA|-NC-ND)?)-(\d\.\d)$', name, re.I)
    if m:
        return '[%s](https://creativecommons.org/licenses/%s/%s/)' % (md(name), m.group(1).lower(), m.group(2))
    m = re.match(r'^CC0-(\d\.\d)$', name, re.I)
    if m:
        return '[%s](https://creativecommons.org/publicdomain/zero/%s/)' % (md(name), m.group(1))
    return md(name)


def credits_md(index):
    lines = [
        '# Credits',
        '',
        "Wallpapers in this collection made by other people, with their licences. Every other wallpaper is",
        "the repository owner's own: all rights reserved, shared only for use as a wallpaper in Deer.",
        '',
        'Made by `add.py` from `index.json`; changes made here by hand are overwritten.',
        '',
    ]
    any_rows = False
    for kind, heading, who in (('photo', 'Wallpapers', 'Photographer'), ('live', 'Live wallpapers', 'Author')):
        rows = [e for e in index['wallpapers' if kind == 'photo' else 'live'] if e.get('author')]
        if not rows:
            continue
        any_rows = True
        lines += ['## ' + heading, '', '| Wallpaper | %s | Licence | Source |' % who, '| --- | --- | --- | --- |']
        for e in rows:
            src = e.get('source') or ''
            host = urllib.parse.urlsplit(src).hostname or src if src else ''
            lines.append('| [%s](%s) | %s | %s | %s |' % (
                md(e['title']), md_url(e['file']), md(e['author']), licence_md(e['license']),
                '[%s](%s)' % (md(host), md_url(src)) if src else ''))
        lines.append('')
    if not any_rows:
        lines += ['None yet.', '']
    return '\n'.join(lines)


# ---------------------------------------------------------------- making the files

def to_srgb(img):
    """Pixels in sRGB with no embedded profile (Deer and every browser assume sRGB)."""
    icc = img.info.get('icc_profile')
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile('sRGB')
            img = ImageCms.profileToProfile(img, src, dst, outputMode='RGB')
        except Exception:
            pass
    return img.convert('RGB')


def jpeg_bytes(img, quality):
    """The picture alone as a JPEG: no exif=, icc_profile= or xmp=, and comment=b'' (Pillow would
    otherwise write the source's COM segment, which it keeps in img.info, a camera's or anyone's text);
    img.info is set aside meanwhile so nothing else of the source can reach the encoder."""
    buf = io.BytesIO()
    info, img.info = img.info, {}
    try:
        img.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True, comment=b'')
    finally:
        img.info = info
    return buf.getvalue()


def thumb_and_color(img):
    tw = max(1, min(THUMB_WIDTH, img.width, img.width * MAX_THUMB_SIDE // img.height))
    thumb_img = img.resize((tw, max(1, round(img.height * tw / img.width))), Image.LANCZOS)
    thumb = jpeg_bytes(thumb_img, 80)
    if len(thumb) > MAX_THUMB_BYTES:
        thumb = jpeg_bytes(thumb_img, 65)
    mean = ImageStat.Stat(img.resize((64, 64), Image.BILINEAR)).mean
    return thumb, '#%02x%02x%02x' % tuple(int(round(c)) for c in mean[:3])


ORIENTATION = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
               5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
               8: Image.Transpose.ROTATE_90}


def upright(im, label):
    """
    The pixels turned as the EXIF orientation says (what ImageOps.exif_transpose does to them; it also
    rewrites the EXIF block, which is thrown away here anyway, and a damaged block makes that raise).
    """
    try:
        o = im.getexif().get(0x0112, 1)
    except Exception:
        warn('%s: its EXIF cannot be read, so it is published as stored (is it upright?)' % label)
        o = 1
    method = ORIENTATION.get(o) if isinstance(o, int) else None
    return im.transpose(method) if method is not None else im.copy()


def make_photo(path, label):
    """The cleaned picture and thumbnail of one photo; ValueError when it cannot be used."""
    try:
        with Image.open(path) as im:
            im.load()
            img = to_srgb(upright(im, label))
    except Exception as e:
        # A damaged file can make Pillow raise anything (OSError, SyntaxError, struct.error, TypeError...).
        why = 'damaged, or not a picture' if 'cannot identify image file' in str(e) else '%s: %s' % (type(e).__name__, e)
        raise ValueError('not a photo that can be read (%s)' % why) from None
    img.info = {}  # nothing of the source's metadata travels with the pixels
    w, h = img.size
    if w < MIN_WIDTH:
        warn('%s is only %d px wide; it will look soft as a background' % (label, w))
    if h > w:
        warn('%s is taller than it is wide; Home fills a wide window, so most of it is cut off' % label)
    if max(w, h) > FULL_MAX:
        scale = FULL_MAX / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    if min(img.size) < MIN_SIDE:
        raise ValueError('only %dx%d px' % img.size)
    full = jpeg_bytes(img, 88)
    if len(full) > MAX_FULL_BYTES:
        full = jpeg_bytes(img, 80)
    if len(full) > MAX_FULL_BYTES:
        raise ValueError('still over %d MB as a JPEG' % (MAX_FULL_BYTES // 1048576))
    thumb, color = thumb_and_color(img)
    return {'full': full, 'thumb': thumb, 'color': color, 'width': img.width, 'height': img.height}


def tool(name):
    """ffmpeg / ffprobe: FFMPEG_DIR, else PATH."""
    d = os.environ.get('FFMPEG_DIR')
    for cand in ([os.path.join(d, name + ext) for ext in ('.exe', '')] if d else []):
        if os.path.isfile(cand):
            return cand
    found = shutil.which(name)
    if not found:
        raise ValueError('videos need %s: install FFmpeg (winget install Gyan.FFmpeg) or set FFMPEG_DIR' % name)
    return found


def run(args):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace')


def probe(path):
    """The first video stream's width, height and transfer, the duration (None when not given), the container."""
    r = run([tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height,color_transfer:format=duration,format_name', '-of', 'json', path])
    try:
        info = json.loads(r.stdout or '{}')
        st = info['streams'][0]
        w, h = int(st['width']), int(st['height'])
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError('not a video that can be read') from None
    try:
        duration = float(info['format']['duration'])
    except (KeyError, ValueError, TypeError):
        duration = None
    return w, h, st.get('color_transfer') or '', duration, info.get('format', {}).get('format_name', '')


def make_video(path, label):
    """The converted MP4 and poster thumbnail of one video; ValueError when it cannot be used."""
    _, _, transfer, _, container = probe(path)
    if container.endswith('_pipe') or container in ('image2', 'gif', 'apng', 'webp'):
        raise ValueError('a picture, not a video')
    box_w, box_h = VIDEO_BOX
    # Fitted into 2560 x 1440 without enlarging, even sides for yuv420p.
    vf = ["scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2" % (box_w, box_h)]
    colours = []
    if transfer in HDR_TRANSFER:
        # iPhone videos are HDR (HLG, BT.2020): mapped to ordinary SDR (BT.709) colours, or they look flat.
        vf += ['zscale=tin=%s:pin=bt2020:min=bt2020nc:t=linear:npl=100' % transfer, 'format=gbrpf32le',
               'zscale=p=bt709', 'tonemap=tonemap=hable:desat=0', 'zscale=t=bt709:m=bt709:r=tv', 'format=yuv420p']
        colours = ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709']
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'out.mp4')

        def convert(filters, extra):
            # Picture only (-an -sn -dn), no metadata or chapters (location!), turned upright (ffmpeg's autorotate).
            return run([tool('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-i', path,
                        '-map', '0:v:0', '-an', '-sn', '-dn', '-map_metadata', '-1', '-map_metadata:s:v', '-1',
                        '-map_chapters', '-1', '-vf', ','.join(filters), '-fpsmax', '60',
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '22', '-maxrate', '10M', '-bufsize', '20M',
                        '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-tag:v', 'avc1'] + extra +
                       ['-movflags', '+faststart', '-fflags', '+bitexact', '-flags:v', '+bitexact', out])

        r = convert(vf, colours)
        if (r.returncode or not os.path.isfile(out)) and colours:
            warn('%s: its HDR colours could not be mapped (%s); converted as they are'
                 % (label, (r.stderr.strip().splitlines() or ['?'])[-1]))
            r = convert(vf[:1], [])
        if r.returncode or not os.path.isfile(out):
            raise ValueError('could not be converted: %s' % (r.stderr.strip().splitlines() or ['?'])[-1])
        size = os.path.getsize(out)
        if size > MAX_VIDEO_BYTES:
            raise ValueError('%.0f MB after converting; GitHub takes at most 100 MB a file: trim it' % (size / 1e6))
        w, h, _, duration, _ = probe(out)
        if not duration or duration <= 0:
            raise ValueError('no length after converting')
        if min(w, h) < MIN_SIDE:
            raise ValueError('only %dx%d px' % (w, h))
        poster = os.path.join(tmp, 'poster.png')
        r = run([tool('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-ss', '%.3f' % min(1.0, duration / 3), '-i', out,
                 '-frames:v', '1', poster])
        if r.returncode or not os.path.isfile(poster):
            raise ValueError('could not take a frame for its thumbnail')
        with Image.open(poster) as im:
            frame = im.convert('RGB')
        with open(out, 'rb') as f:
            full = f.read()
    if h > w:
        warn('%s is taller than it is wide; Home fills a wide window, so most of it is cut off' % label)
    if w < 1280:
        warn('%s is only %d px wide; it will look soft as a background' % (label, w))
    thumb, color = thumb_and_color(frame)
    return {'full': full, 'thumb': thumb, 'color': color, 'width': w, 'height': h, 'duration': round(duration, 3)}


def publish(kind, wid, title, made, origin, credit):
    """Writes the files of a new item; its index entry."""
    full_rel, thumb_rel = rel_path(kind, wid), rel_path(kind, wid, thumb=True)
    os.makedirs(os.path.dirname(os.path.join(HERE, thumb_rel)), exist_ok=True)
    with open(os.path.join(HERE, full_rel), 'wb') as f:
        f.write(made['full'])
    with open(os.path.join(HERE, thumb_rel), 'wb') as f:
        f.write(made['thumb'])
    entry = {
        'id': wid, 'title': title, 'added': datetime.date.today().isoformat(), 'color': made['color'],
        'width': made['width'], 'height': made['height'], 'duration': made.get('duration'),
        'file': full_rel, 'bytes': len(made['full']), 'sha256': sha256(made['full']),
        'thumb': thumb_rel, 'thumbBytes': len(made['thumb']), 'thumbSha256': sha256(made['thumb']),
        'originSha256': origin,
    }
    if kind != 'video':
        del entry['duration']
    entry.update(credit)
    return ordered(entry)


def unpublish(kind, wid):
    for rel in (rel_path(kind, wid), rel_path(kind, wid, thumb=True)):
        try:
            os.remove(os.path.join(HERE, rel))
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- --sync

class Private:
    def __init__(self, root, path):
        self.path = path
        self.rel = unicodedata.normalize('NFC', os.path.relpath(path, root).replace(os.sep, '/'))
        self.name = unicodedata.normalize('NFC', os.path.basename(path))
        self.sha = sha256_file(path)


def scan(root, failed):
    """
    The files of each private folder, by kind; a file in the wrong folder, of no known type or that
    cannot be read goes to failed, and so does a photo or video anywhere else in the repository (dropped
    on its front page by mistake: it would never be published, and nothing would say so).
    """
    files = {}
    for kind, k in KINDS.items():
        base = os.path.join(root, k['dir'])
        if not os.path.isdir(base):
            # Never read as "everything was deleted": a wrong path must not empty the collection.
            sys.exit('%s has no %s/ folder, so nothing was changed' % (root, k['dir']))
        found = []
        for dirpath, dirnames, names in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
            for name in sorted(names):
                path = os.path.join(dirpath, name)
                if name.startswith('.') or name.lower() in QUIET:
                    continue
                rel = os.path.relpath(path, root).replace(os.sep, '/')
                low = name.lower()
                if os.path.islink(path) or not os.path.isfile(path):
                    failed.append([rel, 'not an ordinary file'])
                elif not low.endswith(PHOTO_EXT + VIDEO_EXT):
                    failed.append([rel, 'not a photo (JPEG, PNG, WebP, TIFF, HEIC) or a video (MP4, MOV, WebM, MKV)'])
                elif kind == 'photo' and low.endswith(VIDEO_EXT):
                    failed.append([rel, 'a video in wallpapers/: videos go in live-wallpapers/'])
                elif kind == 'video' and low.endswith(PHOTO_EXT):
                    failed.append([rel, 'a photo in live-wallpapers/: photos go in wallpapers/'])
                else:
                    try:
                        found.append(Private(root, path))
                    except OSError as e:
                        failed.append([rel, 'cannot be read (%s)' % (e.strerror or e)])
        files[kind] = sorted(found, key=lambda f: f.rel)
    homes = {os.path.normcase(os.path.join(root, k['dir'])) for k in KINDS.values()}
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.')
                             and os.path.normcase(os.path.join(dirpath, d)) not in homes)
        for name in sorted(names):
            if name.lower().endswith(PHOTO_EXT + VIDEO_EXT):
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, '/')
                failed.append([rel, 'outside the folders: photos go in wallpapers/, videos in live-wallpapers/'])
    return files


def sync(root, report_path):
    index = load_index()
    legacy = load_legacy()
    credits = read_credits(root)
    failed = []
    files = scan(root, failed)
    taken = ids_taken(index, legacy)
    rep = {'added': [], 'removed': [], 'retitled': [], 'recredited': [], 'failed': failed, 'warnings': WARNINGS,
           'error': None}
    used_keys = set()

    def lookup(group):
        """The credits.json value for the first file of group that has one: (found, value, that file)."""
        for f in group:
            for key in (f.rel, f.name):
                if key in credits:
                    used_keys.add(key)
                    return True, credits[key], f
        return False, None, group[0]

    try:
        for kind, k in KINDS.items():
            items = index[k['list']]
            by_hash = {}
            for f in files[kind]:
                by_hash.setdefault(f.sha, []).append(f)
            # Gone from the private folder: removed, its id retired.
            for e in list(items):
                if e.get('originSha256') not in by_hash:
                    items.remove(e)
                    unpublish(kind, e['id'])
                    index['retired'].append(e['id'])
                    rep['removed'].append(e['id'])
                    print('removed %s  "%s"' % (e['id'], e['title']))
                    save_index(index)
            known = {e['originSha256']: e for e in items}
            pos = 0
            for h, group in by_hash.items():
                if len(group) > 1:
                    warn('%s: the same file as %s; it is published once'
                         % (', '.join(f.rel for f in group[1:]), group[0].rel))
                found, credit, credited = lookup(group)
                if isinstance(credit, str):
                    failed.append([credited.rel, 'its entry in credits.json: ' + credit])
                e = known.get(h)
                if e is not None:
                    # Published already: a new name is a new title, credits.json may change the credit.
                    titles = [title_from(f.name) for f in group]
                    title = e['title'] if e['title'] in titles else titles[0]
                    changed = False
                    if title != e['title']:
                        print('retitled %s  "%s" -> "%s"' % (e['id'], e['title'], title))
                        rep['retitled'].append(e['id'])
                        e['title'] = title
                        changed = True
                    if found and not isinstance(credit, str):
                        want = credit or {}
                        if want != credit_of(e):
                            for key in ('author', 'license', 'source'):
                                e.pop(key, None)
                            e.update(want)
                            rep['recredited'].append(e['id'])
                            print('credit of %s: %s' % (e['id'], ', '.join(want.values()) or 'none'))
                            changed = True
                    elif not found and credit_of(e):
                        warn('%s has no entry in credits.json; it keeps its credit (%s, %s)'
                             % (group[0].rel, e['author'], e['license']))
                    if changed:
                        items[items.index(e)] = ordered(e)
                        save_index(index)
                    continue
                if isinstance(credit, str):
                    continue  # reported above; not published without its credit
                f = group[0]
                try:
                    made = (make_video if kind == 'video' else make_photo)(f.path, f.rel)
                except Exception as err:
                    # A ValueError says why; anything else is a file a library could not cope with. Either
                    # way this file is reported and the others still go.
                    failed.append([f.rel, str(err) if isinstance(err, ValueError)
                                   else 'cannot be converted (%s: %s)' % (type(err).__name__, err)])
                    continue
                title = title_from(f.name)
                wid = unique_id(slug(title), taken)
                taken.add(wid)
                entry = publish(kind, wid, title, made, h, credit or {})
                items.insert(pos, entry)  # newest first, the new ones in file name order
                pos += 1
                save_index(index)
                rep['added'].append(wid)
                print('added %s  "%s"  %dx%d%s  %.1f MB' % (
                    wid, title, entry['width'], entry['height'],
                    '  %.1f s' % entry['duration'] if kind == 'video' else '', entry['bytes'] / 1048576))
        for key in sorted(set(credits) - used_keys):
            warn('credits.json names "%s", which is in neither folder' % key)
        save_index(index)
    except Exception as err:
        # Not a file's fault (those are caught above): a bug, a full disk. Every change until then was
        # saved whole, but the sync is not done: exit status 2, and the Action publishes nothing.
        rep['error'] = '%s: %s' % (type(err).__name__, err)
        traceback.print_exc()
        print('error: the sync stopped (%s); nothing of it is to be published' % rep['error'])
        return 2
    finally:
        if report_path:
            with open(report_path, 'w', encoding='utf-8') as fh:
                json.dump(rep, fh, ensure_ascii=False, indent=1)
    print('%d added, %d removed, %d retitled, %d credits changed, %d not published' % (
        len(rep['added']), len(rep['removed']), len(rep['retitled']), len(rep['recredited']), len(failed)))
    for name, why in failed:
        print('  %s: %s' % (name, why))
    return 1 if failed else 0


# ---------------------------------------------------------------- --check

def mp4_start(head, total):
    """Deer's test of a video's first bytes: an 'ftyp' box naming an MP4 brand and no still picture's."""
    if len(head) < 8:
        return False
    size = int.from_bytes(head[:4], 'big')
    if head[4:8] != b'ftyp' or size < 16 or size > 1024 or size > total or (size - 16) % 4 or len(head) < size:
        return False
    brands = [head[8:12]] + [head[at:at + 4] for at in range(16, size, 4)]
    return any(b in VIDEO_BRANDS for b in brands) and not any(b in IMAGE_BRANDS for b in brands)


def jpeg_markers(b):
    """
    The marker segments of a JPEG, in order, as (marker, payload), walking it from SOI to EOI past the
    entropy-coded data of each scan; ValueError when it is not built so, or has bytes after its end.
    """
    if b[:2] != b'\xff\xd8':
        raise ValueError('does not start as a JPEG')
    out, i, n = [], 2, len(b)
    while True:
        if i >= n or b[i] != 0xFF:
            raise ValueError('a marker is missing at byte %d' % i)
        while i < n and b[i] == 0xFF:
            i += 1  # fill bytes
        if i >= n:
            raise ValueError('ends inside a marker')
        m = b[i]
        i += 1
        if m == 0xD9:
            if i != n:
                raise ValueError('%d bytes after its end' % (n - i))
            out.append((m, b''))
            return out
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            out.append((m, b''))
            continue
        if i + 2 > n:
            raise ValueError('ends inside a marker')
        length = int.from_bytes(b[i:i + 2], 'big')
        if length < 2 or i + length > n:
            raise ValueError('a segment runs past the end')
        out.append((m, b[i + 2:i + length]))
        i += length
        if m == 0xDA:
            # The scan's data, up to the next marker that is neither a stuffed FF 00 nor a restart.
            while i < n and not (b[i] == 0xFF and i + 1 < n and b[i + 1] != 0x00 and not 0xD0 <= b[i + 1] <= 0xD7):
                i += 1


# What a JPEG of add.py's may hold: its JFIF header, quantisation and Huffman tables, the frame
# (baseline or progressive), restart interval, scans and restarts. Anything else (APP1 EXIF / XMP,
# APP2 ICC, APP13 Photoshop, COM comments, any other APPn) is metadata.
JPEG_ALLOWED = {0xDB, 0xC4, 0xC0, 0xC1, 0xC2, 0xDD, 0xDA, 0xD9} | set(range(0xD0, 0xD8))


def jpeg_problem(b, size=None, max_side=None):
    """Why bytes b are not a JPEG holding nothing but its picture (of the given size), or None."""
    try:
        markers = jpeg_markers(b)
    except ValueError as e:
        return 'is not a well-formed JPEG (%s)' % e
    for m, payload in markers:
        if m == 0xE0 and payload.startswith(b'JFIF\x00'):
            continue
        if m not in JPEG_ALLOWED:
            return 'holds metadata (marker FF %02X)' % m
    try:
        with Image.open(io.BytesIO(b)) as im:
            if im.format != 'JPEG':
                return 'is not a JPEG'
            if size and im.size != size:
                return 'is %dx%d, not the listed %dx%d' % (im.size + size)
            if max_side and max(im.size) > max_side:
                return 'is bigger than %d px' % max_side
    except Exception as e:
        return 'cannot be read (%s)' % e
    return None


def entry_problems(e, kind, retired, seen, origins):
    if not isinstance(e, dict):
        return ['not an object']
    p = []
    video = kind == 'video'
    wid = e.get('id', '')
    unknown = set(e) - set(ENTRY_KEYS) - ({'duration'} if video else set())
    if not video and 'duration' in e:
        unknown.add('duration')
    if unknown:
        p.append('unknown key(s): ' + ', '.join(sorted(unknown)))
    if not isinstance(wid, str) or not ID_RE.match(wid):
        p.append('id must be lower-case letters, digits and dashes')
    if wid in seen:
        p.append('id used twice (ids are unique across wallpapers and live)')
    if wid in retired:
        p.append('id is retired')
    seen.add(wid)
    for key in ('title', 'author', 'license'):
        v = e.get(key)
        if key == 'title' or key in e:
            if not isinstance(v, str) or not v.strip() or u16len(v.strip()) > TEXT_MAX[key] or UNSAFE.search(v):
                p.append('%s must be text of at most %d characters (UTF-16 units, as Deer counts), no control characters'
                         % (key, TEXT_MAX[key]))
    if bool(e.get('author')) != bool(e.get('license')):
        p.append('author and license go together (or neither, for the owner\'s own)')
    if 'source' in e and source_problem(e['source']):
        p.append(source_problem(e['source']))
    if 'source' in e and not e.get('author'):
        p.append('a source without a credit')
    added = e.get('added')
    try:
        if not (isinstance(added, str) and DATE_RE.match(added)):
            raise ValueError
        datetime.date.fromisoformat(added)
    except ValueError:
        p.append('added must be a date (YYYY-MM-DD)')
    if not isinstance(e.get('color'), str) or not COLOR_RE.match(e['color']):
        p.append('color must be #rrggbb (lower case)')
    side = MAX_VIDEO_SIDE if video else MAX_SIDE
    w, h = e.get('width'), e.get('height')
    if not all(isinstance(x, int) and not isinstance(x, bool) and MIN_SIDE <= x <= side for x in (w, h)) or w * h > MAX_PIXELS:
        p.append('width and height must be whole numbers from %d to %d' % (MIN_SIDE, side))
        w = h = None
    if video and not (isinstance(e.get('duration'), (int, float)) and not isinstance(e.get('duration'), bool)
                      and 0 < e['duration'] < float('inf')):
        p.append('a video needs its duration in seconds')
    for key in ('sha256', 'thumbSha256', 'originSha256'):
        if not isinstance(e.get(key), str) or not HEX64.match(e[key]):
            p.append('%s must be 64 lower-case hex digits' % key)
    if e.get('originSha256') in origins:
        p.append('the same originSha256 as another item')
    origins.add(e.get('originSha256'))
    for key, size_key, sha_key, cap, thumb in (
            ('file', 'bytes', 'sha256', MAX_VIDEO_BYTES if video else MAX_FULL_BYTES, False),
            ('thumb', 'thumbBytes', 'thumbSha256', MAX_THUMB_BYTES, True)):
        want = rel_path(kind, wid, thumb) if isinstance(wid, str) else None
        if e.get(key) != want:
            p.append('%s should be %s' % (key, want))
            continue
        path = os.path.join(HERE, want)
        if not os.path.isfile(path):
            p.append(want + ' is missing')
            continue
        with open(path, 'rb') as f:
            b = f.read()
        if len(b) != e.get(size_key) or sha256(b) != e.get(sha_key):
            p.append('%s does not match %s and %s' % (want, size_key, sha_key))
        if len(b) > cap:
            p.append(want + ' is too big')
        if thumb or not video:
            why = jpeg_problem(b, None if thumb or w is None else (w, h), MAX_THUMB_SIDE if thumb else None)
            if why:
                p.append('%s %s' % (want, why))
        elif not mp4_start(b[:1024], len(b)):
            p.append(want + ' does not start as an MP4 video')
    return p


def legacy_problems(legacy):
    """The frozen wallpapers.json (Deer 0.1.5-0.1.8) against full/ and thumbs/, as add.py always checked it."""
    out = []
    seen, listed = set(), set()
    retired = set(legacy.get('retired', []))
    for w in legacy['wallpapers']:
        wid = w.get('id', '')
        p = []
        if not ID_RE.match(wid):
            p.append('id must be lower-case letters, digits and dashes')
        if wid in seen:
            p.append('id used twice')
        if wid in retired:
            p.append('id is retired')
        seen.add(wid)
        for key in ('title', 'color', 'file', 'thumb', 'sha256', 'thumbSha256'):
            if not isinstance(w.get(key), str) or not w.get(key):
                p.append('missing ' + key)
        for key, most in TEXT_MAX.items():
            v = w.get(key)
            if v is not None and (not isinstance(v, str) or u16len(v.strip()) > most or UNSAFE.search(v)):
                p.append('%s is longer than %d characters or holds control characters' % (key, most))
        if bool(w.get('author')) != bool(w.get('license')):
            p.append('author and license go together')
        kind = w.get('kind', 'photo')
        if kind not in ('photo', 'video'):
            p.append('kind must be "photo" or "video"')
        video = kind == 'video'
        if video and not (isinstance(w.get('duration'), (int, float)) and 0 < w['duration'] < float('inf')):
            p.append('a video needs its duration in seconds')
        for rel, size_key, sha_key, cap in (('file', 'bytes', 'sha256', MAX_VIDEO_BYTES if video else MAX_FULL_BYTES),
                                            ('thumb', 'thumbBytes', 'thumbSha256', MAX_THUMB_BYTES)):
            path = w.get(rel)
            if not isinstance(path, str):
                continue
            listed.add(path)
            want = 'full/%s.%s' % (wid, 'mp4' if video else 'jpg') if rel == 'file' else 'thumbs/%s.jpg' % wid
            if path != want:
                p.append('%s should be %s' % (rel, want))
                continue
            full = os.path.join(HERE, path)
            if not os.path.isfile(full):
                p.append(path + ' is missing')
                continue
            with open(full, 'rb') as f:
                b = f.read()
            if len(b) != w.get(size_key) or sha256(b) != w.get(sha_key):
                p.append(path + ' does not match its size or sha256')
            if len(b) > cap:
                p.append(path + ' is too big')
        if p:
            out.append('wallpapers.json %s: %s' % (wid or '(no id)', '; '.join(p)))
    for folder in ('full', 'thumbs'):
        if os.path.isdir(os.path.join(HERE, folder)):
            for name in sorted(os.listdir(os.path.join(HERE, folder))):
                if name != '.gitkeep' and '%s/%s' % (folder, name) not in listed:
                    out.append('not in wallpapers.json: %s/%s' % (folder, name))
    return out


def check():
    problems = []
    if not os.path.isfile(INDEX):
        print('index.json is missing')
        return 1
    if os.path.getsize(INDEX) > MAX_INDEX_BYTES:
        problems.append('index.json is over %d bytes; Deer would not read it' % MAX_INDEX_BYTES)
    index = load_index()
    retired = index['retired']
    if not all(isinstance(r, str) and ID_RE.match(r) for r in retired) or len(set(retired)) != len(retired):
        problems.append('retired must be distinct ids')
    retired = set(r for r in retired if isinstance(r, str))
    legacy = load_legacy()
    seen = set()
    for kind, k in KINDS.items():
        items = index[k['list']]
        if len(items) > MAX_ENTRIES:
            problems.append('%s lists over %d items; Deer reads the first %d' % (k['list'], MAX_ENTRIES, MAX_ENTRIES))
        origins = set()
        last = None
        for e in items:
            p = entry_problems(e, kind, retired, seen, origins)
            added = e.get('added') if isinstance(e, dict) else None
            if isinstance(added, str) and last and added > last:
                p.append('added after the item above it (newest first)')
            last = added if isinstance(added, str) else last
            if p:
                problems.append('%s %s: %s' % (k['list'], e.get('id') if isinstance(e, dict) else '?', '; '.join(p)))
        listed = {e.get('file') for e in items if isinstance(e, dict)} | {e.get('thumb') for e in items if isinstance(e, dict)}
        for folder in (k['dir'], k['dir'] + '/thumbs'):
            path = os.path.join(HERE, folder)
            if not os.path.isdir(path):
                continue
            for name in sorted(os.listdir(path)):
                rel = folder + '/' + name
                if name != '.gitkeep' and rel not in listed and not (folder == k['dir'] and name == 'thumbs'):
                    problems.append('not in index.json: ' + rel)
    if not os.path.isfile(CREDITS):
        problems.append('CREDITS.md is missing')
    else:
        with open(CREDITS, encoding='utf-8', newline='') as f:
            if f.read() != credits_md(index):
                problems.append('CREDITS.md does not match index.json (add.py --sync makes it)')
    legacy_count = 0
    if legacy:
        legacy_count = len(legacy['wallpapers'])
        problems += legacy_problems(legacy)
        old = {w.get('id'): w for w in legacy['wallpapers']}
        for kind, k in KINDS.items():
            for e in index[k['list']]:
                if not isinstance(e, dict):
                    continue
                w = old.get(e.get('id'))
                if e.get('id') in set(legacy.get('retired', [])):
                    problems.append('%s %s: retired in wallpapers.json' % (k['list'], e.get('id')))
                if w is not None and (w.get('kind', 'photo') != kind or w.get('sha256') != e.get('sha256')):
                    problems.append('%s %s: wallpapers.json gives this id to a different picture' % (k['list'], e.get('id')))
    for line in problems:
        print(line)
    print('%d wallpapers, %d live wallpapers (wallpapers.json for Deer 0.1.5-0.1.8: %d), %s' % (
        len(index['wallpapers']), len(index['live']), legacy_count,
        'all good' if not problems else '%d problems' % len(problems)))
    return 0 if not problems else 1


# ---------------------------------------------------------------- --migrate (one-off, 2026-10-21)

RETIRE_PREFIXES = ('r07qxo', 'teshiisan')   # not the owner's; the owner agreed they go (2026-10-21)
WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\u0000-\u001f]')


def migrate(root):
    """
    The licence-checked photos of wallpapers.json into the new layout (same ids, the same files), with
    index.json and CREDITS.md; a copy of each into the private wallpapers/ folder named after its title,
    and its credit into the private credits.json, so that the first --sync knows them; the r07qxo and
    teshiisan items retired from wallpapers.json (their files there deleted), and copied nowhere.
    """
    if os.path.exists(INDEX):
        sys.exit('index.json exists: the collection was migrated already')
    legacy = load_legacy()
    if not legacy:
        sys.exit('wallpapers.json is missing')
    retire = [w for w in legacy['wallpapers'] if w['id'].startswith(RETIRE_PREFIXES)]
    move = [w for w in legacy['wallpapers'] if w not in retire and w.get('kind', 'photo') == 'photo'
            and w.get('author') and w.get('license')]
    other = [w['id'] for w in legacy['wallpapers'] if w not in retire and w not in move]
    if other:
        sys.exit('neither licence-checked photos nor to be retired, so nothing was done: ' + ', '.join(other))
    # One picture listed twice (sand-dunes-and-mountains-in-utahs-west-desert and
    # little-sahara-dunes-at-dusk-utah are the same file): the new layout knows a picture by its bytes, so it
    # keeps the first entry and retires the other's id (wallpapers.json, frozen, keeps both).
    firsts, twice = {}, []
    for w in move:
        if w['sha256'] in firsts:
            twice.append(w)
            print('%s is the same picture as %s: not moved, its id retired' % (w['id'], firsts[w['sha256']]['id']))
        else:
            firsts[w['sha256']] = w
    move = [w for w in move if w not in twice]
    private_dir = os.path.join(root, KINDS['photo']['dir'])
    os.makedirs(private_dir, exist_ok=True)
    credits_path = os.path.join(root, 'credits.json')
    credits = read_json(credits_path) if os.path.isfile(credits_path) else {}
    names = set()
    entries = []
    for w in move:
        title = w['title']
        name = title + '.jpg'
        if WINDOWS_BAD.search(title) or title != title.strip() or title.endswith('.') or title_from(name) != title:
            sys.exit('"%s" cannot be a file name that gives back its title; nothing was done' % title)
        if name.lower() in names:
            sys.exit('two photos would be named "%s"; nothing was done' % name)
        names.add(name.lower())
        with open(os.path.join(HERE, w['file']), 'rb') as f:
            full = f.read()
        with open(os.path.join(HERE, w['thumb']), 'rb') as f:
            thumb = f.read()
        if sha256(full) != w['sha256'] or sha256(thumb) != w['thumbSha256']:
            sys.exit('%s: its files do not match wallpapers.json; nothing was done' % w['id'])
        dest = os.path.join(private_dir, name)
        if os.path.exists(dest) and sha256_file(dest) != w['sha256']:
            sys.exit('%s exists with other content; nothing was done' % dest)
        credit = {k: w[k] for k in ('author', 'license', 'source') if w.get(k)}
        if name in credits and credits[name] != credit:
            sys.exit('credits.json gives "%s" another credit; nothing was done' % name)
        entries.append((w, name, full, thumb, credit))
    for w, name, full, thumb, credit in entries:
        for rel, b in ((rel_path('photo', w['id']), full), (rel_path('photo', w['id'], thumb=True), thumb)):
            os.makedirs(os.path.dirname(os.path.join(HERE, rel)), exist_ok=True)
            with open(os.path.join(HERE, rel), 'wb') as f:
                f.write(b)
        with open(os.path.join(private_dir, name), 'wb') as f:
            f.write(full)   # the cleaned JPEG: its SHA-256 is the entry's originSha256
        credits[name] = credit
    index = {'version': 2, 'wallpapers': [], 'live': [],
             'retired': sorted(set(legacy.get('retired', [])) | {w['id'] for w in retire + twice})}
    for w, name, full, thumb, credit in entries:
        e = {k: w[k] for k in ('id', 'title', 'added', 'color', 'width', 'height', 'bytes', 'sha256',
                               'thumbBytes', 'thumbSha256')}
        e.update(file=rel_path('photo', w['id']), thumb=rel_path('photo', w['id'], thumb=True),
                 originSha256=sha256_file(os.path.join(private_dir, name)), **credit)
        index['wallpapers'].append(ordered(e))
    save_index(index)
    write_json(credits_path, dict(sorted(credits.items(), key=lambda kv: kv[0].lower())))
    for w in retire:
        for rel in (w['file'], w['thumb']):
            try:
                os.remove(os.path.join(HERE, rel))
            except FileNotFoundError:
                pass
    legacy['wallpapers'] = [w for w in legacy['wallpapers'] if w not in retire]
    legacy['retired'] = sorted(set(legacy.get('retired', [])) | {w['id'] for w in retire})
    write_json(LEGACY, legacy)
    print('moved %d photos into wallpapers/ (copies in %s, credits in credits.json); retired %d items: %s'
          % (len(entries), private_dir, len(retire), ', '.join(w['id'] for w in retire)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--sync', metavar='PRIVATE', help='make the collection match the private repository (the upload Action)')
    ap.add_argument('--report', metavar='FILE', help='with --sync: what was added, removed, retitled and not published (JSON)')
    ap.add_argument('--check', action='store_true', help='check index.json, the folders, CREDITS.md and wallpapers.json')
    ap.add_argument('--migrate', metavar='PRIVATE', help='one-off: the old layout to the new one')
    a = ap.parse_args()
    if a.check:
        sys.exit(check())
    if a.sync:
        sys.exit(sync(a.sync, a.report))
    if a.migrate:
        migrate(a.migrate)
        return
    ap.print_usage()
    sys.exit(2)


if __name__ == '__main__':
    main()
