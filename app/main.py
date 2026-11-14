import json
import os
import re
import sqlite3
import shutil
import uuid
import time
import threading
import platform
import datetime
from urllib.parse import urlencode
from fractions import Fraction
from functools import lru_cache
from contextlib import contextmanager
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field

from app.encoding import FilesystemJSONResponse, valid_filename, display_filename
from app.tv_artwork import tmdb_choices, tvdb_choices
from app.television import safe_image
from app.library import read_movie_metadata, preserve_unmanaged_nfo_fields, metadata_document
from app.filesystem import rename_media_files, rename_no_replace
from app.control import EditSessions, ReviewSigner, fingerprint, COMMIT_LOCK
from app.review_selection import select_operations, subset_outputs
from app.jobs import ScanJobs
from app.media_roles import classify_media, selected_main_files
from app.version import VERSION, BUILD
from app.diagnostics import append_event, recent_events, redact
from app.operations import folder_snapshot, safe_metadata_relative, metadata_values, metadata_differences, library_health

APP_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", "/config"))
CONFIG_PATH = DATA_DIR / "settings.json"
TMDB_BASE = "https://api.themoviedb.org/3"
FANART_BASE = "https://webservice.fanart.tv/v3/movies"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".mpeg", ".mpg", ".ts", ".webm", ".m2ts"}

edit_sessions = EditSessions()
review_signer = ReviewSigner()
scan_jobs = ScanJobs()

app = FastAPI(title="ArtFlix Movie Manager", version=VERSION, default_response_class=FilesystemJSONResponse)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def load_config() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {"root_path": "/movies", "write_path": "/movies-write", "language": "en-US", "region": "", "tmdb_token": "", "fanart_key": "", "fanart_client_key": "", "overwrite": False, "filename_format": "{title} ({year})", "sort_ignore_prefixes": ["The", "A", "An"], "tv_root_path":"", "tv_write_path":"", "tvdb_key":"", "tvdb_pin":"", "tv_language":"eng"}
    if CONFIG_PATH.exists():
        try:
            saved=json.loads(CONFIG_PATH.read_text())
            cfg={**defaults, **saved}
            cfg.setdefault('tv_sort_ignore_prefixes',cfg['sort_ignore_prefixes'])
            cfg.setdefault('tv_filename_format','{show} - S{season}E{episode} - {title}')
            cfg.setdefault('tv_multi_episode_filename_format',cfg['tv_filename_format'])
            return cfg
        except (json.JSONDecodeError, OSError):
            pass
    defaults.update(tv_sort_ignore_prefixes=defaults["sort_ignore_prefixes"],tv_filename_format="{show} - S{season}E{episode} - {title}",tv_multi_episode_filename_format="{show} - S{season}E{episode} - {title}")
    return defaults


