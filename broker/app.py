"""GPUBerth: AI agents book scarce GPUs for a limited time, each in an isolated VM."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

CONFIG = tomllib.loads(
    Path(os.environ.get("GPUBERTH_CONFIG", "/etc/gpuberth/config.toml")).read_text())
BASE = Path(CONFIG["data_dir"])
APP_DIR = Path(__file__).parent
DB = BASE / "broker.db"
KEYS = BASE / "keys"
HOST_SSH = CONFIG["host_ssh"]
WAKE_SSH = CONFIG.get("wake_ssh", "")  # empty: the GPU host is always on
VM_HOST = CONFIG["vm_gateway"]
PUBLIC_HOST = CONFIG["public_host"]
PUBLIC_URL = CONFIG["public_url"]
# A seat is one agent VM. The first seat should allow all GPUs; further seats take smaller bookings.
SEATS = {seat["name"]: seat for seat in CONFIG["seats"]}
TOTAL_GPUS = CONFIG["total_gpus"]
ALLOWED_GPUS = sorted({n for seat in SEATS.values() for n in seat["gpus"]})
_limits = CONFIG.get("limits", {})
MIN_HOURS = _limits.get("min_hours", 0.25)
MAX_HOURS = _limits.get("max_hours", 8)
DAILY_HOURS = _limits.get("daily_hours", 16)
MAX_OPEN = _limits.get("max_open", 2)
WARN_SECONDS = _limits.get("warn_seconds", 600)
POOL_LIMIT = _limits.get("pool_limit_pct", 90)
LIVE = ("queued", "waiting_approval", "starting", "ready", "ending")
# Runs as root in the VM at booking start. The storage VM exports only this agent's folder
# and the common one to this seat, so nothing else is reachable even for root in the VM.
SHARED_SETUP = """set -e
mkdir -p /shared/my-work /shared/common
O=vers=4.2,hard,nosuid,nodev,noatime
mountpoint -q /shared/my-work || mount -t nfs -o $O {storage}:/srv/agents/{agent} /shared/my-work
mountpoint -q /shared/common || mount -t nfs -o $O {storage}:/srv/common /shared/common
sed -i '/^# gpuberth/,$d' /etc/environment
cat >> /etc/environment <<E
# gpuberth
HF_HOME=/shared/common/huggingface
TORCH_HOME=/shared/common/torch
PIP_CACHE_DIR=/shared/common/pip
UV_CACHE_DIR=/shared/common/uv
CONDA_PKGS_DIRS=/shared/common/conda-pkgs
E
"""

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("broker")
db_lock = threading.Lock()


def now():
    return datetime.now(timezone.utc)


def iso(t):
    return t.isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def q(sql, *args):
    with db_lock, conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def init_db():
    with db_lock, conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS agents(name TEXT PRIMARY KEY, token_hash TEXT,
            role TEXT DEFAULT 'agent', active INT DEFAULT 1);
        CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY, agent TEXT, hours REAL,
            purpose TEXT, ssh_pubkey TEXT, status TEXT, step TEXT DEFAULT '', created_at TEXT,
            started_at TEXT, ends_at TEXT, warned INT DEFAULT 0, approved INT DEFAULT 0,
            end_requested INT DEFAULT 0, certificate TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        cols = [r[1] for r in c.execute("PRAGMA table_info(bookings)")]
        if "gpus" not in cols:
            c.execute("ALTER TABLE bookings ADD COLUMN gpus INT DEFAULT 2")
            c.execute("ALTER TABLE bookings ADD COLUMN seat TEXT DEFAULT 'a'")


def get_setting(key, default=""):
    r = q("SELECT value FROM settings WHERE key=?", key)
    return r[0]["value"] if r else default


def set_setting(key, value):
    q("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      key, value)


def update(bid, **fields):
    cols = ",".join(f"{k}=?" for k in fields)
    q(f"UPDATE bookings SET {cols} WHERE id=?", *fields.values(), bid)


def booking(bid):
    r = q("SELECT * FROM bookings WHERE id=?", bid)
    return r[0] if r else None


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# --- remote control ---------------------------------------------------------

def run(cmd, timeout=30, input_text=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input_text)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def ssh_opts(key):
    return ["ssh", "-i", str(KEYS / key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
            "-o", f"UserKnownHostsFile={BASE}/known_hosts", "-o", "StrictHostKeyChecking=accept-new"]


def ctl(verb, timeout=30):
    return run(ssh_opts("control") + [HOST_SSH, verb], timeout=timeout)


def ctl_status():
    rc, out, _ = ctl("status", timeout=45)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def wake():
    if not WAKE_SSH:
        return
    rc, _, err = run(ssh_opts("wake") + [WAKE_SSH, "wake"], timeout=20)
    log.info("wake signal rc=%s %s", rc, err)


def vm(seat, command, timeout=30, input_text=None):
    return run(ssh_opts("vmadmin") + ["-p", str(SEATS[seat]["ssh_port"]), f"gpuadmin@{VM_HOST}", command],
               timeout=timeout, input_text=input_text)


def wait_for(check, seconds, every=5, tick=None):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        if tick:
            tick()
        time.sleep(every)
    return False


def sign_cert(b, ends_at):
    seconds = int((ends_at - now()).total_seconds())
    with tempfile.TemporaryDirectory() as d:
        pub = Path(d) / "agent.pub"
        pub.write_text(b["ssh_pubkey"] + "\n")
        rc, _, err = run(["ssh-keygen", "-q", "-s", str(BASE / "ca"), "-I", b["id"], "-n", b["id"],
                          "-V", f"-1m:+{seconds}s", "-O", "no-x11-forwarding",
                          "-O", "no-agent-forwarding", str(pub)])
        if rc != 0:
            raise RuntimeError(f"cert signing failed: {err}")
        return (Path(d) / "agent-cert.pub").read_text().strip()


# --- booking life cycle -----------------------------------------------------

def fail(b, message):
    log.error("booking %s failed: %s", b["id"], message)
    update(b["id"], status="failed", error=message, step="", certificate=None)


def wake_host(b):
    update(b["id"], step="waking the GPU host")
    deadline = time.time() + 300
    while time.time() < deadline:
        wake()
        if wait_for(lambda: ctl_status() is not None, 60, every=8):
            return True
    return False


def start_agent_vm(b):
    """Start the seat's VM and confirm its GPUs; returns an error text or None."""
    seat = b["seat"]
    if not re.fullmatch(r"[a-z0-9-]{1,32}", b["agent"]):
        return "invalid agent name"
    rc, _, err = ctl(f"start {seat} {b['gpus']}", timeout=150)
    if rc != 0:
        return f"VM start failed: {err}"
    if not wait_for(lambda: vm(seat, "true", timeout=12)[0] == 0, 180, every=5):
        return "VM did not answer SSH"
    rc, _, err = vm(seat, "sudo tee /etc/ssh/principals/agent >/dev/null", input_text=b["id"] + "\n")
    if rc != 0:
        return f"could not set login: {err}"
    rc, _, err = ctl(f"share {seat} {b['agent']}", timeout=60)
    if rc != 0:
        return f"could not share folders: {err}"
    script = SHARED_SETUP.format(agent=b["agent"], storage=SEATS[seat]["storage_ip"])
    rc, _, err = vm(seat, "sudo bash -s", input_text=script, timeout=60)
    if rc != 0:
        return f"could not mount shared folders: {err}"
    # SSH answers before the NVIDIA driver has finished loading
    result = {"found": 0, "err": ""}

    def gpus_ready():
        rc, out, err = vm(seat, "nvidia-smi -L", timeout=40)
        result.update(found=out.count("GPU "), err=err)
        return rc == 0 and result["found"] == b["gpus"]

    if not wait_for(gpus_ready, 90, every=5):
        return f"found {result['found']} of {b['gpus']} GPUs: {result['err']}"
    rc, _, err = vm(seat, "sudo /usr/local/sbin/gpuberth-cdi", timeout=60)
    if rc != 0:
        return f"could not set up GPU containers: {err}"
    return None


