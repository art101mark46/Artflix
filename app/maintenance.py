"""Private appdata snapshots and read-only NFO sample checks."""
import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse
from app.control import COMMIT_LOCK


def config_archive(data_dir):
    data_dir.mkdir(parents=True,exist_ok=True)
    folder=data_dir/'config-backups';folder.mkdir(exist_ok=True,mode=0o700);os.chmod(folder,0o700)
    identifier=uuid.uuid4().hex
    output=folder/(identifier+'.zip');temporary=folder/(identifier+'.partial')
    try:
        with tempfile.TemporaryDirectory(dir=folder) as scratch:
            snapshot=Path(scratch)/'artflix.sqlite3'
            if (data_dir/'artflix.sqlite3').exists():
                source=sqlite3.connect(data_dir/'artflix.sqlite3');target=sqlite3.connect(snapshot)
                try:source.backup(target)
                finally:target.close();source.close()
            with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
                for directory,dirs,files in os.walk(data_dir,followlinks=False):
                    dirs[:]=[name for name in dirs if name!='config-backups' and not (Path(directory)/name).is_symlink()]
                    for name in files:
                        file=Path(directory)/name
                        if file.is_symlink() or file.name in {'artflix.sqlite3','artflix.sqlite3-wal','artflix.sqlite3-shm','artflix.sqlite3-journal'}:continue
                        if file.name.endswith(('.partial','.tmp')):continue
                        archive.write(file,file.relative_to(data_dir).as_posix())
                if snapshot.exists():archive.write(snapshot,'artflix.sqlite3')
                archive.writestr('ARTFLIX-BACKUP.txt','ArtFlix config backup. Contains private settings/API credentials. Keep private. Stop ArtFlix before restoring to /config. Movie/TV shares are not included.\n')
        os.chmod(temporary,0o600);os.replace(temporary,output)
        return {'id':identifier,'size_bytes':output.stat().st_size,'created':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    finally:
        if temporary.exists():temporary.unlink()


def validate_nfo(file):
    problems=[]
    try:
        if file.stat().st_size>4*1024*1024:raise ValueError('Oversized NFO')
        raw=file.read_bytes()
        if len(raw)>4*1024*1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsupported XML declaration or oversized NFO')
        root=ET.fromstring(raw)
        if root.tag not in {'movie','tvshow','episodedetails','season'}:problems.append('Unrecognized NFO root element')
        if not (root.findtext('title') or '').strip():problems.append('Title is missing')
        if root.tag=='episodedetails':
            for key in ('season','episode'):
                try:
                    if int(root.findtext(key))<(0 if key=='season' else 1):raise ValueError()
                except (ValueError,TypeError):problems.append(key.title()+' number is missing or invalid')
        for thumb in root.iter('thumb'):
            value=(thumb.text or '').strip()
            if value and '://' not in value:
                p=Path(value)
                if p.is_absolute() or '..' in p.parts:problems.append('Non-portable local artwork reference: '+value)
                elif not (file.parent/p).is_file():problems.append('Local artwork not found: '+value)
        return {'root':root.tag,'valid':not problems,'issues':problems}
    except (OSError,ET.ParseError,ValueError) as error:return {'valid':False,'issues':['NFO could not be parsed: '+type(error).__name__]}


def install_maintenance(app,m):
    @app.post('/api/config/backups')
    def create_backup(request:Request):
        m.require_unlocked(request)
        with COMMIT_LOCK:
            if m.scan_jobs.active() or m.tv_services['jobs'].active():raise HTTPException(409,'Finish or cancel scans before creating a config backup.')
            result=config_archive(m.DATA_DIR)
        return result
    @app.get('/api/config/backups')
    def list_backups(request:Request):
        m.require_unlocked(request);folder=m.DATA_DIR/'config-backups'
        return {'backups':[{'id':p.stem,'size_bytes':p.stat().st_size,'created':datetime.datetime.fromtimestamp(p.stat().st_mtime,datetime.timezone.utc).isoformat()} for p in sorted(folder.glob('*.zip'),key=lambda p:p.stat().st_mtime,reverse=True) if p.is_file() and not p.is_symlink()]}
    @app.get('/api/config/backups/{identifier}')
    def download_backup(identifier:str,request:Request):
        m.require_unlocked(request)
        if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise HTTPException(404,'Backup not found.')
        file=m.DATA_DIR/'config-backups'/(identifier+'.zip')
        if not file.is_file() or file.is_symlink():raise HTTPException(404,'Backup not found.')
        return FileResponse(file,filename='ArtFlix-config-'+identifier[:8]+'.zip',media_type='application/zip',headers={'Cache-Control':'no-store'})
    @app.get('/api/nfo/sample-check')
    def sample_check():
        results=[]
        cfg=m.load_config()
        for kind,key in [('Movies','root_path'),('TV Shows','tv_root_path')]:
            if not cfg.get(key):continue
            root=Path(cfg[key]).resolve();found=0
            if not root.is_dir():continue
            for directory,dirs,files in os.walk(root,followlinks=False):
                dirs[:]=sorted(name for name in dirs if not (Path(directory)/name).is_symlink() and name not in {'actors','.actors','extras','trailers'})
                for name in sorted(files):
                    file=Path(directory)/name
                    if file.suffix.lower()!='.nfo' or file.is_symlink():continue
                    results.append({'library':kind,'file':file.relative_to(root).as_posix(),**validate_nfo(file)})
                    found+=1
                    if found>=10:break
                if found>=10:break
        return {'results':results,'notice':'Read-only XML and local-image checks. Actual import into Jellyfin/Kodi must be checked separately.'}