def save_config(config: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=DATA_DIR, prefix="settings-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, CONFIG_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def paths() -> tuple[Path, Path]:
    cfg = load_config()
    root = Path(cfg["root_path"]).resolve()
    write = Path(cfg["write_path"]).resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(400, "Configured scan path is not a mounted directory")
    if not write.exists() or not write.is_dir():
        raise HTTPException(400, "Configured write path is not a mounted directory")
    return root, write


def safe_folder(folder: str, base: Path) -> Path:
    candidate = (base / folder).resolve()
    if candidate == base or base not in candidate.parents or not candidate.is_dir():
        raise HTTPException(400, "Invalid movie folder")
    return candidate


class Settings(BaseModel):
    tv_root_path: str = ""
    tv_write_path: str = ""
    tvdb_key: str = ""
    tvdb_pin: str = ""
    tv_language: str = "eng"
    tv_filename_format: str = "{show} - S{season}E{episode} - {title}"
    tv_multi_episode_filename_format: str = "{show} - S{season}E{episode} - {title}"
    tv_sort_ignore_prefixes: list[str] = Field(default_factory=lambda: ["The", "A", "An"])
    tv_naming_templates: dict[str,str] = Field(default_factory=dict)
    root_path: str = Field(min_length=1)
    write_path: str = Field(min_length=1)
    language: str = "en-US"
    region: str = ""
    tmdb_token: str = ""
    fanart_key: str = ""
    fanart_client_key: str = ""
    overwrite: bool = False
    filename_format: str = "{title} ({year})"
    sort_ignore_prefixes: list[str] = Field(default_factory=lambda: ["The", "A", "An"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    year: int | None = None


class PrepareRequest(BaseModel):
    folder: str
    tmdb_id: int


class InspectRequest(BaseModel):
    folder: str


class WriteRequest(BaseModel):
    folder: str
    tmdb_id: int
    artwork: dict[str, str] = {}
    certification: str | None = None
    metadata: dict[str, str] = {}
    actor_ids: list[int] = []
    rename_videos: bool = False
    filename_format: str | None = None
    replace_existing: bool = False
    folder_name: str | None = None
    actors_only: bool = False
    artwork_only: bool = False
    review_token: str | None = None
    primary_videos: list[str] | None = None
    selected_operations: list[str] | None = None


async def tmdb_get(path: str, params: dict[str, Any] | None = None) -> dict:
    token = load_config().get("tmdb_token", "").strip()
    if not token:
        raise HTTPException(400, "Add a TMDB API Read Access Token in Settings")
    cfg = load_config()
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    query = dict(params or {})
    query.setdefault("language", cfg.get("language") or "en-US")
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(f"{TMDB_BASE}{path}", params=query, headers=headers)
    if response.is_error:
        raise HTTPException(response.status_code, f"TMDB request failed ({response.status_code})")
    return response.json()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"version": VERSION})


@app.get("/api/settings")
async def get_settings():
    cfg = load_config().copy()
    for key in ("tmdb_token", "fanart_key", "fanart_client_key", "tvdb_key", "tvdb_pin"):
        cfg[key] = ""  # Never return stored credentials to the browser.
    cfg["read_only"] = library_read_only()
    cfg["has_tmdb_token"] = bool(load_config().get("tmdb_token"))
    cfg["has_fanart_key"] = bool(load_config().get("fanart_key"))
    cfg["has_tvdb_key"] = bool(load_config().get("tvdb_key"))
    cfg["has_tvdb_pin"] = bool(load_config().get("tvdb_pin"))
    return cfg


@app.put("/api/settings")
async def put_settings(settings: Settings, request: Request):
    require_unlocked(request)
    old = load_config()
    cfg = settings.model_dump()
    # Older clients must not erase TV settings they do not send.
    for key in ("tv_root_path","tv_write_path","tv_language","tv_filename_format","tv_multi_episode_filename_format","tv_sort_ignore_prefixes","tv_naming_templates"):
        if key not in settings.model_fields_set: cfg[key]=old.get(key,cfg[key])
    if tv_services["jobs"].active() and cfg["tv_root_path"]!=old.get("tv_root_path", ""):
        raise HTTPException(409,"Finish or cancel the TV scan before changing its root.")
    for key in ("tv_root_path","tv_write_path"):
        if cfg[key]:cfg[key]=str(Path(cfg[key]).resolve())
    for key in ("tmdb_token", "fanart_key", "fanart_client_key", "tvdb_key", "tvdb_pin"):
        if not cfg[key].strip():
            cfg[key] = old.get(key, "")
        cfg[key] = cfg[key].strip()
    if scan_jobs.active() and Path(cfg["root_path"]).resolve() != Path(old["root_path"]).resolve():
        raise HTTPException(409, "Finish or cancel the current scan before changing its root.")
    cfg["root_path"] = str(Path(cfg["root_path"]).resolve())
    cfg["write_path"] = str(Path(cfg["write_path"]).resolve())
    if len(cfg["filename_format"]) > 180 or any(c in cfg["filename_format"] for c in "/\\\\"):
        raise HTTPException(400, "Filename format must be a filename pattern and no longer than 180 characters")
    fields = set(re.findall(r"\{([^{}]+)\}", cfg["filename_format"]))
    remainder = re.sub(r"\{(?:title|original_title|year|tmdb_id)\}", "", cfg["filename_format"])
    if not fields.issubset({"title", "original_title", "year", "tmdb_id"}) or "{" in remainder or "}" in remainder:
        raise HTTPException(400, "Filename format supports {title}, {original_title}, {year}, and {tmdb_id}")
    cfg["sort_ignore_prefixes"] = list(dict.fromkeys(p.strip() for p in cfg["sort_ignore_prefixes"] if p.strip()))
    cfg['tv_sort_ignore_prefixes']=list(dict.fromkeys(p.strip() for p in cfg['tv_sort_ignore_prefixes'] if p.strip()))
    for key in ('tv_filename_format','tv_multi_episode_filename_format'):
        pattern=cfg[key];remainder=re.sub(r'\{(?:show|season|episode|title)\}', '', pattern)
        if not pattern.strip() or len(pattern)>180 or any(c in pattern for c in '/\\') or '{' in remainder or '}' in remainder:
            raise HTTPException(400,'TV filename formats support {show}, {season}, {episode}, {title}.')
    save_config(cfg)
    return {"ok": True}


def missing_movie_fields(folder: Path, media: list[dict] | None = None) -> list[str]:
    return read_movie_metadata(folder, VIDEO_EXTENSIONS, media)["missing_fields"]


def library_read_only() -> bool:
    return os.getenv("ARTFLIX_READ_ONLY", "").strip().lower() in {"1", "true", "yes"}


def require_unlocked(request: Request):
    if not edit_sessions.valid(request.headers.get("X-Artflix-Edit-Token", "")):
        raise HTTPException(423, "Unlock the padlock before making changes.")


def require_library_writes(request: Request):
    if library_read_only():
        raise HTTPException(403, "Library is in read-only mode. Saving, renaming and resetting are disabled.")
    require_unlocked(request)


def backup_existing_metadata(path: Path, folder: Path):
    try:
        relative = safe_metadata_relative(path.relative_to(folder).as_posix())
    except ValueError:
        raise HTTPException(400, 'Only metadata files can enter the replacement backup workflow.')
    if path.is_symlink():
        raise HTTPException(400, 'Refusing to replace symbolic-link metadata.')
    if path.is_file():
        key = uuid.uuid4().hex
        backup = DATA_DIR / 'backups' / key / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        state = read_asset_state(folder)
        info = {'original_folder': str(folder), 'original_file': relative.as_posix(), 'created': time.time(), 'artwork_source': state.get('artwork', {}).get(relative.as_posix())}
        (DATA_DIR / 'backups' / key / 'restore-info.json').write_text(json.dumps(info, indent=2))
        with database() as db:
            db.execute('INSERT OR REPLACE INTO backup_index VALUES (?,?,?,?)', (key, str(folder), relative.as_posix(), info['created']))
        return key


def record_folder_alias(old, new):
    with database() as db:
        db.execute('INSERT OR REPLACE INTO folder_aliases VALUES (?,?)', (str(old), str(new)))
        db.execute('UPDATE backup_index SET folder=? WHERE folder=?', (str(new), str(old)))


def import_legacy_backups():
    directory = DATA_DIR / 'backups'
    if not directory.exists():
        return
    with database() as db:
        known = {row[0] for row in db.execute('SELECT id FROM backup_index')}
        aliases = dict(db.execute('SELECT old_path,new_path FROM folder_aliases'))
        for context in directory.glob('*/restore-info.json'):
            key = context.parent.name
            if key in known or not re.fullmatch(r'[0-9a-f]{32}', key) or context.is_symlink():
                continue
            try:
                info = json.loads(context.read_text())
                relative = safe_metadata_relative(info['original_file'])
                folder = info['original_folder']
                visited = set()
                while folder in aliases and folder not in visited:
                    visited.add(folder)
                    folder = aliases[folder]
                db.execute('INSERT OR IGNORE INTO backup_index VALUES (?,?,?,?)', (key, folder, relative.as_posix(), info.get('created', context.stat().st_mtime)))
            except (OSError, ValueError, KeyError, TypeError):
                continue


def get_backup(key, folder):
    if not re.fullmatch(r'[0-9a-f]{32}', key):
        raise HTTPException(400, 'Invalid backup identifier.')
    import_legacy_backups()
    with database() as db:
        row = db.execute('SELECT folder,relative_file,created FROM backup_index WHERE id=?', (key,)).fetchone()
    if not row or row[0] != str(folder):
        raise HTTPException(404, 'This backup does not belong to the selected movie.')
    try:
        relative = safe_metadata_relative(row[1])
    except ValueError:
        raise HTTPException(400, 'Invalid backup file.')
    path = DATA_DIR / 'backups' / key / relative
    backup_root = DATA_DIR / 'backups' / key
    if path.is_symlink() or backup_root.resolve() not in path.resolve().parents or not path.is_file():
        raise HTTPException(404, 'Backup file is unavailable.')
    return {'id':key, 'relative':relative, 'path':path, 'created':row[2]}


@app.get('/api/backups')
def list_backups(folder: str):
    _, write = paths()
    target = safe_folder(folder, write)
    import_legacy_backups()
    with database() as db:
        rows = db.execute('SELECT id,relative_file,created FROM backup_index WHERE folder=? ORDER BY created DESC', (str(target),)).fetchall()
    return {'backups':[{'id':key,'file':name,'created':created} for key,name,created in rows if (DATA_DIR / 'backups' / key / name).is_file()]}


class RestoreRequest(BaseModel):
    folder: str
    backup_id: str
    review_token: str | None = None


def restore_plan(req):
    _, write = paths()
    folder = safe_folder(req.folder, write)
    backup = get_backup(req.backup_id, folder)
    destination = folder / backup['relative']
    if destination.is_symlink() or folder not in destination.resolve().parents:
        raise HTTPException(400, 'Unsafe restore destination.')
    if destination.parent != folder and (destination.parent.is_symlink() or (destination.parent.exists() and not destination.parent.is_dir())):
        raise HTTPException(400, 'Unsafe actor directory.')
    content = backup['path'].read_bytes()
    before, after = {}, {}
    if backup['relative'].suffix.lower() == '.nfo':
        try:
            before = metadata_values(destination.read_bytes()) if destination.is_file() else {}
        except (OSError, ET.ParseError):
            before = {}
        try:
            after = metadata_values(content)
        except ET.ParseError:
            after = {'document':'Previous unreadable NFO (restored exactly)'}
    else:
        after = {'image':backup['relative'].as_posix()}
    digest = fingerprint({'folder':str(folder), 'backup':req.backup_id, 'snapshot':folder_snapshot(folder), 'content':fingerprint(content.hex())})
    return {'folder':folder,'destination':destination,'backup':backup,'content':content,'fingerprint':digest,
            'public':{'folder_before':req.folder,'folder_after':req.folder,'metadata':metadata_differences(before,after), 'renames':[],
                      'files':[{'path':backup['relative'].as_posix(),'action':'restore previous version',
                                'before_url':local_image_url(req.folder,backup['relative'].as_posix()) if backup['relative'].suffix.lower() != '.nfo' and destination.exists() else None,
                                'after_url':'/api/backups/image?'+urlencode({'folder':req.folder,'backup_id':req.backup_id}) if backup['relative'].suffix.lower() != '.nfo' else None}]}}


@app.post('/api/backups/preview')
def preview_restore(req: RestoreRequest):
    plan = restore_plan(req)
    return {**plan['public'], 'selection_supported':True, 'review_token':review_signer.sign(plan['fingerprint'])}


@app.get('/api/backups/image')
def backup_image(folder: str, backup_id: str):
    _, write = paths()
    backup = get_backup(backup_id, safe_folder(folder, write))
    if backup['relative'].suffix.lower() == '.nfo':
        raise HTTPException(400, 'Backup is not an image.')
    return FileResponse(backup['path'])


@app.post('/api/backups/restore')
def restore_backup(req: RestoreRequest, request: Request):
    require_library_writes(request)
    with COMMIT_LOCK:
        plan = restore_plan(req)
        if not req.review_token or not review_signer.valid(req.review_token, plan['fingerprint']):
            raise HTTPException(409, 'Review the current restore operation before confirming.')
        backup_existing_metadata(plan['destination'], plan['folder'])
        plan['destination'].parent.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=plan['destination'].parent, prefix='.restore-', delete=False) as stream:
            stream.write(plan['content'])
            temporary = stream.name
        os.replace(temporary, plan['destination'])
        context = DATA_DIR / 'backups' / req.backup_id / 'restore-info.json'
        try:
            original = json.loads(context.read_text())
            if original.get('artwork_source'):
                state = read_asset_state(plan['folder'])
                state.setdefault('artwork', {})[plan['backup']['relative'].as_posix()] = original['artwork_source']
                save_asset_state(plan['folder'], state)
        except (OSError, ValueError):
            pass
        refresh_index_entry(req.folder)
    return {'ok':True, 'folder':req.folder, 'written':['Restored '+plan['backup']['relative'].as_posix()]}