def start_booking(b):
    others = [o for o in active() if o["id"] != b["id"]]
    seat = b["seat"] if b["status"] == "starting" else pick_seat(b["gpus"], others)
    update(b["id"], status="starting", step="checking the GPU host", error=None, seat=seat)
    b = booking(b["id"])
    restore = get_setting("restore")
    st = ctl_status()
    if st is None:
        if not restore:
            set_setting("restore", "off")
        if not wake_host(b):
            fail(b, "the GPU host did not wake up within 5 minutes")
            return
        st = ctl_status()
        if st is None:
            fail(b, "the GPU host stopped answering after wake")
            return
    if st.get("pool_pct", 0) >= POOL_LIMIT:
        fail(b, f"the GPU host disk pool is {st['pool_pct']}% full")
        return
    if st.get("seats", {}).get(seat):
        ctl(f"stop {seat}", timeout=150)
    if st.get("owner"):
        if st.get("busy") and not b["approved"]:
            update(b["id"], status="waiting_approval",
                   step="the owner VM is in use, owner must approve")
            return
        if not restore:
            set_setting("restore", "owner")
        update(b["id"], step="stopping the owner VM")
        rc, _, err = ctl("stop-owner", timeout=180)
        if rc != 0:
            update(b["id"], status="waiting_approval",
                   step=f"the owner VM would not stop cleanly: {err}", approved=0)
            return
    elif not get_setting("restore"):
        set_setting("restore", "idle")

    update(b["id"], step="starting GPU VM")
    error = start_agent_vm(b)
    if error and not others:
        # only safe while nobody else is working on the machine
        log.warning("first start failed: %s; power cycling the GPU host", error)
        update(b["id"], step="GPU problem, power cycling the GPU host")
        ctl(f"stop {seat}", timeout=150)
        ctl("poweroff")
        wait_for(lambda: ctl_status() is None, 180, every=5)
        time.sleep(20)
        if not wake_host(b):
            fail(b, f"{error}; the GPU host did not come back")
            return
        error = start_agent_vm(b)
    if error:
        ctl(f"unshare {seat}", timeout=60)
        ctl(f"stop {seat}", timeout=150)
        fail(b, error)
        return

    ends_at = now() + timedelta(hours=b["hours"])
    update(b["id"], status="ready", step="", started_at=iso(now()), ends_at=iso(ends_at),
           warned=0, certificate=sign_cert(b, ends_at))
    log.info("booking %s ready on seat %s until %s", b["id"], seat, iso(ends_at))


