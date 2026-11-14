"""Preserve filesystem bytes internally; never silently rename malformed paths."""
import json
from starlette.responses import JSONResponse

def valid_filename(value):
    try:str(value).encode('utf-8');return True
    except UnicodeEncodeError:return False

def display_filename(value):
    return str(value).encode('utf-8','backslashreplace').decode('utf-8')

class FilesystemJSONResponse(JSONResponse):
    def render(self,content):
        # JSON escapes preserve surrogateescaped bytes in legacy scan status,
        # instead of failing while UTF-8 encoding the HTTP response.
        return json.dumps(content,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('utf-8')
