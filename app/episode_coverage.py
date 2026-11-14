"""User completeness corrections never change episode files."""
def coverage(local,remote,corrections,calculate):
    available={e['file'] for e in local['episodes']}|set(local.get('unrecognized',[]))
    linked={(c['season'],c['episode']):c['file'] for c in corrections if c['action']=='link' and c['file'] in available}
    ignored={(c['season'],c['episode']) for c in corrections if c['action']=='ignore'}
    inventory={**local,'episodes':[*local['episodes'],*({'file':file,'numbers':[[s,e]]} for (s,e),file in linked.items())]}
    result=calculate(inventory,remote,ignored=ignored)
    saved={(c['season'],c['episode']):c for c in corrections}
    for row in result['episodes']:
        key=(row['season'],row['number']);row['correction']=saved.get(key)
        if key in linked:row['included_in']=linked[key]
        elif key in saved and saved[key]['action']=='link':row['link_unavailable']=True
    return result