def end_booking(b):
    update(b["id"], status="ending", step="stopping and resetting VM", certificate=None)
    rc, _, err = ctl(f"stop {b['seat']}", timeout=150)
    ctl(f"unshare {b['seat']}", timeout=60)
    if rc != 0:
        if ctl_status() is None:
            log.warning("the GPU host unreachable while ending %s", b["id"])
        else:
            update(b["id"], step=f"reset failed, retrying: {err}")
            return
    update(b["id"], status="done", step="")
    log.info("booking %s done", b["id"])


def restore_host():
    restore = get_setting("restore")
    if not restore:
        return
    if ctl_status() is None:  # already off, nothing left to put back
        set_setting("restore", "")
        return
    if restore == "owner":
        rc, _, err = ctl("start-owner", timeout=60)
    elif restore == "off":
        rc, _, err = ctl("poweroff")
    else:
        rc, err = 0, ""
    log.info("restore %s rc=%s %s", restore, rc, err)
    if rc == 0 or restore == "idle":
        set_setting("restore", "")
    elif rc in (3,):  # refused by the guard on the GPU host: somebody is using it, leave it on
        set_setting("restore", "")


def active():
    return q("SELECT * FROM bookings WHERE status IN ('starting','ready','ending')")


def free_gpus():
    return TOTAL_GPUS - sum(b["gpus"] for b in active())


def pick_seat(gpus, others):
    taken = {o["seat"] for o in others}
    for name, seat in SEATS.items():
        if name not in taken and gpus in seat["gpus"]:
            return name
    return None


def watch_ready(b, state):
    left = (datetime.fromisoformat(b["ends_at"]) - now()).total_seconds()
    if b["end_requested"] or left <= 0:
        end_booking(b)
        return
    if left <= WARN_SECONDS and not b["warned"]:
        vm(b["seat"], "sudo wall 'GPU booking ends in 10 minutes: save your files now'")
        update(b["id"], warned=1)
    if time.time() - state.get(b["id"], {}).get("checked", 0) > 60:
        w = state.setdefault(b["id"], {"misses": 0})
        w["checked"] = time.time()
        st = ctl_status()
        running = st and st.get("seats", {}).get(b["seat"])
        w["misses"] = 0 if running else w["misses"] + 1
        if w["misses"] >= 3:
            fail(b, "GPU VM stopped unexpectedly")
            ctl(f"unshare {b['seat']}", timeout=60)
            ctl(f"stop {b['seat']}", timeout=150)


def tick(state):
    for b in active():
        if b["status"] == "ending":
            end_booking(b)
        elif b["status"] == "starting":
            # the service restarted during a start: run it again, every step is safe to repeat
            start_booking(b)
        else:
            watch_ready(b, state)
    if get_setting("block") != "1":
        # strictly first come, first served, so a 2-GPU booking is never overtaken forever
        waiting = q("SELECT * FROM bookings WHERE status IN ('queued','waiting_approval') "
                    "ORDER BY created_at LIMIT 1")
        if waiting:
            head = waiting[0]
            if head["gpus"] <= free_gpus() and pick_seat(head["gpus"], active()):
                start_booking(head)
                if booking(head["id"])["status"] == "waiting_approval":
                    time.sleep(25)
            return
    if not active():
        restore_host()


