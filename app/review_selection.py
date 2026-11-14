"""Constrain a signed full change plan to a user-selected subset of its operations."""
from pathlib import Path
import xml.etree.ElementTree as ET
from fastapi import HTTPException


def operation_ids(files,renames,folder_before,folder_after):
    ids=['file:'+name for name in files]
    if renames:ids.append('rename:files')
    if folder_before!=folder_after:ids.append('rename:folder')
    return ids


def select_operations(selected,files,renames,folder_before,folder_after):
    allowed=operation_ids(files,renames,folder_before,folder_after)
    if selected is None:return set(allowed)
    if not selected or len(selected)!=len(set(selected)) or set(selected)-set(allowed):
        raise HTTPException(400,'Choose at least one valid operation from this review.')
    return set(selected)


def subset_outputs(writes,images,renames,selected):
    """Remap generated sidecars if their connected video rename is deselected."""
    move='rename:files' in selected
    reverse={new:old for old,new in renames.items()}
    def original_name(name):
        if move:return name
        if name in reverse:return reverse[name]
        p=Path(name)
        for before,after in renames.items():
            a,b=Path(before),Path(after)
            if a.suffix.lower() not in {'.mkv','.mp4','.m4v','.avi','.mov','.wmv','.mpeg','.mpg','.ts','.webm','.m2ts'}:continue
            if p.parent==b.parent and (p.name==b.stem+'.nfo' or p.name.startswith(b.stem+'-thumb.')):
                return (a.parent/(a.stem+p.name[len(b.stem):])).as_posix()
        return name
    chosen_images={original_name(name):url for name,url in images.items() if 'file:'+name in selected}
    chosen_writes={}
    for name,content in writes.items():
        if 'file:'+name not in selected:continue
        destination=original_name(name)
        if Path(name).suffix.lower()=='.nfo':
            doc=ET.fromstring(content)
            for node in doc.iter('thumb'):
                value=(node.text or '').strip()
                if not value or '://' in value:continue
                referenced=(Path(name).parent/value).as_posix()
                # Actor portraits use a path relative to the show/movie folder.
                if value in images:referenced=value
                if referenced in images and 'file:'+referenced not in selected:
                    node.text=images[referenced]  # Retain a valid remote reference, without writing an unchecked image.
                elif not move:
                    mapped=original_name(referenced)
                    if mapped!=referenced:node.text=Path(mapped).name if '/' not in value else mapped
            ET.indent(doc)
            content=ET.tostring(doc,encoding='utf-8',xml_declaration=True)
        chosen_writes[destination]=content
    return chosen_writes,chosen_images,renames if move else {},original_name