def scan_library(progress=None, cancelled=None):
    progress = progress or (lambda **kwargs: None)
    cancelled = cancelled or (lambda: False)
    root, write = paths()
    movies, warnings = [], []
    folders = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()), key=lambda p: p.name.casefold())
    with database() as db:
        known_paths = {row[0] for row in db.execute("SELECT path FROM library").fetchall()}
    new_count = 0
    progress(total=len(folders), checked=0, found=0, new=0, errors=0)
    for number, folder in enumerate(folders, 1):
        if cancelled():
            break
        progress(current=display_filename(folder.name))
        try:
            invalid=next((p for p in [folder,*folder.iterdir()] if not valid_filename(p)),None)
            if invalid is not None:
                warnings.append({'folder':folder.name,'error':'Skipped unsupported filename bytes: '+display_filename(invalid.name)+'. Files were left untouched.'})
                continue
            if root not in folder.resolve().parents or write not in (write / folder.relative_to(root)).resolve().parents:
                continue
            files = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink()]
            roles = classify_media(folder, VIDEO_EXTENSIONS)
            videos = [p.name for p in roles["main"]]
            if not videos:
                continue
            media = []
            for path in files:
                if cancelled():
                    break
                if path in roles["main"]:
                    info = video_info(path)
                    media.append(info)
                    if info.get("probe_note"):
                        warnings.append({"folder": folder.name, "error": path.name + ": " + info["probe_note"]})
            if cancelled():
                break
            existing = read_movie_metadata(folder, VIDEO_EXTENSIONS, media)
            movie = {**existing, "extras": roles["extras"], "ambiguous_videos": roles["ambiguous"], "folder": folder.relative_to(root).as_posix(), "name": folder.name, "videos": videos, "media": media,
                     "assets": sorted(p.name for p in files if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".nfo"})}
            movies.append(movie)
            new_count += str(folder) not in known_paths
            with database() as db:
                db.execute("INSERT OR REPLACE INTO library VALUES (?,?)", (str(folder), json.dumps(movie)))
        except OSError as error:
            warnings.append({"folder": folder.name, "error": str(error)})
        finally:
            progress(checked=number, found=len(movies), new=new_count, errors=len(warnings), warnings=warnings[-20:])
    stopped = cancelled()
    if not stopped:
        with database() as db:
            current_paths = {str(root / movie["folder"]) for movie in movies}
            skipped = {str(root / item["folder"]) for item in warnings}
            for (old_path,) in db.execute("SELECT path FROM library").fetchall():
                if Path(old_path).parent == root and old_path not in current_paths and old_path not in skipped:
                    db.execute("DELETE FROM library WHERE path=?", (old_path,))
    return {"count": len(movies), "root": str(root), "warnings": warnings, "cancelled": stopped}


@app.get("/api/scan")
def scan():
    if scan_jobs.active():
        raise HTTPException(409, "A background scan is already running.")
    result = scan_library()
    return {**stored_library(), "warnings": result["warnings"]}


@app.post("/api/scan/start")
def start_scan():
    paths()
    return scan_jobs.start(scan_library)


@app.get("/api/scan/status")
def scan_status():
    return scan_jobs.status()


@app.post("/api/scan/cancel")
def cancel_scan():
    return scan_jobs.cancel()


@app.post("/api/search")
async def search(req: SearchRequest):
    cfg = load_config()
    params: dict[str, Any] = {"query": req.query.strip()}
    if req.year:
        params["year"] = req.year
    data = await tmdb_get("/search/movie", params)
    return {"results": data.get("results", [])[:12]}


@app.get("/api/movie/{tmdb_id}")
async def movie_details(tmdb_id: int):
    cfg = load_config()
    details = await tmdb_get(f"/movie/{tmdb_id}", {"append_to_response": "release_dates,images,credits", "include_image_language": f"{cfg.get('language','en-US')[:2]},null"})
    cache_provider(f"movie:{tmdb_id}:{cfg.get('language', 'en-US')}", details)
    region = cfg.get("region", "").upper()
    certifications: list[str] = []
    releases = details.get("release_dates", {}).get("results", [])
    release = next((r for r in releases if r.get("iso_3166_1") == region), None) if region else None
    if release:
        certifications = list(dict.fromkeys(x.get("certification") for x in release.get("release_dates", []) if x.get("certification")))
    return {"details": details, "certifications": certifications, "certification": certifications[0] if certifications else "", "region": region}


async def fanart_movie_artwork(tmdb_id: int):
    cfg = load_config()
    api_key = cfg.get("fanart_key", "").strip()
    if not api_key:
        return {"artwork": [], "notice": "Set a fanart.tv API key to load artwork."}
    params = {"api_key": api_key}
    if cfg.get("fanart_client_key"):
        params["client_key"] = cfg["fanart_client_key"]
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(f"{FANART_BASE}/{tmdb_id}", params=params)
    if response.is_error:
        return {"artwork": [], "notice": f"fanart.tv returned {response.status_code}."}
    data = response.json()
    found = []
    for key, filename in (("movieposter", "poster.jpg"), ("moviethumb", "landscape.jpg"), ("moviebackground", "fanart.jpg"), ("moviedisc", "disc.png"), ("hdmovieclearart", "clearart.png"), ("hdmovielogo", "clearlogo.png")):
        for item in data.get(key, []):
            url = item.get("url")
            if url and safe_image(url):
                found.append({"type": key, "name": filename, "url": url, "lang": item.get("lang", "")})
    return {"artwork": found, "notice": ""}


@app.get('/api/artwork/{tmdb_id}')
async def artwork(tmdb_id:int):
    if tmdb_id<1:raise HTTPException(400,'Choose a TMDB movie first.')
    cfg=load_config();found=[];notices=[]
    try:
        result=await fanart_movie_artwork(tmdb_id)
        found.extend({**item,'provider':'fanart.tv'} for item in result['artwork'])
        if result.get('notice'):notices.append(result['notice'])
    except (httpx.HTTPError,ValueError,HTTPException):notices.append('fanart.tv artwork unavailable; other providers are still shown.')
    if cfg.get('tmdb_token'):
        try:
            images=await tmdb_get(f'/movie/{tmdb_id}/images',{'language':''})
            categories={'Posters':'movieposter','Backgrounds':'moviebackground','Logos':'hdmovielogo'}
            found.extend({**item,'type':categories[item['type']]} for item in tmdb_choices(images))
        except (httpx.HTTPError,ValueError,HTTPException):notices.append('TMDB artwork unavailable; other providers are still shown.')
    else:notices.append('Set a TMDB token to browse TMDB artwork.')
    if cfg.get('tvdb_key'):
        try:
            ids=await tmdb_get(f'/movie/{tmdb_id}/external_ids')
            imdb=ids.get('imdb_id') or ''
            if not re.fullmatch(r'tt[0-9]+',imdb):raise ValueError('No IMDb identifier')
            provider=tv_services['provider']
            results=(await provider.get('/search',{'remote_id':imdb,'type':'movie'})).get('data') or []
            matches={int(row['tvdb_id']) for row in results if row.get('type')=='movie' and str(row.get('tvdb_id','')).isdigit()}
            if len(matches)!=1:raise ValueError('No unique TVDB movie')
            identifier=matches.pop();details=(await provider.get(f'/movies/{identifier}/extended')).get('data') or {}
            if details.get('id')!=identifier:raise ValueError('Movie identity mismatch')
            rows=(await provider.get('/artwork/types')).get('data') or []
            types={str(row['id']):row.get('name','') for row in rows if 'id' in row}
            categories={'Posters':'movieposter','Backgrounds':'moviebackground','Landscapes':'moviethumb','Logos':'hdmovielogo','Clearart':'hdmovieclearart','Banners':'Banners'}
            found.extend({**item,'type':categories[item['type']]} for item in tvdb_choices(details,types))
        except (httpx.HTTPError,ValueError,HTTPException):notices.append('TVDB artwork unavailable or no unique IMDb match; images were not guessed.')
    else:notices.append('Set a TVDB key to browse TVDB artwork.')
    seen=set();unique=[]
    for item in found:
        key=(item['name'],item['url'])
        if key not in seen:seen.add(key);unique.append(item)
    return {'artwork':unique,'notice':' '.join(notices)}


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:160] or "movie"


def renamed_video_names(details: dict, videos: list[Path], pattern: str) -> dict[str, str]:
    names = {}
    values = {
        "title": details.get("title") or "movie",
        "original_title": details.get("original_title") or details.get("title") or "movie",
        "year": (details.get("release_date") or "")[:4],
        "tmdb_id": str(details.get("id", "")),
    }
    for index, path in enumerate(videos, 1):
        base = safe_filename(pattern.format_map(values))
        if len(videos) > 1:
            base = f"{base} - {index:02d}"
        names[path.name] = f"{base}{path.suffix}"
    if len(set(n.casefold() for n in names.values())) != len(names):
        raise HTTPException(400, "The filename pattern creates duplicate video names")
    return names


def connected_file_renames(folder: Path, videos: dict[str, str]) -> dict[str, str]:
    result = dict(videos)
    protected_extra_stems = [Path(item['name']).stem for item in classify_media(folder, VIDEO_EXTENSIONS)['extras'] if '/' not in item['name']]
    extensions = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".chapters", ".txt"}
    for file in folder.iterdir():
        if not file.is_file() or file.suffix.lower() not in extensions:
            continue
        if any(file.name.startswith(stem + '.') for stem in protected_extra_stems):
            continue
        matches = [(old, new) for old, new in videos.items() if file.name.startswith(Path(old).stem + ".")]
        if matches:
            old, new = max(matches, key=lambda pair: len(Path(pair[0]).stem))
            result[file.name] = Path(new).stem + file.name[len(Path(old).stem):]
    if len({name.casefold() for name in result.values()}) != len(result):
        raise HTTPException(409, "Renaming connected files would create duplicate names")
    return result