def worker():
    state = {}
    while True:
        try:
            tick(state)
        except Exception:
            log.exception("worker error")
        time.sleep(5)


# --- HTTP API ---------------------------------------------------------------

app = FastAPI(title="GPU booking", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=worker, daemon=True).start()


def auth(authorization, admin=False):
    token = (authorization or "").removeprefix("Bearer ").strip()
    if token:
        digest = hash_token(token)
        for a in q("SELECT * FROM agents WHERE active=1"):
            if hmac.compare_digest(a["token_hash"], digest):
                if admin and a["role"] != "admin":
                    break
                return a
    raise HTTPException(401, "invalid token")


def public(b, full=True):
    data = {k: b[k] for k in ("id", "agent", "hours", "gpus", "purpose", "status", "step",
                              "created_at", "started_at", "ends_at", "error")}
    if b["status"] == "ready":
        data["seconds_left"] = int((datetime.fromisoformat(b["ends_at"]) - now()).total_seconds())
        if full:
            data["certificate"] = b["certificate"]
            seat = SEATS[b["seat"]]
            data["ssh_port"] = int(seat["ssh_port"])
            data["ssh"] = (f"ssh -p {seat['ssh_port']} -i YOUR_KEY -o CertificateFile=cert.pub "
                           f"agent@{PUBLIC_HOST}")
            data["public_ports"] = seat["ports"]
            data["public_ip"] = PUBLIC_HOST
    if b["status"] in ("queued", "waiting_approval"):
        ahead = q("SELECT COUNT(*) n FROM bookings WHERE status IN "
                  "('queued','waiting_approval','starting','ready','ending') AND created_at < ?",
                  b["created_at"])
        data["position"] = ahead[0]["n"] + 1
    return data


def owned(bid, who):
    b = booking(bid)
    if not b or (b["agent"] != who["name"] and who["role"] != "admin"):
        raise HTTPException(404, "booking not found")
    return b


class NewBooking(BaseModel):
    hours: float
    purpose: str
    ssh_public_key: str
    gpus: int = TOTAL_GPUS


class Extend(BaseModel):
    hours: float


class Block(BaseModel):
    on: bool


def hours_used(agent):
    since = iso(now() - timedelta(hours=24))
    r = q("SELECT COALESCE(SUM(hours),0) h FROM bookings WHERE agent=? AND created_at>? "
          "AND status NOT IN ('failed','cancelled')", agent, since)
    return r[0]["h"]


@app.post("/api/bookings", status_code=201)
def create(body: NewBooking, authorization: str = Header(None)):
    who = auth(authorization)
    if not MIN_HOURS <= body.hours <= MAX_HOURS:
        raise HTTPException(422, f"hours must be {MIN_HOURS} to {MAX_HOURS}")
    if body.gpus not in ALLOWED_GPUS:
        raise HTTPException(422, f"gpus must be one of {ALLOWED_GPUS}")
    purpose = body.purpose.strip()[:200]
    if not purpose:
        raise HTTPException(422, "purpose is required")
    key = body.ssh_public_key.strip()
    if not re.fullmatch(r"(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+( [\w.@+-]*)?", key):
        raise HTTPException(422, "invalid ssh_public_key")
    if run(["ssh-keygen", "-l", "-f", "-"], input_text=key)[0] != 0:
        raise HTTPException(422, "invalid ssh_public_key")
    open_now = q("SELECT COUNT(*) n FROM bookings WHERE agent=? AND status IN "
                 "('queued','waiting_approval','starting','ready','ending')", who["name"])[0]["n"]
    if open_now >= MAX_OPEN:
        raise HTTPException(429, f"at most {MAX_OPEN} open bookings per agent")
    if hours_used(who["name"]) + body.hours > DAILY_HOURS:
        raise HTTPException(429, f"limit of {DAILY_HOURS} hours per 24 hours reached")
    bid = "b-" + secrets.token_hex(4)
    q("INSERT INTO bookings(id,agent,hours,gpus,purpose,ssh_pubkey,status,created_at) "
      "VALUES(?,?,?,?,?,?,'queued',?)", bid, who["name"], body.hours, body.gpus, purpose, key,
      now().isoformat(timespec="milliseconds"))
    log.info("new booking %s by %s for %sh with %s GPU", bid, who["name"], body.hours, body.gpus)
    return public(booking(bid))


@app.get("/api/bookings")
def my_bookings(authorization: str = Header(None)):
    who = auth(authorization)
    rows = q("SELECT * FROM bookings WHERE agent=? ORDER BY created_at DESC LIMIT 50", who["name"])
    return [public(b) for b in rows]


