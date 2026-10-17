"""Add wallpapers to Deer's collection, remove one, or check the collection before pushing.

  python add.py PHOTO [PHOTO ...] [--title "Title"] [--author "Name" --license "CC-BY-4.0" --source URL]
  python add.py --remove ID
  python add.py --check
  python add.py --uploads FOLDER [--report FILE]   (every photo in FOLDER, as your own; the upload Action)

Adding a photo:
  - turns it the right way up (EXIF orientation) and removes every bit of metadata (EXIF, GPS
    location, camera serial numbers, colour profiles are converted to sRGB first);
  - makes full/<id>.jpg (at most 3840 px on the long side) and thumbs/<id>.jpg (480 px wide);
  - adds an entry to wallpapers.json: id, title, added, color (the mean colour, which Deer paints
    while the picture loads), width, height, file, bytes, sha256, thumb, thumbBytes, thumbSha256,
    and author, license and source when given.
--title is the same for every photo given, so give it only with one photo; without it the title
comes from the file name ("lake_at-dawn.jpg" -> "Lake at dawn"). The id comes from the title and
is never reused for another picture.

Then commit and push:  git add -A && git commit -m "Add wallpapers" && git push

Your own photos need nothing more: with no --author and --license they are listed as the owner's
own (README: all rights reserved, shared as Deer wallpapers) and Deer shows no credit. Someone
else's photo goes in only when its licence lets anyone share it (for example CC0-1.0, CC-BY-4.0):
then give --author and --license, which Deer shows as the credit (CC-BY requires it).
Needs Pillow (python -m pip install pillow).
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys
import unicodedata

try:
    from PIL import Image, ImageCms, ImageOps, ImageStat
except ImportError:
    sys.exit('Pillow is needed: python -m pip install pillow')

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, 'wallpapers.json')
FULL_MAX = 3840          # long side of full/<id>.jpg
THUMB_WIDTH = 480        # width of thumbs/<id>.jpg
MIN_WIDTH = 1920         # smaller photos look soft as a background
MAX_FULL_BYTES = 25 * 1024 * 1024   # Deer refuses bigger files
MAX_THUMB_BYTES = 512 * 1024
ID_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$')
# Deer skips an entry whose texts are longer than these or hold control or direction characters.
TEXT_MAX = {'title': 120, 'author': 120, 'license': 64}
UNSAFE = re.compile('[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]')

Image.MAX_IMAGE_PIXELS = 300_000_000
# Photos --uploads takes (HEIC/HEIF from phones when pillow-heif is installed).
UPLOAD_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp', '.heic', '.heif')
# File names cameras and phones give, which make poor titles.
CAMERA_NAME = re.compile(r'^(img|dsc|dscn|dscf|pxl|mvimg|photo|image|screenshot|whatsapp image|signal|pict?)[\s_-]*\d|^\d{6,}', re.I)

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def load_manifest():
    with open(MANIFEST, encoding='utf-8') as f:
        data = json.load(f)
    if data.get('version') != 1 or not isinstance(data.get('wallpapers'), list):
        sys.exit('wallpapers.json is not a version 1 list')
    return data


def save_manifest(data):
    text = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    tmp = MANIFEST + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, MANIFEST)


def title_from(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    if CAMERA_NAME.match(stem):
        return 'Photo'  # "IMG_2034", "PXL_20261012_093011" say nothing about the picture
    words = re.sub(r'[_\-.]+', ' ', stem).strip()
    words = re.sub(r'\s+', ' ', words)
    return (words[:1].upper() + words[1:]) if words else 'Wallpaper'


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
    buf = io.BytesIO()
    # No exif=, no icc_profile=: Pillow then writes no metadata at all.
    img.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def add_one(data, taken, path, author, license_, title, source):
    """Make the files of one photo and put its entry first in data; ValueError when it cannot be used."""
    try:
        with Image.open(path) as im:
            im.load()
            img = ImageOps.exif_transpose(im)
            img = to_srgb(img)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise ValueError('not a photo Pillow can read (%s)' % e) from None
    w, h = img.size
    if w < MIN_WIDTH:
        print('warning: %s is only %d px wide; it will look soft as a background' % (path, w))
    if h > w:
        print('warning: %s is taller than it is wide; Home fills a wide window, so most of it is cut off' % path)
    if max(w, h) > FULL_MAX:
        scale = FULL_MAX / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    full = jpeg_bytes(img, 88)
    if len(full) > MAX_FULL_BYTES:
        full = jpeg_bytes(img, 80)
    if len(full) > MAX_FULL_BYTES:
        raise ValueError('still over %d MB as a JPEG' % (MAX_FULL_BYTES // 1048576))
    tw = min(THUMB_WIDTH, img.width)
    thumb_img = img.resize((tw, max(1, round(img.height * tw / img.width))), Image.LANCZOS)
    thumb = jpeg_bytes(thumb_img, 80)
    if len(thumb) > MAX_THUMB_BYTES:
        thumb = jpeg_bytes(thumb_img, 65)
    mean = ImageStat.Stat(img.resize((64, 64), Image.BILINEAR)).mean
    color = '#%02x%02x%02x' % tuple(int(round(c)) for c in mean[:3])
    t = UNSAFE.sub('', (title or title_from(path)).strip())[:TEXT_MAX['title']].strip() or 'Wallpaper'
    wid = unique_id(slug(t), taken)
    taken.add(wid)
    full_rel = 'full/%s.jpg' % wid
    thumb_rel = 'thumbs/%s.jpg' % wid
    with open(os.path.join(HERE, full_rel), 'wb') as f:
        f.write(full)
    with open(os.path.join(HERE, thumb_rel), 'wb') as f:
        f.write(thumb)
    entry = {
        'id': wid,
        'title': t,
        'added': datetime.date.today().isoformat(),
        'color': color,
        'width': img.width,
        'height': img.height,
        'file': full_rel,
        'bytes': len(full),
        'sha256': sha256(full),
        'thumb': thumb_rel,
        'thumbBytes': len(thumb),
        'thumbSha256': sha256(thumb),
    }
    if author:
        entry['author'] = author
    if license_:
        entry['license'] = license_
    if source:
        entry['source'] = source
    data['wallpapers'].insert(0, entry)
    print('added %s  "%s"  %dx%d  %.1f MB' % (wid, t, img.width, img.height, len(full) / 1048576))
    return entry


def add(paths, author, license_, title, source):
    data = load_manifest()
    taken = {w['id'] for w in data['wallpapers']} | set(data.get('retired', []))
    if title and len(paths) > 1:
        sys.exit('--title names one photo; give one photo with it, or leave it out')
    for path in paths:
        try:
            add_one(data, taken, path, author, license_, title, source)
        except ValueError as e:
            sys.exit('%s: %s' % (path, e))
        save_manifest(data)  # after each photo, so a later failure leaves no unlisted files


def uploads(folder, report):
    """
    --uploads: every photo in folder, as the owner's own (no credit), title from its file name.
    Writes report (JSON): done (file names added) and failed ([name, why]). Exit status 1 when any failed.
    """
    data = load_manifest()
    taken = {w['id'] for w in data['wallpapers']} | set(data.get('retired', []))
    done, failed = [], []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or name.startswith('.') or name.lower() == 'readme.md':
            continue
        if not name.lower().endswith(UPLOAD_EXT):
            failed.append([name, 'not a photo (JPEG, PNG, WebP, TIFF or HEIC)'])
            continue
        try:
            add_one(data, taken, path, '', '', None, None)
        except ValueError as e:
            failed.append([name, str(e)])
            continue
        save_manifest(data)
        done.append(name)
    if report:
        with open(report, 'w', encoding='utf-8') as f:
            json.dump({'done': done, 'failed': failed}, f, ensure_ascii=False, indent=1)
    print('%d added, %d could not be added' % (len(done), len(failed)))
    for name, why in failed:
        print('  %s: %s' % (name, why))
    return 1 if failed else 0


def remove(wid):
    data = load_manifest()
    keep = [w for w in data['wallpapers'] if w['id'] != wid]
    if len(keep) == len(data['wallpapers']):
        sys.exit('no wallpaper with id %s' % wid)
    gone = next(w for w in data['wallpapers'] if w['id'] == wid)
    for rel in (gone['file'], gone['thumb']):
        try:
            os.remove(os.path.join(HERE, rel))
        except FileNotFoundError:
            pass
    data['wallpapers'] = keep
    # An id is never given to another picture: a copy someone already downloaded keeps its name.
    data['retired'] = sorted(set(data.get('retired', [])) | {wid})
    save_manifest(data)
    print('removed', wid)


def check():
    data = load_manifest()
    bad = 0
    seen = set()
    listed = set()
    for w in data['wallpapers']:
        wid = w.get('id', '')
        problems = []
        if not ID_RE.match(wid):
            problems.append('id must be lower-case letters, digits and dashes')
        if wid in seen:
            problems.append('id used twice')
        seen.add(wid)
        for key in ('title', 'color', 'file', 'thumb', 'sha256', 'thumbSha256'):
            if not isinstance(w.get(key), str) or not w.get(key):
                problems.append('missing ' + key)
        for key in ('author', 'license', 'source'):
            if key in w and (not isinstance(w[key], str) or not w[key]):
                problems.append(key + ' must be text when given')
        for key, most in TEXT_MAX.items():
            v = w.get(key)
            if isinstance(v, str) and (len(v.strip()) > most or UNSAFE.search(v)):
                problems.append('%s is longer than %d characters or holds control characters (Deer would skip it)' % (key, most))
        if bool(w.get('author')) != bool(w.get('license')):
            problems.append('give author and license together (or neither, for your own photos)')
        for rel, size_key, sha_key, cap in (('file', 'bytes', 'sha256', MAX_FULL_BYTES), ('thumb', 'thumbBytes', 'thumbSha256', MAX_THUMB_BYTES)):
            p = w.get(rel)
            if not isinstance(p, str):
                continue
            listed.add(p.replace('/', os.sep))
            if p != '%s/%s.jpg' % ('full' if rel == 'file' else 'thumbs', wid):
                problems.append('%s should be %s/%s.jpg' % (rel, 'full' if rel == 'file' else 'thumbs', wid))
                continue
            full = os.path.join(HERE, p)
            if not os.path.isfile(full):
                problems.append(p + ' is missing')
                continue
            b = open(full, 'rb').read()
            if len(b) != w.get(size_key) or sha256(b) != w.get(sha_key):
                problems.append(p + ' does not match its size or sha256')
            if len(b) > cap:
                problems.append(p + ' is too big')
        if problems:
            bad += 1
            print('%s: %s' % (wid or '(no id)', '; '.join(problems)))
    for folder in ('full', 'thumbs'):
        for name in os.listdir(os.path.join(HERE, folder)):
            rel = os.path.join(folder, name)
            if name != '.gitkeep' and rel not in listed:
                print('not in wallpapers.json:', rel.replace(os.sep, '/'))
                bad += 1
    print('%d wallpapers, %s' % (len(data['wallpapers']), 'all good' if not bad else '%d problems' % bad))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('photos', nargs='*')
    ap.add_argument('--author')
    ap.add_argument('--license', dest='license_')
    ap.add_argument('--title')
    ap.add_argument('--source')
    ap.add_argument('--remove', metavar='ID')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--uploads', metavar='FOLDER', help='add every photo in FOLDER as your own (the upload Action)')
    ap.add_argument('--report', metavar='FILE', help='with --uploads: write what was added and what failed (JSON)')
    a = ap.parse_args()
    if a.check:
        sys.exit(check())
    if a.uploads:
        sys.exit(uploads(a.uploads, a.report))
    if a.remove:
        remove(a.remove)
        return
    if not a.photos:
        ap.print_help()
        return
    if bool(a.author) != bool(a.license_):
        sys.exit("someone else's photo needs both --author and --license; your own needs neither")
    for key, v in (('author', a.author), ('license', a.license_)):
        if v and (len(v.strip()) > TEXT_MAX[key] or UNSAFE.search(v)):
            sys.exit('--%s must be at most %d characters, with no control characters' % (key, TEXT_MAX[key]))
    add(a.photos, (a.author or '').strip(), (a.license_ or '').strip(), a.title, a.source)


if __name__ == '__main__':
    main()
