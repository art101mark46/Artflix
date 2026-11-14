"""Filesystem snapshots, safe backup lookup, and library health calculations."""
import hashlib
import json
import os
from pathlib import Path

METADATA_SUFFIXES = {'.nfo', '.jpg', '.jpeg', '.png', '.webp'}


def file_signature(path):
    if path.is_symlink():
        return {'symlink': os.readlink(path)}
    if not path.exists():
        return None
    stat = path.stat()
    return {'size': stat.st_size, 'mtime': stat.st_mtime_ns, 'inode': stat.st_ino}


def folder_snapshot(folder):
    result = {}
    for entry in folder.iterdir():
        result[entry.name] = file_signature(entry)
        if entry.name in {'actors', '.actors'} and entry.is_dir() and not entry.is_symlink():
            for image in entry.iterdir():
                result[entry.name + '/' + image.name] = file_signature(image)
    return result


def safe_metadata_relative(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or path.suffix.lower() not in METADATA_SUFFIXES:
        raise ValueError('Only NFO and artwork files can be restored')
    if len(path.parts) > 2 or (len(path.parts) == 2 and path.parts[0] not in {'actors', '.actors'}):
        raise ValueError('Invalid metadata backup path')
    return path


def metadata_values(data):
    import xml.etree.ElementTree as ET
    doc = ET.fromstring(data)
    values = {key: (doc.findtext(tag) or '').strip() for key, tag in {'title':'title', 'original_title':'originaltitle', 'tmdb_id':'tmdbid', 'year':'year', 'release_date':'premiered', 'runtime':'runtime', 'tagline':'tagline', 'plot':'plot', 'certificate':'mpaa'}.items()}
    if not values['tmdb_id']:
        values['tmdb_id'] = next(((e.text or '').strip() for e in doc.findall('uniqueid') if e.get('type', '').lower() == 'tmdb'), '')
    values['set'] = (doc.findtext('set/name') or doc.findtext('set') or '').strip()
    for key, tag in {'genres':'genre', 'directors':'director', 'writers':'credits', 'studios':'studio', 'actors':'actor/name'}.items():
        values[key] = ', '.join(e.text.strip() for e in doc.findall(tag) if e.text and e.text.strip())
    return values


def metadata_differences(before, after):
    return [{'field': key, 'before': before.get(key, ''), 'after': after.get(key, '')} for key in dict.fromkeys([*before, *after]) if before.get(key, '') != after.get(key, '')]


def movie_tmdb_id(movie):
    raw = str((movie.get('metadata') or {}).get('tmdbid') or (movie.get('searchable') or {}).get('tmdbid') or '').strip()
    return str(int(raw)) if raw.isdigit() and int(raw) > 0 else None


def library_health(movies):
    groups = {}
    for movie in movies:
        key = movie_tmdb_id(movie)
        if key:
            groups.setdefault(key, []).append({'folder': movie['folder'], 'name': movie['name']})
    duplicates = [{'tmdb_id': key, 'movies': value} for key, value in groups.items() if len(value) > 1]
    return {'total': len(movies), 'unmatched': sum(not movie_tmdb_id(m) for m in movies),
            'missing_poster': sum('poster' in m.get('missing_fields', []) for m in movies),
            'missing_fanart': sum('fanart' in m.get('missing_fields', []) for m in movies),
            'missing_certificate': sum('certificate' in m.get('missing_fields', []) for m in movies),
            'incomplete': sum(m.get('metadata_status') != 'complete' for m in movies),
            'unreadable': sum(bool(m.get('metadata', {}).get('error')) or any(v.get('probe_note') for v in m.get('media', [])) for m in movies),
            'ambiguous_videos': sum(bool(m.get('ambiguous_videos')) for m in movies),
            'duplicate_movies': sum(len(g['movies']) for g in duplicates), 'duplicates': duplicates}