@app.get("/api/availability")
def availability(authorization: str = Header(None)):
    auth(authorization)
    waiting = q("SELECT COUNT(*) n FROM bookings WHERE status IN ('queued','waiting_approval')")
    return {"total_gpus": TOTAL_GPUS, "free_gpus": free_gpus(), "waiting_bookings": waiting[0]["n"]}


@app.get("/api/bookings/{bid}")
def get_booking(bid: str, authorization: str = Header(None)):
    return public(owned(bid, auth(authorization)))


@app.post("/api/bookings/{bid}/extend")
def extend(bid: str, body: Extend, authorization: str = Header(None)):
    who = auth(authorization)
    b = owned(bid, who)
    if b["status"] != "ready":
        raise HTTPException(409, "only a ready booking can be extended")
    if not 0 < body.hours <= MAX_HOURS:
        raise HTTPException(422, f"hours must be up to {MAX_HOURS}")
    if q("SELECT 1 FROM bookings WHERE status IN ('queued','waiting_approval') LIMIT 1"):
        raise HTTPException(409, "another booking is waiting")
    if hours_used(b["agent"]) + body.hours > DAILY_HOURS:
        raise HTTPException(429, f"limit of {DAILY_HOURS} hours per 24 hours reached")
    ends_at = datetime.fromisoformat(b["ends_at"]) + timedelta(hours=body.hours)
    update(bid, ends_at=iso(ends_at), hours=b["hours"] + body.hours, warned=0,
           certificate=sign_cert(b, ends_at))
    return public(booking(bid))


@app.delete("/api/bookings/{bid}")
def cancel(bid: str, authorization: str = Header(None)):
    b = owned(bid, auth(authorization))
    if b["status"] in ("queued", "waiting_approval"):
        update(bid, status="cancelled", step="")
    elif b["status"] in ("starting", "ready"):
        update(bid, end_requested=1)
    return public(booking(bid), full=False)


@app.get("/api/admin/overview")
def overview(authorization: str = Header(None)):
    auth(authorization, admin=True)
    rows = q("SELECT * FROM bookings ORDER BY created_at DESC LIMIT 40")
    return {"block": get_setting("block") == "1", "restore": get_setting("restore"),
            "free_gpus": free_gpus(), "total_gpus": TOTAL_GPUS,
            "host": ctl_status(), "bookings": [public(b, full=False) for b in rows]}


@app.post("/api/admin/block")
def block(body: Block, authorization: str = Header(None)):
    auth(authorization, admin=True)
    set_setting("block", "1" if body.on else "0")
    return {"block": body.on}


@app.post("/api/admin/bookings/{bid}/approve")
def approve(bid: str, authorization: str = Header(None)):
    auth(authorization, admin=True)
    b = booking(bid)
    if not b or b["status"] != "waiting_approval":
        raise HTTPException(409, "booking is not waiting for approval")
    update(bid, approved=1)
    return public(booking(bid), full=False)


@app.get("/dl/{name}", response_class=PlainTextResponse)
def download(name: str):
    """One-time file: gone after the first download, or after 24 hours."""
    if not re.fullmatch(r"[a-f0-9]{48}", name):
        raise HTTPException(404)
    src = BASE / "downloads" / f"{name}.md"
    taken = src.with_suffix(".taken")
    try:
        src.rename(taken)  # atomic, so two requests cannot both get the file
    except FileNotFoundError:
        raise HTTPException(404)
    age = time.time() - taken.stat().st_mtime
    text = taken.read_text()
    taken.unlink()
    if age > 86400:
        raise HTTPException(404)
    return PlainTextResponse(text, headers={
        "Content-Disposition": 'attachment; filename="gpu-agent-access.md"',
        "Cache-Control": "no-store"})


def render_guide(token, agent):
    ports = " or ".join("-".join(map(str, (s["ports"][0], s["ports"][-1]))) for s in SEATS.values())
    text = (APP_DIR / "agent-guide.md").read_text()
    for key, value in (("TOKEN", token), ("AGENT", agent), ("URL", PUBLIC_URL), ("HOST", PUBLIC_HOST),
                       ("PORTS", ports), ("MAX_HOURS", MAX_HOURS), ("DAILY_HOURS", DAILY_HOURS),
                       ("MAX_OPEN", MAX_OPEN)):
        text = text.replace("{{" + key + "}}", str(value))
    return text


@app.get("/", response_class=HTMLResponse)
def page():
    return (APP_DIR / "index.html").read_text()


@app.get("/agent.md", response_class=PlainTextResponse)
def agent_doc():
    return render_guide("YOUR_TOKEN", "YOUR_AGENT_NAME")
