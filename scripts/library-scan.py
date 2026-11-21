#!/usr/bin/env python3
"""Read a private ROM library without modifying or copying any game data.

Cover fetching is opt-in. Private catalogs/textures default to ignored artifacts/;
downloaded original art stays on FILES, outside the source/release image.
Requires Pillow only when converting cover art.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = Path('/run/media/system/Files/Downloads/surface-go-library-art')
EXTENSIONS = {
    '.gba': 'gba', '.z64': 'n64', '.n64': 'n64', '.v64': 'n64',
    '.sfc': 'snes', '.smc': 'snes', '.nes': 'nes', '.gb': 'gb', '.gbc': 'gbc',
    '.nds': 'nds', '.gcm': 'gamecube', '.cso': 'psp', '.pbp': 'psp',
    '.iso': 'unknown', '.rvz': 'unknown', '.chd': 'unknown', '.cue': 'unknown',
}
REPOSITORIES = {
    'gba': 'Nintendo_-_Game_Boy_Advance', 'n64': 'Nintendo_-_Nintendo_64',
    'snes': 'Nintendo_-_Super_Nintendo_Entertainment_System',
    'nes': 'Nintendo_-_Nintendo_Entertainment_System', 'gb': 'Nintendo_-_Game_Boy',
    'gbc': 'Nintendo_-_Game_Boy_Color', 'nds': 'Nintendo_-_Nintendo_DS',
    'gamecube': 'Nintendo_-_GameCube', 'wii': 'Nintendo_-_Wii',
    'ps1': 'Sony_-_PlayStation', 'ps2': 'Sony_-_PlayStation_2',
    'psp': 'Sony_-_PlayStation_Portable', 'xbox': 'Microsoft_-_Xbox',
}
EXCLUDED = re.compile(r'(^|[\W_])(bios|firmware|prod[ -]?keys?|title[ -]?keys?|keys|lost\+found)([\W_]|$)', re.I)
REGION = re.compile(r'^(?:usa|europe|japan|world|australia|korea|canada|brazil|china|taiwan|asia|france|germany|spain|italy|uk|u|e|j|en|ja|fr|de|es|it|pt|nl|ko|zh|sv|no|da|fi|ru|pl)(?:\s*[,+]\s*(?:usa|europe|japan|world|australia|korea|canada|brazil|china|taiwan|asia|france|germany|spain|italy|uk|en|ja|fr|de|es|it|pt|nl|ko|zh|sv|no|da|fi|ru|pl))*$', re.I)


def clean_field(value):
    return ''.join(' ' if ord(char) < 32 or ord(char) == 127 else char for char in str(value)).strip()


def rom_stem(path):
    name = Path(path).name
    if name.lower().endswith('.nkit.iso'):
        return name[:-9]
    return Path(name).stem


def display_title(name):
    name = re.sub(r'^\d{3,5}\s*-\s*', '', name)
    name = re.sub(r'\(([^()]*)\)', lambda match: '' if REGION.fullmatch(match[1].strip()) or match[1].lower() in {'trashman', 'squirrels', 'ndsi enhanced'} else match[0], name)
    name = re.sub(r'\[(?:b|!|h\d?|f\d?)\]', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip(' -')
    name = re.sub(r', The(?=\s*(?:\(|$))', '', name)
    return clean_field(name)


def match_key(value):
    text = ''.join(char for char in unicodedata.normalize('NFKD', value).casefold() if not unicodedata.combining(char))
    return ''.join(char for char in text if char.isalnum())


def read_at(path, offset, size):
    with path.open('rb') as stream:
        stream.seek(offset)
        return stream.read(size)


def identify_system(path):
    system = EXTENSIONS.get(path.suffix.lower())
    if system is None or system != 'unknown':
        return system
    # Plain/NKit disc headers retain Nintendo magic; never read/hash full images.
    if path.suffix.lower() == '.iso':
        header = read_at(path, 0, 32)
        if header[28:32] == bytes.fromhex('c2339f3d'):
            return 'gamecube'
        if header[24:28] == bytes.fromhex('5d1c9ea3'):
            return 'wii'
        sector = read_at(path, 0x8000, 4096)
        if b'PSP GAME' in sector:
            return 'psp'
        if b'PLAYSTATION 2' in sector:
            return 'ps2'
        if b'PLAYSTATION' in sector:
            return 'ps1'
        if read_at(path, 0x10000, 20) == b'MICROSOFT*XBOX*MEDIA':
            return 'xbox'
    # A container extension alone cannot prove the emulated console.
    folders = ' '.join(path.parts[:-1]).lower()
    for pattern, candidate in [
        (r'\b(gamecube|nintendo gc|dolphin)\b', 'gamecube'), (r'\bwii\b', 'wii'),
        (r'\b(playstation 2|ps2)\b', 'ps2'), (r'\b(playstation|ps1|psx)\b', 'ps1'),
        (r'\b(psp|playstation portable)\b', 'psp'), (r'\bxbox\b', 'xbox')]:
        if re.search(pattern, folders):
            return candidate
    if path.suffix.lower() == '.cue':
        # Cue text is small; examine a bounded sector header in its local BIN.
        text = read_at(path, 0, 65536).decode('utf-8', errors='replace')
        match = re.search(r'^\s*FILE\s+"([^"\r\n]+)"', text, re.M | re.I)
        if match:
            binary = path.parent / match[1]
            if binary.parent.resolve() == path.parent.resolve() and not binary.is_symlink() and binary.is_file():
                if b'PLAYSTATION' in read_at(binary, 16 * 2352, 4096):
                    return 'ps1'
    return 'unknown'


def scan(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Library root must be a directory')
    errors, games, seen = [], [], set()
    def onerror(error):
        errors.append(clean_field(str(error)))
    for directory, subdirs, filenames in os.walk(root, followlinks=False, onerror=onerror):
        subdirs[:] = sorted(name for name in subdirs if not EXCLUDED.search(name) and not (Path(directory) / name).is_symlink())
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix.lower() not in EXTENSIONS or EXCLUDED.search(filename) or path.is_symlink():
                continue
            if any(ord(char) < 32 or ord(char) == 127 for char in str(path)):
                errors.append('Skipped path containing control characters: ' + clean_field(path))
                continue
            try:
                stat = path.stat()
                if not path.is_file() or stat.st_size == 0:
                    continue
                system = identify_system(path)
                title = display_title(rom_stem(path))
                if not title:
                    continue
                identity = (system, match_key(title), stat.st_size)
                if identity in seen:
                    continue
                seen.add(identity)
                game_id = hashlib.sha256(('\0'.join(map(str, identity))).encode()).hexdigest()[:20]
                games.append(dict(id=game_id, title=title, system_id=system,
                                  rom_path=str(path), size=stat.st_size,
                                  cover_texture_path='', cover_source='', cover_status='missing'))
            except (OSError, ValueError) as error:
                errors.append(clean_field(f'{path}: {error}'))
    # Put verified recognizable titles first, while retaining every other game.
    priority = ['pokemonemeraldversion', 'mariokart64', 'papermario', 'sonicadvance3', 'supermarioworld']
    def order(game):
        key = match_key(game['title'])
        return (priority.index(key) if key in priority else len(priority), game['title'].casefold(), game['system_id'], game['rom_path'])
    return sorted(games, key=order), sorted(errors)


def local_cover(game):
    path = Path(game['rom_path'])
    candidates = []
    for extension in ('.png', '.jpg', '.jpeg', '.webp'):
        candidates.extend([path.parent / (rom_stem(path) + extension), path.with_suffix(extension),
                           path.parent / 'covers' / (rom_stem(path) + extension)])
        if match_key(display_title(path.parent.name)) == match_key(game['title']):
            candidates.extend([path.parent / ('cover' + extension), path.parent / ('front' + extension)])
    return next((candidate for candidate in candidates if candidate.is_file() and not candidate.is_symlink()), None)


def request_bytes(url, maximum):
    request = urllib.request.Request(url, headers={'User-Agent': 'surface-go-private-library/1.0', 'Accept': 'application/vnd.github+json' if 'api.github.com' in url else '*/*'})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read(maximum + 1)
            break
        except urllib.error.HTTPError as error:
            if attempt or error.code not in {429, 500, 502, 503, 504}:
                raise
            time.sleep(0.3)
    if len(data) > maximum:
        raise ValueError('Download exceeded size bound')
    return data


def cover_index(system, cache, fetch):
    repository = REPOSITORIES.get(system)
    if not repository:
        return []
    index_path = cache / (system + '-index.json')
    if index_path.exists():
        return json.loads(index_path.read_text())
    if not fetch:
        return []
    url = f'https://api.github.com/repos/libretro-thumbnails/{repository}/git/trees/master?recursive=1'
    tree = json.loads(request_bytes(url, 20 * 1024 * 1024))
    if tree.get('truncated'):
        raise ValueError('GitHub cover tree is truncated; refusing incomplete matching')
    index = sorted(item['path'] for item in tree['tree'] if item['type'] == 'blob' and item['path'].startswith('Named_Boxarts/') and item['path'].lower().endswith('.png'))
    index_path.write_text(json.dumps(index))
    return index


def select_cover(game, index):
    wanted = match_key(game['title'])
    matches = [name for name in index if match_key(display_title(Path(name).stem)) == wanted]
    # Exact normalized title and exact system only; no fuzzy/sequel matching.
    if not matches:
        return None
    rom_name = rom_stem(game['rom_path'])
    def score(name):
        stem = Path(name).stem
        region = next((part[1] for part in re.finditer(r'\(([^()]+)\)', rom_name) if REGION.fullmatch(part[1])), '')
        return (stem.casefold() != rom_name.casefold(), bool(region) and region.casefold() not in stem.casefold(), 'USA' not in stem, stem)
    return min(matches, key=score)


def convert_cover(source, target):
    from PIL import Image, ImageOps, ImageFilter, ImageEnhance
    with Image.open(source) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError('Cover image too large')
        image = ImageOps.exif_transpose(image).convert('RGBA')
        # Preserve the complete box front; extend its colors behind landscape boxes.
        canvas = ImageOps.fit(image,(400,480),method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(22))
        canvas = ImageEnhance.Brightness(canvas).enhance(0.42)
        image.thumbnail((400, 480), Image.Resampling.LANCZOS)
        canvas.alpha_composite(image, ((400 - image.width) // 2, (480 - image.height) // 2))
        target.write_bytes(b'SGOTEX01' + struct.pack('<II', 400, 480) + canvas.tobytes())


def prepare_covers(games, output, cache, fetch=False, maximum_downloads=32):
    covers = output / 'covers'
    covers.mkdir(parents=True, exist_ok=True, mode=0o700)
    indexes, errors = {}, []
    systems = sorted(set(game['system_id'] for game in games))
    def load(system):
        try:
            return system, cover_index(system, cache, fetch), None
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            return system, [], f'{system} cover index: {error}'
    with ThreadPoolExecutor(max_workers=4) as pool:
        for system, index, error in pool.map(load, systems):
            indexes[system] = index
            if error:
                errors.append(clean_field(error))
    jobs, downloads = [], 0
    for game in games:
        source = local_cover(game)
        url = ''
        if source is None:
            match = select_cover(game, indexes.get(game['system_id'], []))
            if match:
                url = f"https://raw.githubusercontent.com/libretro-thumbnails/{REPOSITORIES[game['system_id']]}/master/" + urllib.parse.quote(match)
                source = cache / (game['system_id'] + '-' + hashlib.sha256(url.encode()).hexdigest()[:20] + '.png')
                if not source.exists():
                    if not fetch or downloads >= maximum_downloads:
                        continue
                    downloads += 1
        if source is not None:
            jobs.append((game, source, url))
    def convert(job):
        game, source, url = job
        try:
            if not source.exists():
                source.write_bytes(request_bytes(url, 10 * 1024 * 1024))
            target = covers / (game['id'] + '.sgo')
            convert_cover(source, target)
            game.update(cover_texture_path=str(target), cover_source=url or str(source), cover_status='available')
            return None
        except (OSError, ValueError, urllib.error.URLError) as error:
            return clean_field(f"{game['title']} cover: {error}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        errors.extend(error for error in pool.map(convert, jobs) if error)
    return sorted(errors)


def write_catalog(games, errors, root, output):
    fields = ('id', 'title', 'system_id', 'rom_path', 'cover_texture_path')
    content = ''.join('\t'.join(clean_field(game[field]) for field in fields) + '\n' for game in games)
    (output / 'catalog.tsv').write_text(content, encoding='utf-8')
    (output / 'catalog.json').write_text(json.dumps(dict(schema=1, root=str(root), games=games,
        errors=errors, policy='Private references only; no ROM data copied. Cover art is not licensed for redistribution by this project.'), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/run/media/Luls/Games'))
    parser.add_argument('--output', type=Path, default=PROJECT / 'artifacts/library')
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--fetch-covers', action='store_true', help='Opt in to bounded GitHub cover/index downloads')
    parser.add_argument('--max-cover-downloads', type=int, default=32)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        output = args.output.resolve()
        if output == root or output.is_relative_to(root):
            raise ValueError('Output must be outside the read-only library')
        cache = args.cache.resolve()
        if cache == root or cache.is_relative_to(root):
            raise ValueError('Artwork cache must be outside the read-only library')
        # Avoid accidentally adding proprietary library metadata/artwork to source.
        if output.is_relative_to(PROJECT) and not output.is_relative_to(PROJECT / 'artifacts'):
            raise ValueError('Project-local output must be in ignored artifacts/')
        if args.fetch_covers:
            if args.cache == DEFAULT_CACHE and not os.path.ismount('/run/media/system/Files'):
                raise ValueError('FILES drive must be mounted before downloading art')
            args.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not 0 <= args.max_cover_downloads <= 200:
            raise ValueError('Cover download bound must be 0..200')
        games, errors = scan(root)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        errors.extend(prepare_covers(games, output, args.cache, args.fetch_covers, args.max_cover_downloads))
        write_catalog(games, sorted(errors), root, output)
        print(json.dumps(dict(catalog=str(output / 'catalog.tsv'), games=len(games), covers=sum(bool(game['cover_texture_path']) for game in games), warnings=len(errors))))
        return 0
    except (OSError, ValueError) as error:
        print(f'Library scan refused: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
