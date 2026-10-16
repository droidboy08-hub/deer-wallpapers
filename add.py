"""Add wallpapers to Deer's collection, remove one, or check the collection before pushing.

  python add.py PHOTO [PHOTO ...] --author "Name" --license "CC0-1.0" [--title "Title"] [--source URL]
  python add.py --remove ID
  python add.py --check

Adding a photo:
  - turns it the right way up (EXIF orientation) and removes every bit of metadata (EXIF, GPS
    location, camera serial numbers, colour profiles are converted to sRGB first);
  - makes full/<id>.jpg (at most 3840 px on the long side) and thumbs/<id>.jpg (480 px wide);
  - adds an entry to wallpapers.json: id, title, author, license, source, added, color (the mean
    colour, which Deer paints while the picture loads), width, height, file, bytes, sha256, thumb,
    thumbBytes, thumbSha256.
--title is the same for every photo given, so give it only with one photo; without it the title
comes from the file name ("lake_at-dawn.jpg" -> "Lake at dawn"). The id comes from the title and
is never reused for another picture.

Then commit and push:  git add -A && git commit -m "Add wallpapers" && git push

Only add photos you took yourself or whose licence allows anyone to share them (for example
CC0-1.0, CC-BY-4.0), and credit the photographer in --author.
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

Image.MAX_IMAGE_PIXELS = 300_000_000


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
    words = re.sub(r'[_\-.]+', ' ', stem).strip()
    words = re.sub(r'\s+', ' ', words)
    return (words[:1].upper() + words[1:]) if words else 'Wallpaper'


def slug(title):
    s = title.lower()
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


def add(paths, author, license_, title, source):
    data = load_manifest()
    taken = {w['id'] for w in data['wallpapers']} | set(data.get('retired', []))
    if title and len(paths) > 1:
        sys.exit('--title names one photo; give one photo with it, or leave it out')
    for path in paths:
        with Image.open(path) as im:
            im.load()
            img = ImageOps.exif_transpose(im)
            img = to_srgb(img)
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
            sys.exit('%s is still over %d MB as a JPEG' % (path, MAX_FULL_BYTES // 1048576))
        tw = min(THUMB_WIDTH, img.width)
        thumb_img = img.resize((tw, max(1, round(img.height * tw / img.width))), Image.LANCZOS)
        thumb = jpeg_bytes(thumb_img, 80)
        if len(thumb) > MAX_THUMB_BYTES:
            thumb = jpeg_bytes(thumb_img, 65)
        mean = ImageStat.Stat(img.resize((64, 64), Image.BILINEAR)).mean
        color = '#%02x%02x%02x' % tuple(int(round(c)) for c in mean[:3])
        t = (title or title_from(path)).strip()
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
            'author': author,
            'license': license_,
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
        if source:
            entry['source'] = source
        data['wallpapers'].insert(0, entry)
        print('added %s  "%s"  %dx%d  %.1f MB' % (wid, t, img.width, img.height, len(full) / 1048576))
    save_manifest(data)


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
        for key in ('title', 'author', 'license', 'color', 'file', 'thumb', 'sha256', 'thumbSha256'):
            if not isinstance(w.get(key), str) or not w.get(key):
                problems.append('missing ' + key)
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
    a = ap.parse_args()
    if a.check:
        sys.exit(check())
    if a.remove:
        remove(a.remove)
        return
    if not a.photos:
        ap.print_help()
        return
    if not a.author or not a.license_:
        sys.exit('give --author (who took the photos) and --license (for example CC0-1.0)')
    add(a.photos, a.author.strip(), a.license_.strip(), a.title, a.source)


if __name__ == '__main__':
    main()
