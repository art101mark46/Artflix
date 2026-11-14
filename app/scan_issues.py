"""Persistent, append-only scan warnings, independent of the progress window."""
import datetime
import os
import uuid
from fastapi.responses import StreamingResponse
from app.encoding import display_filename

SCHEMA='''CREATE TABLE IF NOT EXISTS scan_issues(id INTEGER PRIMARY KEY,scan_id TEXT,time TEXT,library TEXT,root TEXT,folder TEXT,path TEXT,path_bytes_hex TEXT,reason TEXT,UNIQUE(scan_id,path,reason))'''

class ScanWarnings(list):
    def __init__(self,database,library,root):
        super().__init__();self.database=database;self.library=library;self.root=root;self.scan_id=uuid.uuid4().hex;self._keys=set()
    def append(self,warning):
        path=warning.get('path') or str(self.root/warning.get('folder',''))
        record=(self.scan_id,datetime.datetime.now(datetime.timezone.utc).isoformat(),self.library,display_filename(self.root),display_filename(warning.get('folder','')),display_filename(path),os.fsencode(path).hex(),display_filename(warning['error']))
        key=(record[5],record[7])
        if key in self._keys:return
        with self.database() as connection:
            connection.execute(SCHEMA)
            connection.execute('INSERT OR IGNORE INTO scan_issues(scan_id,time,library,root,folder,path,path_bytes_hex,reason) VALUES (?,?,?,?,?,?,?,?)',record)
        self._keys.add(key)
        super().append(warning)
    def extend(self,warnings):
        for warning in warnings:self.append(warning)

def install_scan_issues(app,m):
    def rows(limit=None):
        with m.database() as connection:
            connection.execute(SCHEMA)
            query='SELECT id,scan_id,time,library,root,folder,path,path_bytes_hex,reason FROM scan_issues ORDER BY id DESC'
            records=connection.execute(query+(' LIMIT ?' if limit else ''),(limit,) if limit else ()).fetchall()
        for row in records:
            yield dict(zip(('id','scan_id','time','library','root','folder','path','path_bytes_hex','reason'),row))
    @app.get('/api/scan/issues')
    def scan_issues():
        with m.database() as connection:
            connection.execute(SCHEMA);count=connection.execute('SELECT count(*) FROM scan_issues').fetchone()[0]
        return {'total':count,'issues':list(rows(200)),'notice':'Historical scan warnings. Rescan after fixing a file; older entries remain as a record.'}
    @app.get('/api/scan/issues/download')
    def download_scan_issues():
        def content():
            yield 'ArtFlix scan warnings — newest first\nHistorical entries remain after a file is corrected. Scanning did not modify these files.\n\n'
            for row in rows():
                yield f"Time: {row['time']}\nLibrary: {row['library']}\nPath: {row['path']}\nIssue: {row['reason']}\nScan: {row['scan_id']}\nOriginal path bytes (hex): {row['path_bytes_hex']}\n\n"
        return StreamingResponse(content(),media_type='text/plain; charset=utf-8',headers={'Content-Disposition':'attachment; filename="artflix-scan-warnings.txt"','Cache-Control':'no-store'})
