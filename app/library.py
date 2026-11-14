"""Read existing movie metadata without modifying the library."""
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from app.media_roles import classify_media

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
SUBTITLE_EXTENSIONS = {'.srt', '.ass', '.ssa', '.sub', '.idx', '.vtt', '.sup'}
OPTIONAL_FIELDS = {'tagline', 'actor_portraits', 'subtitles'}
SCALAR_FIELDS = ('title', 'originaltitle', 'plot', 'tagline', 'year', 'premiered', 'runtime', 'mpaa', 'tmdbid')
FIELD_TAGS = {'title': 'title', 'original_title': 'originaltitle', 'tmdb_id': 'tmdbid', 'year': 'year', 'release_date': 'premiered', 'runtime': 'runtime', 'genres': 'genre', 'tagline': 'tagline', 'directors': 'director', 'writers': 'credits', 'studios': 'studio', 'plot': 'plot', 'certificate': 'mpaa', 'actors': 'actor/name'}


def metadata_document(folder: Path, video_extensions: set[str]):
    candidates = sorted((p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() == '.nfo'), key=lambda p: p.name.casefold())
    preferred = next((p for p in candidates if p.name.casefold() == 'movie.nfo'), None)
    if preferred is None:
        stems = {p.stem.casefold() for p in classify_media(folder, video_extensions)['main']}
        preferred = next((p for p in candidates if p.stem.casefold() in stems), None)
    if preferred is None and len(candidates) == 1:
        preferred = candidates[0]
    if preferred is None:
        return None, None, bool(candidates), 'Multiple NFO files found; no unambiguous movie NFO.' if candidates else ''
    try:
        doc = ET.parse(preferred).getroot()
        for element in doc.iter():
            element.tag = element.tag.rsplit('}', 1)[-1]
        if doc.tag.lower() != 'movie':
            raise ValueError('NFO is not a movie document')
        return doc, preferred.name, True, ''
    except (OSError, ET.ParseError, ValueError):
        return None, preferred.name, True, 'The existing NFO could not be read as a movie XML document.'


def read_movie_metadata(folder: Path, video_extensions: set[str], media=None) -> dict:
    doc, filename, present, error = metadata_document(folder, video_extensions)
    metadata = {}
    missing = []
    if doc is None:
        missing.append('nfo')
        doc = ET.Element('movie')
    else:
        metadata = {tag: (doc.findtext(tag) or '').strip() for tag in SCALAR_FIELDS}
        metadata['set'] = (doc.findtext('set/name') or doc.findtext('set') or '').strip()
        if not metadata.get('tmdbid'):
            metadata['tmdbid'] = next(((e.text or '').strip() for e in doc.findall('uniqueid') if e.get('type', '').lower() == 'tmdb'), '')
        for key, tag in (('genres', 'genre'), ('directors', 'director'), ('writers', 'credits'), ('studios', 'studio')):
            metadata[key] = ', '.join(e.text.strip() for e in doc.findall(tag) if e.text and e.text.strip())
    for field, tag in FIELD_TAGS.items():
        values = [e.text.strip() for e in doc.findall(tag) if e.text and e.text.strip()]
        if field == 'tmdb_id' and metadata.get('tmdbid'):
            values = [metadata['tmdbid']]
        if not values or (field in {'year', 'runtime', 'tmdb_id'} and all(v == '0' for v in values)):
            missing.append(field)
    files = [p for p in folder.iterdir() if p.is_file()]
    for kind in ('poster', 'fanart'):
        aliases = {kind, 'backdrop'} if kind == 'fanart' else {kind}
        if not any(p.suffix.lower() in IMAGE_EXTENSIONS and (p.stem.lower() in aliases or p.stem.lower().endswith('-' + kind)) for p in files):
            missing.append(kind)
    portrait_dirs = [folder / 'actors', folder / '.actors']
    if not any(d.is_dir() and not d.is_symlink() and any(p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS for p in d.iterdir()) for d in portrait_dirs):
        missing.append('actor_portraits')
    extra_stems = [Path(item['name']).stem for item in classify_media(folder, video_extensions)['extras'] if '/' not in item['name']]
    if not any(v.get('subtitles') for v in (media or [])) and not any(p.suffix.lower() in SUBTITLE_EXTENSIONS and not any(p.name.startswith(stem + '.') for stem in extra_stems) for p in files):
        missing.append('subtitles')
    meaningful = any(value for key, value in metadata.items() if key != 'error')
    status = 'none' if not meaningful else ('partial' if set(missing) - OPTIONAL_FIELDS else 'complete')
    if error:
        metadata['error'] = error
    return {'metadata': metadata, 'nfo_filename': filename, 'has_nfo': present, 'missing_fields': missing, 'metadata_status': status, 'metadata_version': 4,
            'searchable': {'title': metadata.get('title', ''), 'year': metadata.get('year', ''), 'mpaa': metadata.get('mpaa', ''),
                           'set': metadata.get('set', ''), 'genres': [e.text.strip() for e in doc.findall('genre') if e.text and e.text.strip()],
                           'actors': [e.text.strip() for e in doc.findall('actor/name') if e.text and e.text.strip()]}}


def preserve_unmanaged_nfo_fields(generated: bytes, folder: Path, video_extensions: set[str]) -> bytes:
    old, _, _, _ = metadata_document(folder, video_extensions)
    if old is None:
        return generated
    new = ET.fromstring(generated)
    old_set, new_set = old.find('set'), new.find('set')
    if old_set is not None and new_set is not None:
        old_name = (old_set.findtext('name') or old_set.text or '').strip()
        if old_name == (new_set.findtext('name') or '').strip():
            for child in old_set:
                if child.tag != 'name' and new_set.find(child.tag) is None:
                    new_set.append(deepcopy(child))
    for actor in new.findall('actor'):
        previous = next((entry for entry in old.findall('actor') if entry.findtext('name') == actor.findtext('name')), None)
        if previous is not None:
            for child in previous:
                if actor.find(child.tag) is None:
                    actor.append(deepcopy(child))
    managed = {'title', 'originaltitle', 'sorttitle', 'year', 'plot', 'tagline', 'runtime', 'premiered', 'id', 'tmdbid', 'uniqueid', 'mpaa', 'country', 'genre', 'studio', 'spokenland', 'director', 'credits', 'actor', 'set'}
    for element in old:
        if element.tag not in managed or (element.tag == "uniqueid" and element.get("type", "").lower() != "tmdb"):
            new.append(deepcopy(element))
    return ET.tostring(new, encoding='utf-8', xml_declaration=True)