@app.post("/api/prepare")
async def prepare(req: PrepareRequest):
    root, _ = paths()
    folder = safe_folder(req.folder, root)
    details = await tmdb_get(f"/movie/{req.tmdb_id}", {"append_to_response": "release_dates,credits", "language": load_config().get("language", "en-US")})
    return {"folder": req.folder, "source_files": sorted(p.name for p in folder.iterdir() if p.is_file()), "details": details}


def read_asset_state(folder: Path) -> dict:
    try:
        data = json.loads((folder / ".artflix-assets.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "artflix.sqlite3", timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS media_cache (path TEXT PRIMARY KEY, signature TEXT, data TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS library (path TEXT PRIMARY KEY, data TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS provider_cache (key TEXT PRIMARY KEY, data TEXT, updated REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS backup_index (id TEXT PRIMARY KEY, folder TEXT, relative_file TEXT, created REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS folder_aliases (old_path TEXT PRIMARY KEY, new_path TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS saved_searches (id TEXT PRIMARY KEY, name TEXT, data TEXT, updated REAL, archived INTEGER NOT NULL DEFAULT 0)")
    try:
        with db:
            yield db
    finally:
        db.close()


def display_ratio(stream: dict) -> str:
    try:
        declared = stream.get("display_aspect_ratio", "")
        if declared and declared not in {"0:1", "N/A"}:
            ratio = Fraction(declared.replace(":", "/"))
        else:
            sample = stream.get("sample_aspect_ratio") or "1:1"
            if sample in {"N/A", "0:1"}: sample = "1:1"
            ratio = Fraction(int(stream["width"]), int(stream["height"])) * Fraction(sample.replace(":", "/"))
        return f"{ratio.numerator}:{ratio.denominator} ({float(ratio):.2f}:1)"
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        return "Unknown"


def video_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    signature = f"v3:{stat.st_size}:{stat.st_mtime_ns}"
    with database() as db:
        row = db.execute("SELECT data FROM media_cache WHERE path=? AND signature=?", (str(path), signature)).fetchone()
    if row:
        cached=json.loads(row[0])
        if not cached.get('probe_note'):return cached
    data = probe_video(path)
    if not data.get('probe_note'):
        with database() as db:
            db.execute("INSERT OR REPLACE INTO media_cache VALUES (?,?,?)", (str(path), signature, json.dumps(data)))
    return data


def probe_video(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"name": path.name, "size_bytes": path.stat().st_size, "duration": None, "video": [], "audio": [], "subtitles": []}
    try:
        result = subprocess.run(["ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=20, check=True)
        data = json.loads(result.stdout)
        try:
            duration = float(data.get("format", {}).get("duration", 0))
            info["duration"] = int(duration) if duration else None
        except (TypeError, ValueError):
            info["duration"] = None
        for stream in data.get("streams", []):
            tags = stream.get("tags", {})
            label = tags.get("title") or stream.get("codec_name", "unknown")
            item = {"codec": stream.get("codec_name", "unknown"), "language": tags.get("language", ""), "name": label}
            if stream.get("codec_type") == "video":
                item.update({"width": stream.get("width"), "height": stream.get("height"), "aspect_ratio": display_ratio(stream), "scan_type": {"progressive": "Progressive", "tt": "Interlaced", "bb": "Interlaced", "tb": "Interlaced", "bt": "Interlaced"}.get(stream.get("field_order"), "Unknown")})
                info["video"].append(item)
            elif stream.get("codec_type") == "audio":
                item["channels"] = stream.get("channels")
                info["audio"].append(item)
            elif stream.get("codec_type") == "subtitle":
                info["subtitles"].append(item)
    except FileNotFoundError:
        info["probe_note"] = "ffprobe is unavailable; technical metadata could not be read."
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        info["probe_note"] = "The video could not be probed or timed out; its file was left untouched."
    return info


@app.post("/api/inspect")
def inspect_movie(req: InspectRequest):
    root, write = paths()
    folder_name = req.folder
    source = safe_folder(folder_name, root)
    target = safe_folder(folder_name, write)
    roles = classify_media(source, VIDEO_EXTENSIONS)
    files = roles["main"]
    media = [video_info(p) for p in files]
    existing = read_movie_metadata(source, VIDEO_EXTENSIONS, media)
    nfo = existing["metadata"]
    cfg = load_config()
    saved = read_asset_state(target)
    saved["artwork"] = {name: url for name, url in saved.get("artwork", {}).items() if (target / name).is_file()}
    saved["actor_ids"] = [int(match.group(1)) for file in (target / "actors").glob("*--*.*") if (match := re.search(r"--(\d+)\.", file.name))]
    return {"extras": roles["extras"], "ambiguous_videos": roles["ambiguous"], "local": local_movie_bundle(source, req.folder, existing), "missing_fields": existing["missing_fields"], "metadata_status": existing["metadata_status"], "nfo_filename": existing["nfo_filename"], "saved_assets": saved, "folder": folder_name, "metadata": nfo, "videos": media, "folder_path": str(source), "file_paths": {p.name: str(p) for p in files}, "sidecars": [p.name for p in source.iterdir() if p.is_file() and p.suffix.lower() in {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}], "filename_format": cfg.get("filename_format", "{title} ({year})"), "output_videos": [p.name for p in target.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]}


def text(parent: ET.Element, name: str, value: Any):
    el = ET.SubElement(parent, name)
    el.text = "" if value is None else str(value)
    return el


def create_nfo(d: dict, certification: str, overrides: dict[str, str] | None = None) -> bytes:
    overrides = overrides or {}
    root = ET.Element("movie")
    title = overrides.get("title", d.get("title"))
    original_title = overrides.get("original_title", d.get("original_title"))
    release_date = overrides.get("premiered", d.get("release_date", ""))
    text(root, "title", title)
    text(root, "originaltitle", original_title)
    text(root, "sorttitle", title)
    text(root, "year", overrides.get("year", (release_date or "")[:4]))
    text(root, "plot", overrides.get("plot", d.get("overview")))
    text(root, "tagline", overrides.get("tagline", d.get("tagline")))
    text(root, "runtime", overrides.get("runtime", d.get("runtime")))
    text(root, "premiered", release_date)
    if d.get("id"):
        text(root, "id", d.get("id"))
        text(root, "tmdbid", d.get("id"))
        text(root, "uniqueid", d.get("id")).set("type", "tmdb")
    text(root, "mpaa", certification)
    collection = d.get("belongs_to_collection") or {}
    set_name = overrides.get("set", collection.get("name", "")).strip()
    if set_name:
        group = ET.SubElement(root, "set")
        text(group, "name", set_name)
    for country in d.get("production_countries", []):
        text(root, "country", country.get("name"))
    genres = [x.strip() for x in overrides.get("genres", "").split(",") if x.strip()] if "genres" in overrides else [g.get("name", "") for g in d.get("genres", [])]
    for genre in genres:
        text(root, "genre", genre)
    studios = [x.strip() for x in overrides.get("studios", "").split(",") if x.strip()] if "studios" in overrides else [c.get("name", "") for c in d.get("production_companies", [])]
    for studio in studios:
        text(root, "studio", studio)
    for lang in d.get("spoken_languages", []):
        text(root, "spokenland", lang.get("english_name") or lang.get("name"))
    credits = d.get("credits", {})
    if "directors" in overrides:
        for director in [x.strip() for x in overrides["directors"].split(",") if x.strip()]:
            text(root, "director", director)
    else:
        for crew in credits.get("crew", []):
            if crew.get("job") in {"Director", "Writer", "Screenplay", "Story"}:
                el = text(root, "director" if crew["job"] == "Director" else "credits", crew.get("name"))
                el.set("department", crew.get("job", ""))
    if "writers" in overrides:
        for writer in [x.strip() for x in overrides["writers"].split(",") if x.strip()]:
            text(root, "credits", writer)
    for cast in credits.get("cast", []):
        el = text(root, "actor", "")
        text(el, "name", cast.get("name"))
        text(el, "role", cast.get("character"))
        text(el, "order", cast.get("order"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def validate_filename_pattern(pattern):
    fields = set(re.findall(r'\{([^{}]+)\}', pattern))
    remainder = re.sub(r'\{(?:title|original_title|year|tmdb_id)\}', '', pattern)
    if len(pattern) > 180 or any(c in pattern for c in '/\\') or not fields.issubset({'title','original_title','year','tmdb_id'}) or '{' in remainder or '}' in remainder:
        raise HTTPException(400, 'Filename format supports {title}, {original_title}, {year}, and {tmdb_id}, without paths.')


def write_details(req, source):
    cached = cached_movie(req.tmdb_id) if req.tmdb_id else None
    if cached:
        return cached
    existing = read_movie_metadata(source, VIDEO_EXTENSIONS)
    local = local_movie_bundle(source, req.folder, existing)['movie']['details']
    if req.tmdb_id != local['id']:
        raise HTTPException(409, 'Refresh or search and review this TMDB movie before saving it.')
    return local


def prepare_write_plan(req):
    cfg = load_config()
    pattern = req.filename_format if req.filename_format is not None else cfg.get('filename_format', '{title} ({year})')
    validate_filename_pattern(pattern)
    root, write = paths()
    source, target = safe_folder(req.folder, root), safe_folder(req.folder, write)
    details = write_details(req, source)
    new_name = req.folder_name.strip() if req.folder_name and not req.actors_only and not req.artwork_only else target.name
    if new_name != target.name and (new_name != safe_filename(new_name) or new_name in {'.','..'}):
        raise HTTPException(400, 'Folder name contains unsupported characters.')
    destination = target.parent / new_name
    if (destination != target or req.rename_videos) and not os.path.samefile(source, target):
        raise HTTPException(400, 'Renaming requires scan and write paths to map to the same movie folder.')
    if destination != target and os.path.lexists(destination):
        raise HTTPException(409, 'A folder with that name already exists.')
    region = cfg.get('region', '').upper()
    certificate = req.certification
    if certificate is None:
        certificate = next((entry.get('certification') for release in details.get('release_dates', {}).get('results', []) if release.get('iso_3166_1') == region for entry in release.get('release_dates', []) if entry.get('certification')), '')
    writes = {} if req.actors_only or req.artwork_only else {'movie.nfo': preserve_unmanaged_nfo_fields(create_nfo(details, certificate, req.metadata), source, VIDEO_EXTENSIONS)}
    selected_art = {} if req.actors_only else req.artwork
    for name, url in selected_art.items():
        if name not in {'poster.jpg','landscape.jpg','fanart.jpg','disc.png','clearart.png','clearlogo.png','banner.jpg'} or not safe_image(url):
            raise HTTPException(400, 'Unsupported artwork selection.')
    cast = {int(person['id']): person for person in details.get('credits', {}).get('cast', []) if person.get('id') is not None}
    actor_sources = {}
    for actor_id in set(req.actor_ids):
        person = cast.get(actor_id)
        if not person:
            raise HTTPException(400, 'Selected actor is not in the reviewed cast.')
        profile = person.get('profile_path')
        if not profile or not re.fullmatch(r'/[A-Za-z0-9_.-]+', profile):
            raise HTTPException(400, 'Refresh the movie to obtain this actor’s downloadable portrait.')
        ext = Path(profile).suffix.lower()
        if ext not in {'.jpg','.jpeg','.png','.webp'}:
            ext = '.jpg'
        name = f"actors/{safe_filename(person.get('name', 'actor'))}--{actor_id}{ext}"
        actor_sources[name] = 'https://image.tmdb.org/t/p/w185' + profile
    actor_dir = target / 'actors'
    if actor_sources and (actor_dir.is_symlink() or (actor_dir.exists() and not actor_dir.is_dir())):
        raise HTTPException(400, 'The actors folder must be a normal directory.')
    rename_map = {}
    if req.rename_videos and not req.actors_only and not req.artwork_only:
        try:
            videos = selected_main_files(source, VIDEO_EXTENSIONS, req.primary_videos)
        except ValueError as error:
            raise HTTPException(409, str(error))
        names = {**details, 'title': req.metadata.get('title', details.get('title')), 'original_title': req.metadata.get('original_title', details.get('original_title')), 'release_date': req.metadata.get('year', details.get('release_date', ''))}
        rename_map = connected_file_renames(source, renamed_video_names(names, videos, pattern))
        for old, new in rename_map.items():
            if not (target / old).is_file() or (target / old).is_symlink():
                raise HTTPException(409, 'A scanned video or subtitle is unavailable in the write path.')
            if old != new and os.path.lexists(target / new):
                raise HTTPException(409, 'Rename would replace an existing file: ' + new)
    names = [*writes, *selected_art, *actor_sources]
    for name in names:
        relative = safe_metadata_relative(name)
        path = target / relative
        if path.is_symlink() or target not in path.resolve().parents:
            raise HTTPException(400, 'Invalid metadata output path.')
        if path.exists() and not (cfg.get('overwrite') or req.replace_existing):
            raise HTTPException(409, 'Existing file protected: ' + name + '. Enable replacement and review again.')
    if (target / '.artflix-assets.json').is_symlink():
        raise HTTPException(400, 'Invalid artwork source record.')
    before_doc, _, _, _ = metadata_document(source, VIDEO_EXTENSIONS)
    before = metadata_values(ET.tostring(before_doc)) if before_doc is not None else {}
    after = metadata_values(writes['movie.nfo']) if 'movie.nfo' in writes else before
    actions = [{'path': name, 'action': 'replace' if (target / name).exists() else 'create', 'before_url': local_image_url(req.folder, name) if (source / name).is_file() and Path(name).suffix.lower() != '.nfo' else None,
                'after_url': selected_art.get(name) or actor_sources.get(name)} for name in names]
    changes = metadata_differences(before, after)
    public = {'folder_before': req.folder, 'folder_after': destination.relative_to(write).as_posix(), 'metadata': changes, 'files': actions,
              'renames': [{'before': old, 'after': new} for old,new in rename_map.items() if old != new]}
    digest = fingerprint({'request': req.model_dump(exclude={'review_token','selected_operations'}), 'root': str(root), 'write': str(write), 'config': cfg,
                          'snapshot': folder_snapshot(target), 'source_snapshot': folder_snapshot(source), 'details': details, 'public': public})
    return {'cfg':cfg, 'pattern':pattern, 'source':source, 'target':target, 'destination':destination, 'write_root':write, 'writes':writes, 'art':selected_art, 'actors':actor_sources,
            'renames':rename_map, 'fingerprint':digest, 'public':public}


@app.post('/api/write/preview')
def preview_write(req: WriteRequest):
    plan = prepare_write_plan(req)
    return {**plan['public'], 'selection_supported':True, 'review_token': review_signer.sign(plan['fingerprint'])}


async def download_image(client, url, maximum=25 * 1024 * 1024):
    async with client.stream('GET', url) as response:
        if response.status_code < 200 or response.status_code >= 300:
            raise HTTPException(502, 'Image provider returned an error.')
        if not response.headers.get('content-type','').startswith('image/'):
            raise HTTPException(400, 'Download is not an image.')
        chunks, total = [], 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > maximum:
                raise HTTPException(413, 'Image exceeds the size limit.')
            chunks.append(chunk)
        return b''.join(chunks)


@app.post('/api/write')
async def write_metadata(req: WriteRequest, request: Request):
    require_library_writes(request)
    plan = prepare_write_plan(req)
    if not req.review_token or not review_signer.valid(req.review_token, plan['fingerprint']):
        raise HTTPException(409, 'Review the current changes before saving. Files or settings may have changed.')
    selected=select_operations(req.selected_operations,[*plan['writes'],*plan['art'],*plan['actors']],plan['renames'],str(plan['target']),str(plan['destination']))
    writes,images,moves,_=subset_outputs(plan['writes'],{**plan['art'],**plan['actors']},plan['renames'],selected)
    selected_art={name:url for name,url in plan['art'].items() if 'file:'+name in selected}
    async with httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
        for name, url in images.items():
            writes[name] = await download_image(client, url)
    with COMMIT_LOCK:
        require_library_writes(request)
        current = prepare_write_plan(req)
        if not review_signer.valid(req.review_token, current['fingerprint']):
            raise HTTPException(409, 'The movie changed while images were downloading. Review again.')
        plan['renames']=moves
        plan['art']=selected_art
        if 'rename:folder' not in selected:plan['destination']=plan['target']
        target = plan['target']
        for name in writes:
            backup_existing_metadata(target / name, target)
        for name, data in writes.items():
            destination = target / name
            destination.parent.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.metadata-', delete=False) as stream:
                stream.write(data)
                temporary = stream.name
            os.replace(temporary, destination)
        if plan['renames']:
            try:
                rename_media_files(target, plan['renames'])
            except OSError as error:
                raise HTTPException(409, f'Metadata was saved but renaming could not finish without overwriting a path: {error}')
        state = read_asset_state(target)
        state['artwork'] = {**state.get('artwork', {}), **plan['art']}
        save_asset_state(target, state)
        if plan['destination'] != target:
            try:
                rename_no_replace(target, plan['destination'])
            except OSError as error:
                raise HTTPException(409, f'Metadata was saved but the folder could not be safely renamed: {error}')
            record_folder_alias(target, plan['destination'])
        if plan['cfg'].get('filename_format') != plan['pattern']:
            plan['cfg']['filename_format'] = plan['pattern']
            save_config(plan['cfg'])
        folder = plan['destination'].relative_to(plan['write_root']).as_posix()
        refresh_index_entry(folder, req.folder)
    written = list(writes) + [f'{old} → {new}' for old,new in plan['renames'].items() if old != new]
    return {'ok':True, 'folder':folder, 'written':written}


def save_asset_state(folder, state):
    destination = folder / '.artflix-assets.json'
    if destination.is_symlink():
        raise HTTPException(400, 'Invalid artwork source record.')
    with tempfile.NamedTemporaryFile(mode='w', dir=folder, prefix='.artflix-', delete=False) as stream:
        json.dump(state, stream)
        temporary = stream.name
    os.replace(temporary, destination)


@app.get("/health")
async def health():
    return {"ok": True}


class ResetRequest(BaseModel):
    folder: str
    confirm: bool = False
    expected_files: list[str] | None = None


def reset_candidates(folder: Path) -> list[Path]:
    # Strict positive allowlist: video and subtitle extensions are never eligible.
    allowed = {".nfo", ".jpg", ".jpeg", ".png", ".webp"}
    result = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and (p.suffix.lower() in allowed or p.name == ".artflix-assets.json")]
    for actors in (folder / "actors", folder / ".actors"):
        if actors.is_dir() and not actors.is_symlink():
            result.extend(p for p in actors.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    return sorted(result)


@app.post("/api/reset")
async def reset_movie(req: ResetRequest, request: Request):
    if req.confirm:
        require_library_writes(request)
    root, write = paths()
    source = safe_folder(req.folder, root)
    target = safe_folder(req.folder, write)
    with COMMIT_LOCK:
        candidates = reset_candidates(target)
        names = [p.relative_to(target).as_posix() for p in candidates]
        if req.confirm:
            require_library_writes(request)
            if req.expected_files is None or sorted(req.expected_files) != sorted(names):
                raise HTTPException(409, 'The metadata files changed. Review the reset list again.')
            for file in candidates:
                if file.suffix.lower() != '.json':
                    backup_existing_metadata(file, target)
            for file in candidates:
                file.unlink()
            for actors in (target / 'actors', target / '.actors'):
                if actors.is_dir() and not actors.is_symlink() and not any(actors.iterdir()):
                    actors.rmdir()
            with database() as db:
                for base in {str(source),str(target)}:
                    for (path,) in db.execute('SELECT path FROM media_cache').fetchall():
                        if Path(base) in Path(path).parents:
                            db.execute('DELETE FROM media_cache WHERE path=?',(path,))
            refresh_index_entry(req.folder)
    return {'deleted' if req.confirm else 'files':names, 'videos_preserved':True, 'subtitles_preserved':True}


@lru_cache(maxsize=8)
def bbfc_badge_url(rating: str) -> str:
    page = httpx.get(f"https://www.bbfc.co.uk/rating/{rating.lower()}", timeout=15)
    page.raise_for_status()
    match = re.search(r"/_next/static/images/BBFC_" + re.escape(rating) + r"[-_][^\"\s<>]+?\.png", page.text, re.IGNORECASE)
    if not match:
        raise ValueError("Symbol unavailable")
    return "https://www.bbfc.co.uk" + match.group(0)


@app.get("/api/bbfc/{rating}")
async def bbfc_badge(rating: str):
    rating = rating.upper()
    if rating not in {"U", "PG", "12", "12A", "15", "18", "R18"}:
        raise HTTPException(404, "Unknown BBFC rating")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(bbfc_badge_url(rating))
            response.raise_for_status()
        return Response(content=response.content, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})
    except (httpx.HTTPError, ValueError):
        raise HTTPException(404, "BBFC symbol unavailable")


@app.get("/api/library")
def stored_library():
    root = Path(load_config()["root_path"]).resolve()
    with database() as db:
        rows = db.execute("SELECT path, data FROM library ORDER BY path COLLATE NOCASE").fetchall()
    movies = []
    for path, data in rows:
        if Path(path).parent != root:
            continue
        try:
            movie = json.loads(data)
            if isinstance(movie, dict) and movie.get("folder"):
                if movie.get("metadata_version") != 4:
                    folder = Path(path)
                    if folder.is_dir() and not folder.is_symlink():
                        roles = classify_media(folder, VIDEO_EXTENSIONS)
                        primary = {p.name for p in roles["main"]}
                        movie["media"] = [v for v in movie.get("media", []) if v.get("name") in primary]
                        movie.update(read_movie_metadata(folder, VIDEO_EXTENSIONS, movie["media"]))
                        movie.update(videos=sorted(primary), extras=roles["extras"], ambiguous_videos=roles["ambiguous"])
                        with database() as db:
                            db.execute("UPDATE library SET data=? WHERE path=?", (json.dumps(movie), path))
                movies.append(movie)
        except (ValueError, TypeError):
            continue
    return {"movies": movies, "count": len(movies), "root": str(root)}


class LockRequest(BaseModel):
    locked: bool


@app.get('/api/edit-lock')
def get_edit_lock(request: Request):
    return {'locked': not edit_sessions.valid(request.headers.get('X-Artflix-Edit-Token', '')), 'read_only': library_read_only()}


@app.post('/api/edit-lock')
def set_edit_lock(req: LockRequest, request: Request):
    current = request.headers.get('X-Artflix-Edit-Token', '')
    edit_sessions.lock(current)
    return {'locked': req.locked, 'token': None if req.locked else edit_sessions.unlock(), 'read_only': library_read_only()}


def cache_provider(key, data):
    with database() as db:
        db.execute('INSERT OR REPLACE INTO provider_cache VALUES (?,?,?)', (key, json.dumps(data), time.time()))


def cached_movie(tmdb_id):
    language = load_config().get('language', 'en-US')
    with database() as db:
        row = db.execute('SELECT data FROM provider_cache WHERE key=?', (f'movie:{tmdb_id}:{language}',)).fetchone()
    return json.loads(row[0]) if row else None


def local_image_url(folder, name):
    return '/api/local-image?' + urlencode({'folder': folder, 'name': name})


@app.get('/api/local-image')
def local_image(folder: str, name: str):
    root, _ = paths()
    source = safe_folder(folder, root)
    try:
        relative = safe_metadata_relative(name)
    except ValueError:
        raise HTTPException(400, 'Invalid image path')
    file = source / relative
    if file.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'} or file.is_symlink() or source not in file.resolve().parents or not file.is_file():
        raise HTTPException(404, 'Image unavailable')
    return FileResponse(file)


def local_movie_bundle(source, relative_folder, existing=None):
    existing = existing or read_movie_metadata(source, VIDEO_EXTENSIONS)
    m = existing['metadata']
    raw_id = m.get('tmdbid', '')
    tmdb_id = int(raw_id) if str(raw_id).isdigit() else 0
    doc, _, _, _ = metadata_document(source, VIDEO_EXTENSIONS)
    profiles = []
    for directory in (source / 'actors', source / '.actors'):
        if directory.is_dir() and not directory.is_symlink():
            profiles.extend(p for p in directory.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'})
    def normalized(name):
        return re.sub(r'[^\w]', '', name.casefold())
    cast = []
    for index, actor in enumerate(doc.findall('actor') if doc is not None else []):
        name = actor.findtext('name') or ''
        portrait = next((p for p in profiles if normalized(re.sub(r'--\d+$', '', p.stem)) == normalized(name)), None)
        match = re.search(r'--(\d+)\.', portrait.name) if portrait else None
        actor_id = int(match.group(1)) if match else -(index + 1)
        cast.append({'id': actor_id, 'name': name, 'character': actor.findtext('role') or '', 'order': index,
                     'profile_path': None, 'local_photo': local_image_url(relative_folder, portrait.relative_to(source).as_posix()) if portrait else None})
    crew = [{'name': name.strip(), 'job': job} for key, job in [('directors', 'Director'), ('writers', 'Writer')] for name in m.get(key, '').split(',') if name.strip()]
    details = {'id': tmdb_id, 'title': m.get('title') or source.name, 'original_title': m.get('originaltitle', ''), 'release_date': m.get('premiered') or m.get('year', ''),
               'overview': m.get('plot', ''), 'tagline': m.get('tagline', ''), 'runtime': m.get('runtime', ''),
               'genres': [{'name': x.strip()} for x in m.get('genres', '').split(',') if x.strip()],
               'production_companies': [{'name': x.strip()} for x in m.get('studios', '').split(',') if x.strip()],
               'credits': {'cast': cast, 'crew': crew}, 'belongs_to_collection': {'name': m.get('set', '')} if m.get('set') else None,
               'production_countries': [{'name':(e.text or '')} for e in (doc.findall('country') if doc is not None else [])], 'spoken_languages': [{'english_name':(e.text or '')} for e in (doc.findall('spokenland') if doc is not None else [])], 'poster_path': None}
    state = read_asset_state(source)
    artwork = []
    categories = [('poster', 'movieposter', 'poster.jpg'), ('landscape', 'moviethumb', 'landscape.jpg'), ('fanart', 'moviebackground', 'fanart.jpg'), ('backdrop', 'moviebackground', 'fanart.jpg'), ('disc', 'moviedisc', 'disc.png'), ('clearart', 'hdmovieclearart', 'clearart.png'), ('clearlogo','hdmovielogo','clearlogo.png'), ('banner','Banners','banner.jpg')]
    for path in sorted(source.iterdir()):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
            continue
        category = next(((kind, filename) for stem, kind, filename in categories if path.stem.lower() == stem or path.stem.lower().endswith('-' + stem)), None)
        if category:
            kind, filename = category
            url = local_image_url(relative_folder, path.name)
            artwork.append({'type': kind, 'name': filename, 'url': url, 'local': True, 'downloaded': True, 'lang': '', 'existing_name': path.name})
            if kind == 'movieposter' and not details.get('poster_url'):
                details['poster_url'] = url
    return {'movie': {'details': details, 'certification': m.get('mpaa', ''), 'certifications': [], 'region': load_config().get('region', '').upper()},
            'artwork': {'artwork': artwork, 'notice': 'Saved artwork from this folder. Use Refresh artwork from all providers to browse alternatives.'}}


def refresh_index_entry(folder, old_folder=None):
    root, _ = paths()
    source = safe_folder(folder, root)
    files = [p for p in source.iterdir() if p.is_file() and not p.is_symlink()]
    roles = classify_media(source, VIDEO_EXTENSIONS)
    media = [video_info(p) for p in roles["main"]]
    record = {**read_movie_metadata(source, VIDEO_EXTENSIONS, media), 'extras':roles['extras'], 'ambiguous_videos':roles['ambiguous'], 'folder': folder, 'name': source.name, 'media': media,
              'videos': [p.name for p in roles['main']],
              'assets': sorted(p.name for p in files if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.nfo'})}
    with database() as db:
        if old_folder and old_folder != folder:
            db.execute('DELETE FROM library WHERE path=?', (str(root / old_folder),))
        db.execute('INSERT OR REPLACE INTO library VALUES (?,?)', (str(source), json.dumps(record)))
    return record


@app.get('/api/library/health')
def health_summary():
    return library_health(stored_library()['movies'])

# Saved searches are application preferences, not mutations of the movie share.
BUILTIN_SEARCHES = [
    {'id':'builtin-certificates','name':'Missing certificates','data':{'filters':{},'missing_fields':['certificate'],'missing_mode':'any','set_exact':False,'health':None}},
    {'id':'builtin-artwork','name':'Missing posters or backgrounds','data':{'filters':{},'missing_fields':['poster','fanart'],'missing_mode':'any','set_exact':False,'health':None}},
    {'id':'builtin-4k-subtitles','name':'4K films without subtitles','data':{'filters':{'resolution':'4k'},'missing_fields':['subtitles'],'missing_mode':'any','set_exact':False,'health':None}},
    {'id':'builtin-unmatched','name':'Unmatched movies','data':{'filters':{},'missing_fields':[],'missing_mode':'any','set_exact':False,'health':{'kind':'unmatched','id':None}}},
    {'id':'builtin-duplicates','name':'Possible duplicates','data':{'filters':{},'missing_fields':[],'missing_mode':'any','set_exact':False,'health':{'kind':'duplicates','id':None}}},
]


class SearchState(BaseModel):
    filters: dict[str,str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    missing_mode: str = 'any'
    set_exact: bool = False
    health: dict[str,str | None] | None = None


class SavedSearchRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1,max_length=80)
    data: SearchState


def validate_search_state(state):
    allowed={'title','year','certificate','actors','genres','set','resolution','audio','codec','subtitles'}
    missing={'nfo','title','original_title','tmdb_id','year','release_date','runtime','genres','tagline','directors','writers','studios','plot','certificate','actors','poster','fanart','actor_portraits','subtitles'}
    if not set(state.filters).issubset(allowed) or any(len(value)>500 for value in state.filters.values()) or not set(state.missing_fields).issubset(missing) or state.missing_mode not in {'any','all'}:
        raise HTTPException(400,'Invalid saved-search fields.')
    if state.health:
        if set(state.health)-{'kind','id'} or state.health.get('kind') not in {'unmatched','poster','fanart','certificate','incomplete','unreadable','ambiguous','duplicates'}:
            raise HTTPException(400,'Invalid health filter.')
        identifier=state.health.get('id')
        if identifier is not None and not re.fullmatch(r'[1-9][0-9]*',identifier):
            raise HTTPException(400,'Invalid duplicate-movie ID.')


@app.get('/api/saved-searches')
def saved_searches():
    with database() as db:
        rows=db.execute('SELECT id,name,data,updated,archived FROM saved_searches ORDER BY name COLLATE NOCASE').fetchall()
    items=[{'id':key,'name':name,'data':json.loads(data),'updated':updated,'archived':bool(archived)} for key,name,data,updated,archived in rows]
    return {'builtins':BUILTIN_SEARCHES,'searches':[i for i in items if not i['archived']],'archived':[i for i in items if i['archived']]}


@app.post('/api/saved-searches')
def save_search(req: SavedSearchRequest, request: Request):
    require_unlocked(request)
    validate_search_state(req.data)
    name=req.name.strip()
    if not name:raise HTTPException(400,'Enter a name for the search.')
    key=req.id or uuid.uuid4().hex
    if not re.fullmatch(r'[0-9a-f]{32}',key):raise HTTPException(400,'Invalid saved-search ID.')
    with database() as db:
        if req.id and not db.execute('SELECT id FROM saved_searches WHERE id=?',(key,)).fetchone():raise HTTPException(404,'Saved search not found.')
        db.execute('INSERT OR REPLACE INTO saved_searches VALUES (?,?,?,?,0)',(key,name,json.dumps(req.data.model_dump()),time.time()))
    return {'ok':True,'id':key}


@app.delete('/api/saved-searches/{search_id}')
def archive_search(search_id: str, request: Request):
    require_unlocked(request)
    with database() as db:
        if not db.execute('SELECT id FROM saved_searches WHERE id=?',(search_id,)).fetchone():raise HTTPException(404,'Saved search not found.')
        db.execute('UPDATE saved_searches SET archived=1 WHERE id=?',(search_id,))
    return {'ok':True,'archived':True}


@app.get('/api/collections/completeness')
async def collection_completeness(movie_id: int, refresh: bool = False):
    if movie_id<1:raise HTTPException(400,'Match this movie with TMDB first.')
    language=load_config().get('language','en-US')
    membership_key=f'collection-membership:{movie_id}:{language}'
    with database() as db:row=db.execute('SELECT data FROM provider_cache WHERE key=?',(membership_key,)).fetchone()
    cached=cached_movie(movie_id)
    if row and not refresh:
        membership=json.loads(row[0])
    elif cached is not None and not refresh:
        membership=cached.get('belongs_to_collection')
    else:
        movie=await tmdb_get(f'/movie/{movie_id}')
        membership=movie.get('belongs_to_collection')
        cache_provider(membership_key,membership)
    if not membership or not membership.get('id'):
        return {'collection':None,'message':'TMDB does not list this movie as part of a collection.'}
    key=f"collection:{membership['id']}:{language}"
    with database() as db:row=db.execute('SELECT data,updated FROM provider_cache WHERE key=?',(key,)).fetchone()
    if row and not refresh:
        collection,updated=json.loads(row[0]),row[1]
    else:
        collection=await tmdb_get(f"/collection/{int(membership['id'])}")
        cache_provider(key,collection)
        updated=time.time()
    owned={}
    for item in stored_library()['movies']:
        identifier=str(item.get('metadata',{}).get('tmdbid',''))
        if identifier.isdigit():owned.setdefault(int(identifier),[]).append({'folder':item['folder'],'name':item['name']})
    today=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    parts=[]
    for movie in sorted(collection.get('parts',[]),key=lambda m:(m.get('release_date') or '9999',m.get('title') or '')):
        identifier=movie.get('id')
        if not isinstance(identifier,int):continue
        release=movie.get('release_date') or ''
        status='owned' if identifier in owned else 'unknown_date' if not release else 'upcoming' if release>today else 'missing'
        parts.append({'id':identifier,'title':movie.get('title') or movie.get('original_title') or str(identifier),'release_date':release,'status':status,'owned_folders':owned.get(identifier,[])})
    return {'collection':{'id':collection.get('id',membership['id']),'name':collection.get('name') or membership.get('name','Collection')},'parts':parts,
            'owned':sum(p['status']=='owned' for p in parts),'missing':sum(p['status']=='missing' for p in parts),'upcoming':sum(p['status']=='upcoming' for p in parts),
            'unknown_date':sum(p['status']=='unknown_date' for p in parts),'updated':updated}


def diagnostic_secrets():
    cfg=load_config()
    return [cfg.get(key,'') for key in ('tmdb_token','fanart_key','fanart_client_key','tvdb_key','tvdb_pin')]


def journal(event,**fields):
    try:append_event(DATA_DIR,event,diagnostic_secrets(),**fields)
    except OSError:pass  # Diagnostics must never prevent safe reads or library operations.


@app.middleware('http')
async def record_request_diagnostics(request: Request, call_next):
    started=time.monotonic()
    try:
        response=await call_next(request)
    except Exception as error:
        route=request.scope.get('route')
        journal('request.error',route=getattr(route,'path','/unmatched'),error_type=type(error).__name__)
        raise
    route=request.scope.get('route')
    path=getattr(route,'path','/unmatched')
    if response.status_code>=400:
        journal('request.failed',route=path,status=response.status_code)
    elif path in {'/api/write','/api/reset','/api/backups/restore','/api/scan/start','/api/scan/cancel','/api/settings','/api/saved-searches'} and request.method in {'POST','PUT','DELETE'}:
        journal('operation.completed',route=path,status=response.status_code,elapsed_ms=round((time.monotonic()-started)*1000))
    return response


def deployment_checks():
    cfg=load_config();checks=[];root=Path(cfg['root_path']);write=Path(cfg['write_path'])
    def add(key,title,status,detail,**data):checks.append({'key':key,'title':title,'status':status,'detail':detail,**data})
    read_ok=root.is_dir() and os.access(root,os.R_OK|os.X_OK)
    add('read_path','Scan path','pass' if read_ok else 'fail',str(root) if read_ok else 'Configured scan path is missing or unreadable.')
    write_exists=write.is_dir();read_only_mount=True
    if write_exists:
        try:read_only_mount=bool(os.statvfs(write).f_flag & os.ST_RDONLY)
        except OSError:pass
    writable=write_exists and os.access(write,os.W_OK|os.X_OK) and not read_only_mount
    add('write_path','Metadata write path','pass' if writable else 'warn' if write_exists else 'fail', 'Filesystem permits writes (no test file was written).' if writable else 'Read-only or not writable. Scanning is still possible.' if write_exists else 'Configured write path is missing.')
    same=False
    try:same=read_ok and write_exists and os.path.samefile(root,write)
    except OSError:pass
    add('path_mapping','Read/write path mapping','pass' if same else 'warn','Both paths refer to the same library.' if same else 'Renaming requires both mappings to refer to the same movie folder.')
    for key,title,path in [('config_space','Appdata free space',DATA_DIR),('library_space','Movie-share free space',root)]:
        try:
            disk=shutil.disk_usage(path)
            add(key,title,'pass' if disk.free>=1024**3 else 'warn' if disk.free else 'fail',f'{disk.free/1024**3:.2f} GiB available.',free_bytes=disk.free)
        except OSError:add(key,title,'fail','Free space could not be checked.')
    config_ok=False
    try:
        DATA_DIR.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=DATA_DIR) as stream:stream.write(b'ArtFlix appdata check');stream.flush()
        config_ok=True;add('config_access','Appdata write access','pass','Settings and cache directory is writable.')
    except OSError:add('config_access','Appdata write access','fail','Settings/cache directory is not writable.')
    db_ok=False
    try:
        with database() as db:result=db.execute('PRAGMA quick_check').fetchone()[0]
        db_ok=result=='ok';add('database','SQLite database','pass' if db_ok else 'fail','Integrity check passed.' if db_ok else 'Integrity check reported an error.')
    except (OSError,sqlite3.Error):add('database','SQLite database','fail','Database could not be opened or checked.')
    probe=bool(shutil.which('ffprobe'));add('ffprobe','Video metadata tool','pass' if probe else 'fail','ffprobe is installed.' if probe else 'ffprobe is missing; technical scans will be incomplete.')
    add('safety','Library mutation mode','pass','Server-enforced read-only mode is active.' if library_read_only() else 'Writes require unlocking the padlock and approving changes.')
    if library_read_only() and writable:add('mount_safety','First-scan mount protection','warn','For filesystem-level first-scan protection, mount both movie mappings read-only as well.')
    add('tmdb_config','TMDB credential','pass' if cfg.get('tmdb_token') else 'warn','Configured; use Test API connections to verify.' if cfg.get('tmdb_token') else 'Not configured. Local scans still work.')
    add('fanart_config','fanart.tv credential','pass' if cfg.get('fanart_key') else 'warn','Configured; use Test API connections to verify.' if cfg.get('fanart_key') else 'Not configured. Saved artwork remains viewable.')
    if cfg.get('tv_root_path') or cfg.get('tv_write_path'):
        try:
            tv_root,tv_write=tv_services['roots']()
            add('tv_read_path','TV scan path','pass' if os.access(tv_root,os.R_OK|os.X_OK) else 'fail','TV scan mapping is available.')
            tv_same=tv_root.samefile(tv_write)
            add('tv_mapping','TV read/write mapping','pass' if tv_same else 'fail','Both mappings refer to the same TV share.' if tv_same else 'TV mappings must refer to the same share.')
            tv_writable=os.access(tv_write,os.W_OK|os.X_OK) and not bool(os.statvfs(tv_write).f_flag & os.ST_RDONLY)
            add('tv_write_path','TV metadata write path','pass' if tv_writable else 'warn','Filesystem permits writes; no test file was written.' if tv_writable else 'Read-only or not writable; scanning remains available.')
            space=shutil.disk_usage(tv_root).free
            add('tv_space','TV-share free space','pass' if space>=1024**3 else 'warn',f'{space/1024**3:.2f} GiB available.')
        except (HTTPException,OSError):add('tv_paths','TV library paths','fail','TV mappings are missing, overlap movie paths, or are unavailable.')
        add('tvdb_config','TVDB credential','pass' if cfg.get('tvdb_key') else 'warn','Configured; use Test API connections to verify.' if cfg.get('tvdb_key') else 'Not configured. Local TV scans still work.')
    return {'checks':checks,'scan_ready':read_ok and config_ok and db_ok and probe,'write_ready':writable and config_ok and db_ok and not library_read_only()}


@app.get('/api/deployment/checks')
def check_deployment():
    return deployment_checks()


@app.post('/api/deployment/providers')
async def test_provider_connections():
    cfg=load_config();results=[]
    for provider in ('TMDB','fanart.tv'):
        configured=bool(cfg.get('tmdb_token' if provider=='TMDB' else 'fanart_key'))
        if not configured:
            results.append({'provider':provider,'status':'fail','detail':'Credential not configured.'});continue
        started=time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
                if provider=='TMDB':response=await client.get(TMDB_BASE+'/configuration',headers={'Authorization':'Bearer '+cfg['tmdb_token']})
                else:
                    params={'api_key':cfg['fanart_key']}
                    if cfg.get('fanart_client_key'):params['client_key']=cfg['fanart_client_key']
                    response=await client.get(FANART_BASE+'/278',params=params)
            status='pass' if response.status_code==200 else 'warn' if provider=='fanart.tv' and response.status_code==404 else 'fail'
            results.append({'provider':provider,'status':status,'detail':f'Provider returned HTTP {response.status_code}.','elapsed_ms':round((time.monotonic()-started)*1000)})
            journal('provider.test',provider=provider,status=response.status_code)
        except httpx.HTTPError as error:
            results.append({'provider':provider,'status':'fail','detail':'Connection failed ('+type(error).__name__+').'})
            journal('provider.test_failed',provider=provider,error_type=type(error).__name__)
    if cfg.get('tvdb_key'):
        try:
            await tv_services['provider'].get('/languages')
            results.append({'provider':'TVDB','status':'pass','detail':'TVDB login and API access succeeded.'})
        except HTTPException as error:
            results.append({'provider':'TVDB','status':'fail','detail':error.detail})
    return {'results':results}


def system_details():
    try:
        with database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tv_library(path TEXT PRIMARY KEY,data TEXT)')
            counts={name:db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for name,table in [('indexed_movies','library'),('indexed_shows','tv_library'),('backups','backup_index'),('probe_cache','media_cache')]}
    except (OSError,sqlite3.Error):counts={}
    return {'application':'ArtFlix Movie Manager','version':VERSION,'build':BUILD,'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'platform':platform.system(),'read_only':library_read_only(),'counts':counts}


@app.get('/api/system')
def system_info():return system_details()


@app.get('/api/diagnostics/report')
def diagnostic_report():
    checks=deployment_checks()
    job=scan_jobs.status()
    report={'system':system_details(),'checks':[{k:c[k] for k in ('key','title','status')} for c in checks['checks']],
            'scan':{k:job.get(k) for k in ('state','checked','total','found','new','errors','elapsed')},'events':recent_events(DATA_DIR,diagnostic_secrets())}
    content=redact(json.dumps(report,indent=2),diagnostic_secrets())
    return Response(content,media_type='application/json',headers={'Content-Disposition':f'attachment; filename="ArtFlix-diagnostics-{datetime.date.today().isoformat()}.json"'})


# TV uses its own roots, index and background job; movie endpoints are unchanged.
import sys
from app.tv_routes import install_tv
tv_services = install_tv(app, sys.modules[__name__])

from app.maintenance import install_maintenance
install_maintenance(app, sys.modules[__name__])
