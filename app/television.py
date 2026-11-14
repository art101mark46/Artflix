"""TV library scanning, episode inventory and TVDB v4 adapter.

Scanning never contacts providers or modifies library files. Aired order is explicit;
provider inventories are saved only after every page has been fetched successfully.
"""
from app.encoding import valid_filename,display_filename
from app.tv_scan import directories
import datetime as dt
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

TVDB_BASE = 'https://api4.thetvdb.com/v4'
IMAGE_HOSTS = {'artworks.thetvdb.com', 'assets.fanart.tv', 'image.tmdb.org'}
EXTRAS = {'extras','trailers','samples','featurettes','behind the scenes','deleted scenes','interviews'}
LANGUAGES = {'en':'eng','fr':'fra','de':'deu','es':'spa','it':'ita','ja':'jpn','zh':'zho','ko':'kor','pt':'por','ru':'rus','nl':'nld','ar':'ara','hi':'hin','pl':'pol','sv':'swe','da':'dan','no':'nor','fi':'fin','cs':'ces','tr':'tur','el':'ell','he':'heb','uk':'ukr'}


def tv_language(config):
    value=config.get('tv_language') or config.get('language','en-US').split('-')[0]
    return LANGUAGES.get(value,value) if re.fullmatch(r'[a-z]{2,3}',value) else 'eng'


def safe_image(url):
    try:
        parsed=urlparse(url)
        return parsed.scheme=='https' and parsed.hostname in IMAGE_HOSTS and not parsed.username and not parsed.password and parsed.port in {None,443}
    except ValueError:return False


def read_nfo(path, root_tag):
    if not path.is_file() or path.is_symlink():return None
    if path.stat().st_size>4*1024*1024:return None
    try:
        raw=path.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():return None
        root=ET.fromstring(raw)
        return root if root.tag==root_tag else None
    except (OSError,ET.ParseError):return None


def values(root):
    if root is None:return {}
    data={key:(root.findtext(tag) or '').strip() for key,tag in {'title':'title','original_title':'originaltitle','year':'year','premiered':'premiered','runtime':'runtime','plot':'plot','certificate':'mpaa','status':'status','tvdb_id':'tvdbid','tagline':'tagline'}.items()}
    if not data['tvdb_id']:
        data['tvdb_id']=next(((e.text or '').strip() for e in root.findall('uniqueid') if e.get('type')=='tvdb'),'')
    for key,tag in {'genres':'genre','studios':'studio','directors':'director','writers':'credits'}.items():
        data[key]=', '.join(e.text.strip() for e in root.findall(tag) if e.text)
    data['actors']=[{'id':e.findtext('tvdbid') or '', 'name':e.findtext('name') or '', 'role':e.findtext('role') or '', 'image':e.findtext('thumb') or ''} for e in root.findall('actor')]
    return data


def episode_numbers(name):
    match=re.search(r'(?i)(?<![a-z0-9])s(\d{1,3})[ ._-]*e(\d{1,4})((?:[ ._]*e\d{1,4})*)(?:-(?:e)?(\d{1,4}))?',name)
    if match:
        season,first=int(match[1]),int(match[2]);numbers=[first]+[int(x) for x in re.findall(r'(?i)e(\d+)',match[3])]
        if match[4]:
            last=int(match[4])
            if last<first or last-first>100:return []
            numbers=list(range(first,last+1))
        return [(season,n) for n in numbers]
    match=re.search(r'(?i)(?<!\d)(\d{1,3})x(\d{1,4})(?!\d)',name)
    return [(int(match[1]),int(match[2]))] if match else []


def inventory(folder, extensions, probe=None, cancelled=None, progress=None):
    episodes=[];unrecognized=[];extras=[];warnings=[];issues=[]
    cancelled=cancelled or (lambda:False)
    for directory,files in directories(folder,cancelled,warnings.append,lambda path,message:issues.append({'path':str(path),'error':message})):
        if cancelled():break
        subtitles=[name for name in files if valid_filename(name) and (directory/name).suffix.lower() in {'.srt','.ass','.ssa','.sub','.idx','.vtt','.sup'} and not (directory/name).is_symlink() and (directory/name).is_file()]
        for name in sorted(files):
            if cancelled():break
            path=Path(directory)/name
            if path.is_symlink() or path.suffix.lower() not in extensions:continue
            relative=path.relative_to(folder).as_posix()
            if re.search(r'(?i)[._-](sample|trailer)$',path.stem):extras.append(relative);continue
            if not valid_filename(path):
                message='Skipped video with unsupported filename bytes: '+display_filename(relative)+'. File was left untouched.'
                warnings.append(message);issues.append({'path':str(path),'error':message})
                if progress:progress(file=display_filename(relative),phase='finished')
                continue
            if progress:progress(file=relative,phase='reading')
            numbers=episode_numbers(path.stem)
            doc=read_nfo(path.with_suffix('.nfo'),'episodedetails')
            if not numbers and doc is not None:
                try:numbers=[(int(doc.findtext('season')),int(doc.findtext('episode')))]
                except (ValueError,TypeError):pass
            if not numbers:
                unrecognized.append(relative)
                if progress:progress(file=relative,phase='finished')
                continue
            sidecars=[name for name in subtitles if Path(name).stem==path.stem or name.startswith(path.stem+'.')]
            episodes.append({'file':relative,'sidecars':sidecars,'numbers':[list(n) for n in numbers],'metadata':values(doc),'has_nfo':doc is not None,'media':probe(path) if probe else None})
            if progress:progress(file=relative,phase='finished')
    return {'episodes':episodes,'unrecognized':unrecognized,'extras':extras,'warnings':warnings,'issues':issues}


