class BaseModel:
    def __init__(self, **k):
        for c in reversed(type(self).__mro__):
            for n in getattr(c,'__dict__',{}):
                if not n.startswith('_') and not callable(getattr(c,n)): setattr(self,n,getattr(c,n))
        for n,v in k.items(): setattr(self,n,v)
