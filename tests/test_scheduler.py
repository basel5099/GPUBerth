"""Scheduler tests against a simulated GPU host. Run: python3 tests/test_scheduler.py"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
d = tempfile.mkdtemp()
Path(d, "config.toml").write_text(f"""
data_dir = "{d}"
public_url = "https://gpu.example.com"
public_host = "203.0.113.10"
host_ssh = "root@192.0.2.20"
wake_ssh = "root@192.0.2.10"
vm_gateway = "192.0.2.1"
total_gpus = 2
[[seats]]
name = "a"
ssh_port = 6050
ports = [6051, 6052, 6053, 6054]
storage_ip = "10.50.0.2"
gpus = [1, 2]
[[seats]]
name = "b"
ssh_port = 6055
ports = [6056, 6057, 6058, 6059]
storage_ip = "10.51.0.2"
gpus = [1]
""")
os.environ["GPUBERTH_CONFIG"] = d + "/config.toml"
sys.path.insert(0, str(ROOT / "broker"))
try:
    import fastapi  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
app = importlib.import_module("app")
subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',d+'/ca'],check=True)
subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',d+'/k'],check=True)
PUB=open(d+'/k.pub').read().strip()
app.init_db()
for n in ('one','two','three'):
    app.q("INSERT INTO agents VALUES(?,?,'agent',1)",n,app.hash_token('t-'+n))
class H:  # simulated GPU host
    def reset(s): s.on=True; s.ic=False; s.busy=False; s.seats={'a':0,'b':0}; s.log=[]; s.nvidia=None; s.shares={}
h=H(); h.reset()
def ctl(verb,timeout=30):
    h.log.append(verb); p=verb.split()
    if p[0]=='status':
        return (0,json.dumps({'owner':h.ic,'seats':{k:bool(v) for k,v in h.seats.items()},'busy':h.busy,'pool_pct':50,'shared_pct':1}),'') if h.on else (255,'','down')
    if not h.on: return 255,'','down'
    if p[0]=='start':
        assert not h.ic,'GPU conflict with ic'
        n=int(p[2]); used=sum(h.seats.values()); assert used+n<=2,'GPU overbooked'; assert h.seats[p[1]]==0,'seat busy'
        assert (p[1],n) in (('a',1),('a',2),('b',1)),p
        h.seats[p[1]]=n; return 0,'',''
    if p[0]=='stop': h.seats[p[1]]=0; return 0,'',''
    if p[0]=='share': h.shares[p[1]]=p[2]; return 0,'',''
    if p[0]=='unshare': h.shares.pop(p[1],None); return 0,'',''
    if p[0]=='stop-owner': h.ic=False; return 0,'',''
    if p[0]=='start-owner': assert sum(h.seats.values())==0,'ic while agent runs'; h.ic=True; return 0,'',''
    if p[0]=='poweroff': h.on=False; return 0,'',''
    return 2,'','bad'
def vm(seat,cmd,timeout=30,input_text=None):
    if 'nvidia-smi' in cmd:
        n=h.nvidia if h.nvidia is not None else h.seats[seat]
        return 0,'\n'.join(f'GPU {i}: x' for i in range(n)),''
    return 0,'',''
app.ctl=ctl; app.vm=vm; app.wake=lambda: setattr(h,'on',True)
app.time.sleep=lambda *_: None
real_wf=app.wait_for
app.wait_for=lambda check,seconds,every=5,tick=None: any(check() for _ in range(3))
def book(who,gpus=None,hours=1):
    kw=dict(hours=hours,purpose='p',ssh_public_key=PUB)
    if gpus is not None: kw['gpus']=gpus
    return app.create(app.NewBooking(**kw),'Bearer t-'+who)['id']
def st(i): return app.booking(i)['status']
def seat(i): return app.booking(i)['seat']
def tick(n=3):
    for _ in range(n): app.tick(S)
def end(i): app.update(i,end_requested=1)
def clear():
    app.q("DELETE FROM bookings"); app.q("DELETE FROM settings"); h.reset(); S.clear()
S={}; ok=0
def check(name,c):
    global ok
    print(('PASS ' if c else 'FAIL ')+name); ok+=0 if c else 1
def avail(): return app.availability('Bearer t-one')['free_gpus']

# 1 default stays 2 GPUs on seat a
a=book('one'); tick(); check('no gpus field = 2 GPUs, seat a',st(a)=='ready' and app.booking(a)['gpus']==2 and seat(a)=='a')
p=app.public(app.booking(a)); check('seat a ports',p['ssh_port']==6050 and p['public_ports']==[6051,6052,6053,6054]); check('availability 0',avail()==0)
clear()
# 2 two 1-GPU side by side
a=book('one',1); tick(); b=book('two',1); tick()
check('two 1-GPU run together',st(a)=='ready' and st(b)=='ready' and seat(a)=='a' and seat(b)=='b')
pb=app.public(app.booking(b)); check('seat b ports',pb['ssh_port']==6055 and pb['public_ports']==[6056,6057,6058,6059])
check('each sees own share',h.shares=={'a':'one','b':'two'}); check('availability 0 full',avail()==0)
# 3 third waits, ending one lets it in on free seat
c=book('three',1); tick(); check('third waits',st(c)=='queued')
end(a); tick(); check('third gets freed seat a',st(c)=='ready' and seat(c)=='a' and st(b)=='ready')
check('share swapped to new agent',h.shares=={'a':'three','b':'two'})
clear()
# 4 2-GPU waits while 1 runs, later 1-GPU may not overtake
a=book('one',1); tick(); b=book('two',2); tick(); c=book('three',1); tick()
check('availability 1 while one runs',avail()==1)
check('2-GPU waits with 1 free',st(b)=='queued'); check('later 1-GPU does not overtake',st(c)=='queued')
end(a); tick(); check('2-GPU starts when both free',st(b)=='ready' and h.seats=={'a':2,'b':0} and st(c)=='queued')
end(b); tick(); check('then the 1-GPU',st(c)=='ready')
clear()
# 5 ic restore only after the last booking
h.ic=True; a=book('one',1); tick(); b=book('two',1); tick()
check('restore marked owner',app.get_setting('restore')=='owner' and not h.ic)
end(a); tick(); check('owner VM stays off while b runs',not h.ic and st(b)=='ready')
end(b); tick(); check('owner VM back after last',h.ic and app.get_setting('restore')=='')
clear()
# 6 off -> wake -> both end -> poweroff only at last
h.on=False; a=book('one',1); tick(); check('woke',h.on and st(a)=='ready'); b=book('two',1); tick()
end(a); tick(); check('stays on while b runs',h.on)
end(b); tick(); check('powers off after last',h.on is False)
clear()
# 7 GPU fault with other booking running must not power cycle
a=book('one',1); tick(); h.nvidia=0; b=book('two',1); tick(1)
check('fault fails only b',st(b)=='failed' and st(a)=='ready' and 'poweroff' not in h.log and h.seats['a']==1 and h.seats['b']==0)
clear()
# 8 extend refused while waiting; bad gpus; expiry per booking
a=book('one',1); tick(); b=book('two',1); tick()
try: app.create(app.NewBooking(hours=1,purpose='p',ssh_public_key=PUB,gpus=3),'Bearer t-one'); r=False
except app.HTTPException as e: r=e.status_code==422
check('gpus=3 refused',r)
app.update(a,ends_at=app.iso(app.now()-timedelta(seconds=1))); tick()
check('one expiry leaves other',st(a)=='done' and st(b)=='ready')
clear()
# 9 busy ic waits approval
h.ic=True; h.busy=True; a=book('one',1); tick(); check('busy owner VM waits',st(a)=='waiting_approval' and h.ic)
app.update(a,approved=1); tick(); check('approved runs',st(a)=='ready' and not h.ic)
print('FAILED' if ok else 'ALL PASSED', ok)
sys.exit(1 if ok else 0)
