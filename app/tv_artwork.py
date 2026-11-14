"""Normalize provider artwork into the same user-selectable destinations."""
import re
from app.television import safe_image, tvdb_image_url

KINDS={'poster':('poster.jpg','Posters'),'background':('fanart.jpg','Backgrounds'),'fanart':('fanart.jpg','Backgrounds'),'banner':('banner.jpg','Banners'),'clearlogo':('clearlogo.png','Logos'),'logo':('clearlogo.png','Logos'),'clearart':('clearart.png','Clearart'),'landscape':('landscape.jpg','Landscapes')}

def tvdb_choices(details,types):
    result=[]
    for item in details.get('artworks') or []:
        kind=re.sub('[^a-z]','',str(types.get(str(item.get('type')),'' )).lower()).removeprefix('series').removeprefix('movie')
        if kind not in KINDS:continue
        url=tvdb_image_url(item.get('image'))
        if not url:continue
        name,category=KINDS[kind]
        result.append({'name':name,'type':category,'url':url,'lang':item.get('language') or '', 'provider':'TVDB'})
    # Some series have a primary image but no artwork array.
    if not result and tvdb_image_url(details.get('image')):
        result.append({'name':'poster.jpg','type':'Posters','url':tvdb_image_url(details['image']),'lang':'','provider':'TVDB'})
    return result

def tmdb_choices(data):
    result=[]
    for key,name,category in [('posters','poster.jpg','Posters'),('backdrops','fanart.jpg','Backgrounds'),('logos','clearlogo.png','Logos')]:
        for item in data.get(key) or []:
            path=item.get('file_path') or ''
            if not re.fullmatch(r'/[A-Za-z0-9_./-]+',path) or '..' in path:continue
            url='https://image.tmdb.org/t/p/original'+path
            if safe_image(url):result.append({'name':name,'type':category,'url':url,'lang':item.get('iso_639_1') or '', 'provider':'TMDB'})
    return result
