class HTTPException(Exception):
    def __init__(self, status_code, detail=""): self.status_code, self.detail = status_code, detail
def Header(default=None): return default
class Request: pass
class FastAPI:
    def __init__(self, **k): pass
    def _d(self, *a, **k): return lambda f: f
    get = post = delete = on_event = _d
