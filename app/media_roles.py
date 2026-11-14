"""Conservative main-video/extra classification. No file is moved or removed."""
import os
import re
from pathlib import Path

EXTRA_DIRS = {'extras','trailers','samples','behind the scenes','behindthescenes','deleted scenes','deletedscenes','interviews','scenes','shorts','featurettes','clips','other','theme-music','backdrops'}
MARKERS = {'sample','trailer','extra','featurette','behindthescenes','deleted','interview','clip','short','scene','theme'}


def folder_title(value):
    value = re.sub(r'[\[(](?:19|20)\d{2}[\])]', '', value)
    return re.sub(r'[^\w]', '', value.casefold())


def classify_media(folder: Path, extensions: set[str]) -> dict:
    main, extras, ambiguous = [], [], []
    for entry in sorted(folder.iterdir(), key=lambda p:p.name.casefold()):
        if entry.is_symlink():
            continue
        if entry.is_file() and entry.suffix.lower() in extensions:
            stem = entry.stem.casefold()
            marker = next((m for m in MARKERS if stem == m or re.search(r'[-._]' + m + r'$', stem)), None)
            looks_like_title = folder_title(entry.stem) == folder_title(folder.name)
            if marker and not looks_like_title:
                extras.append({'name':entry.name,'category':marker,'reason':'Recognized standalone/suffixed extra'})
            else:
                main.append(entry)
                if marker or re.search(r'(?:^|[ ._-])(?:sample|trailer)(?:[ ._-]|$)', stem):
                    ambiguous.append(entry.name)
        elif entry.is_dir() and entry.name.casefold() in EXTRA_DIRS:
            for directory, directories, files in os.walk(entry, followlinks=False):
                directories[:] = [name for name in directories if not (Path(directory)/name).is_symlink()]
                for name in sorted(files):
                    path = Path(directory)/name
                    if path.is_file() and not path.is_symlink() and path.suffix.lower() in extensions and folder.resolve() in path.resolve().parents:
                        extras.append({'name':path.relative_to(folder).as_posix(),'category':entry.name,'reason':'Inside an extras directory'})
    if not main:
        # A title such as "Sample" must not disappear just because its only file
        # resembles an extra. Keep root-level candidates flagged, never auto-selected.
        uncertain=[entry for entry in extras if '/' not in entry['name']]
        for entry in uncertain:
            main.append(folder/entry['name'])
            ambiguous.append(entry['name'])
            extras.remove(entry)
    if len(main)>1:
        ambiguous = list(dict.fromkeys([*ambiguous, *(p.name for p in main)]))
    return {'main':main,'extras':extras,'ambiguous':ambiguous}


def selected_main_files(folder, extensions, requested=None):
    roles=classify_media(folder,extensions)
    candidates={p.name:p for p in roles['main']}
    if requested is None:
        if roles['ambiguous']:
            raise ValueError('Choose the main movie file(s) explicitly before renaming; multiple versions or possible extras were found.')
        selected=list(candidates)
    else:
        if len(requested)!=len(set(requested)) or any(name not in candidates or Path(name).name!=name for name in requested):
            raise ValueError('Selection contains a non-main video, duplicate, extra or unsafe filename.')
        selected=requested
    if not selected:
        raise ValueError('Select at least one main movie file before renaming.')
    return sorted((candidates[name] for name in selected),key=lambda p:p.name.casefold())