def completeness(local, remote, include_specials=False, today=None, ignored=None):
    ignored=ignored or set()
    today=today or dt.date.today().isoformat()
    owned={tuple(n) for file in local['episodes'] for n in file['numbers']}
    unique={}
    for episode in remote:
        s,e=episode.get('seasonNumber'),episode.get('number')
        if not isinstance(s,int) or not isinstance(e,int) or e<1 or s<0:continue
        unique[(s,e)]=episode
    counts={'owned':0,'missing':0,'upcoming':0,'unknown':0,'specials':0,'ignored':0};rows=[]
    for (season,number),episode in sorted(unique.items()):
        aired=str(episode.get('aired') or '')[:10]
        try:dt.date.fromisoformat(aired)
        except ValueError:aired=''
        present=(season,number) in owned
        status='ignored' if (season,number) in ignored else 'owned' if present else 'unknown' if not aired else 'upcoming' if aired>today else 'missing'
        if season==0 and not include_specials:counts['specials']+=1
        else:counts[status]+=1
        rows.append({'season':season,'number':number,'id':episode.get('id'),'title':episode.get('name') or '', 'aired':aired,'status':status,'special':season==0})
    expected=sum(1 for row in rows if (include_specials or not row['special']) and row['aired'] and row['aired']<=today and row['status']!='ignored')
    owned_aired=sum(1 for row in rows if (include_specials or not row['special']) and row['aired'] and row['aired']<=today and row['status']=='owned')
    counts.update(expected=expected,owned_aired=owned_aired,percent=round(100*owned_aired/expected) if expected else None)
    return {'counts':counts,'episodes':rows,'local_only':[list(n) for n in sorted(owned-set(unique))]}


class TVDB:
    def __init__(self,config):self.config=config;self.token=None;self.expires=0;self.identity=None
    async def get(self,path,params=None):
        cfg=self.config();identity=(cfg.get('tvdb_key',''),cfg.get('tvdb_pin',''))
        if not identity[0]:raise HTTPException(400,'Add your TVDB v4 API key in Settings. Your key may also require a subscriber PIN.')
        try:
            async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
                for attempt in range(2):
                    if identity!=self.identity or self.expires<time.time():
                        body={'apikey':identity[0]}
                        if identity[1]:body['pin']=identity[1]
                        login=await client.post(TVDB_BASE+'/login',json=body)
                        if login.is_error:raise HTTPException(400,f'TVDB login failed ({login.status_code}). Check your key and subscriber PIN.')
                        self.token=login.json().get('data',{}).get('token')
                        if not self.token:raise HTTPException(502,'TVDB did not return an access token.')
                        self.identity=identity;self.expires=time.time()+20*86400
                    response=await client.get(TVDB_BASE+path,params=params,headers={'Authorization':'Bearer '+self.token})
                    if response.status_code==401 and attempt==0:self.expires=0;continue
                    if response.is_error:raise HTTPException(502,f'TVDB request failed ({response.status_code}).')
                    return response.json()
        except (httpx.HTTPError,ValueError):raise HTTPException(502,'TVDB could not be reached or returned an invalid response. Try again later.')
    async def bundle(self,series_id,order,lang):
        details=(await self.get(f'/series/{series_id}/extended',{'meta':'translations'})).get('data') or {}
        if details.get('id')!=series_id:raise HTTPException(502,'TVDB returned a different or missing series ID. No inventory was replaced.')
        translations=details.get('translations') or {}
        names=translations.get('nameTranslations') or [];plots=translations.get('overviewTranslations') or []
        details['name']=next((v.get('name') for v in names if v.get('language')==lang and v.get('name')),details.get('name',''))
        details['overview']=next((v.get('overview') for v in plots if v.get('language')==lang and v.get('overview')),details.get('overview',''))
        episodes=[];seen=set()
        for page in range(1000):
            result=await self.get(f'/series/{series_id}/episodes/{order}/{lang}',{'page':page})
            items=(result.get('data') or {}).get('episodes') or []
            ids=tuple(item.get('id') for item in items)
            if items and ids in seen:raise HTTPException(502,'TVDB repeated an episode page; inventory was not replaced.')
            seen.add(ids);episodes.extend(items)
            if not (result.get('links') or {}).get('next'):break
        else:raise HTTPException(502,'TVDB episode pagination exceeded the safety limit.')
        return {'details':details,'episodes':episodes,'order':order,'language':lang,'updated':time.time()}


def serialize_nfo(tag, metadata, existing=None, actors=None, episode=None):
    root=ET.fromstring(ET.tostring(existing)) if existing is not None else ET.Element(tag)
    mapping={'title':'title','original_title':'originaltitle','year':'year','premiered':'premiered','runtime':'runtime','plot':'plot','certificate':'mpaa','status':'status','tvdb_id':'tvdbid','tagline':'tagline','genres':'genre','studios':'studio','directors':'director','writers':'credits','thumb':'thumb'}
    for key,xml in mapping.items():
        if key not in metadata:continue
        for old in root.findall(xml):root.remove(old)
        items=str(metadata[key]).split(',') if key in {'genres','studios','directors','writers'} else [str(metadata[key])]
        for value in items:
            if value.strip():ET.SubElement(root,xml).text=value.strip()
    if metadata.get('tvdb_id'):
        for old in root.findall('uniqueid'):
            if old.get('type')=='tvdb':root.remove(old)
        ET.SubElement(root,'uniqueid',{'type':'tvdb','default':'true'}).text=str(metadata['tvdb_id'])
    if actors is not None:
        for old in root.findall('actor'):root.remove(old)
        for actor in actors:
            node=ET.SubElement(root,'actor')
            for key,value in {'name':actor.get('name'),'role':actor.get('role'),'tvdbid':actor.get('id'),'thumb':actor.get('image')}.items():
                if value:ET.SubElement(node,key).text=str(value)
    if episode:
        for key,value in episode.items():
            for old in root.findall(key):root.remove(old)
            ET.SubElement(root,key).text=str(value)
    ET.indent(root)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def normalize_credits(record, people_types=None):
    """Separate TVDB crew from cast; use provider role names, not guessed type IDs."""
    people_types=people_types or {}
    found={'directors':[], 'writers':[]};seen={key:set() for key in found}
    for character in record.get('characters') or []:
        label=str(character.get('peopleType') or people_types.get(str(character.get('type')), '')).strip().casefold()
        role={'director':'director','writer':'writer','screenwriter':'writer','screenplay':'writer','story':'writer','actor':'actor','guest star':'actor','guest actor':'actor','voice actor':'actor'}.get(label,'other' if label or character.get('type') is not None else '')
        character['credit_role']=role
        key={'director':'directors','writer':'writers'}.get(role)
        name=str(character.get('personName') or '').strip()
        if key and name and name.casefold() not in seen[key]:found[key].append(name);seen[key].add(name.casefold())
    record['credits']={key:', '.join(names) for key,names in found.items()}
    return record['credits']


def tv_cast(record):
    return [c for c in record.get('characters') or [] if c.get('credit_role') in {None,'','actor'} and str(c.get('peopleType') or '').strip().casefold() in {'','actor','guest star','guest actor','voice actor'}]


def tvdb_image_url(value):
    """Resolve TVDB's relative artwork paths without relaxing the host allowlist."""
    if not isinstance(value,str):return ''
    value=value.strip()
    if value.startswith('/banners/'):
        value='https://artworks.thetvdb.com'+value
    elif value.startswith('banners/'):
        value='https://artworks.thetvdb.com/'+value
    return value if safe_image(value) else ''


def normalize_episode_images(data):
    for episode in data.get('episodes') or []:
        episode['image']=tvdb_image_url(episode.get('image'))
    return data


def season_artwork_layout(folder,extensions):
    local=inventory(folder,extensions)
    seasons={number[0] for episode in local['episodes'] for number in episode['numbers']}
    directories={}
    for directory in folder.iterdir():
        match=re.fullmatch(r'(?i)season\s*(\d{1,3})',directory.name)
        if match and directory.is_dir() and not directory.is_symlink():
            number=int(match[1]);seasons.add(number);directories.setdefault(number,[]).append(directory.name)
    result={}
    for season in sorted(seasons):
        prefix='season-specials' if season==0 else f'season{season:02d}'
        result[season]={}
        for kind in ('poster','banner','landscape'):
            outputs=[prefix+'-'+kind+'.jpg']
            if len(directories.get(season,[]))==1:outputs.append(directories[season][0]+'/'+kind+'.jpg')
            result[season][kind]=outputs
    return result
