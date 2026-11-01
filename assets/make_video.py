#!/usr/bin/env python3
"""Draws the GPUBerth explainer video frame by frame. Nothing here is AI generated.

Needs Pillow, imageio-ffmpeg and the DejaVu fonts.
  python3 make_video.py out.mp4            render the video
  python3 make_video.py --still 12.5 x.png render one frame
"""
import math
import subprocess
import sys

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 24
FONT_DIRS = ("./", "/usr/share/fonts/truetype/dejavu/")
BG = (15, 23, 32)
PANEL = (27, 38, 51)
LINE = (58, 78, 98)
TEXT = (232, 237, 242)
MUTED = (147, 164, 181)
GREEN = (62, 207, 142)
AMBER = (245, 181, 74)
RED = (240, 100, 90)
BLUE = (90, 169, 240)
PURPLE = (176, 136, 245)


def load_font(name, size):
    for folder in FONT_DIRS:
        try:
            return ImageFont.truetype(folder + name, size)
        except OSError:
            pass
    raise SystemExit(f"font {name} not found")


FONTS = {}


def font(size, kind="sans"):
    name = {"sans": "DejaVuSans.ttf", "bold": "DejaVuSans-Bold.ttf", "mono": "DejaVuSansMono.ttf"}[kind]
    return FONTS.setdefault((name, size), load_font(name, size))


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def lerp(a, b, t):
    return a + (b - a) * t


def show(t, start, length=0.5):
    """0 before start, then eases to 1."""
    return ease((t - start) / length)


class Canvas:
    def __init__(self):
        self.img = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.img)

    def box(self, x, y, w, h, fill=PANEL, line=LINE, a=1, r=12, width=2):
        if a <= 0:
            return
        self.d.rounded_rectangle((x, y, x + w, y + h), r, fill=mix(BG, fill, a),
                                 outline=mix(BG, line, a), width=width)

    def text(self, x, y, s, size=24, col=TEXT, a=1, kind="sans", anchor="la"):
        if a > 0:
            self.d.text((x, y), s, font=font(size, kind), fill=mix(BG, col, a), anchor=anchor)

    def line(self, x1, y1, x2, y2, col=LINE, a=1, width=3):
        if a > 0:
            self.d.line((x1, y1, x2, y2), fill=mix(BG, col, a), width=width)

    def arrow(self, x1, y1, x2, y2, col=BLUE, a=1, p=1, width=4):
        if a <= 0 or p <= 0:
            return
        ex, ey = lerp(x1, x2, p), lerp(y1, y2, p)
        self.line(x1, y1, ex, ey, col, a, width)
        ang = math.atan2(y2 - y1, x2 - x1)
        c = mix(BG, col, a)
        pts = [(ex, ey)]
        for off in (2.6, -2.6):
            pts.append((ex + 16 * math.cos(ang + off), ey + 16 * math.sin(ang + off)))
        self.d.polygon(pts, fill=c)

    def cross(self, x, y, size=16, a=1):
        self.line(x - size, y - size, x + size, y + size, RED, a, 7)
        self.line(x - size, y + size, x + size, y - size, RED, a, 7)

    def agent(self, x, y, col=BLUE, label="", a=1):
        """A small robot. x, y is its centre."""
        if a <= 0:
            return
        self.line(x, y - 34, x, y - 25, col, a, 3)
        self.d.ellipse((x - 4, y - 40, x + 4, y - 32), fill=mix(BG, col, a))
        self.d.rounded_rectangle((x - 26, y - 25, x + 26, y + 25), 10, fill=mix(BG, col, a))
        for ex in (-11, 11):
            self.d.ellipse((x + ex - 5, y - 10, x + ex + 5, y), fill=BG)
        self.d.rounded_rectangle((x - 11, y + 8, x + 11, y + 13), 2, fill=BG)
        if label:
            self.text(x, y + 38, label, 16, MUTED, a, anchor="ma")

    def gpu(self, x, y, label="GPU", col=GREEN, a=1, w=150, h=84):
        self.box(x, y, w, h, PANEL, col, a)
        for i in range(2):
            cx = x + w * (0.3 + 0.4 * i)
            cy = y + h * 0.42
            self.d.ellipse((cx - 19, cy - 19, cx + 19, cy + 19), outline=mix(BG, col, a), width=3)
            self.d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=mix(BG, col, a))
        self.text(x + w / 2, y + h - 21, label, 15, TEXT, a, anchor="ma")

    def pill(self, x, y, s, col, a=1, on=True, w=150):
        self.box(x, y, w, 42, col if on else PANEL, col if on else LINE, a, r=21)
        self.text(x + w / 2, y + 21, s, 19, BG if on else MUTED, a, "bold", "mm")

    def heading(self, s, t):
        self.text(60, 44, s, 38, TEXT, show(t, 0.1), "bold")

    def caption(self, s, t, start=0.3):
        self.text(W / 2, H - 56, s, 25, MUTED, show(t, start), anchor="ma")


def scene_title(c, t):
    a = show(t, 0.2, 0.8)
    x = lerp(-260, 170, ease((t - 0.2) / 1.1))
    c.line(0, 470, W, 470, LINE, 1, 3)
    for i in range(6):
        c.line(180 + i * 180, 470, 180 + i * 180, 520, LINE, 1, 8)
    c.agent(x + 70, 425, GREEN)
    c.text(W / 2, 210, "GPUBerth", 92, TEXT, a, "bold", "mm")
    c.text(W / 2, 300, "Book, isolate and share expensive GPUs", 34, BLUE, show(t, 0.9), anchor="mm")
    c.text(W / 2, 348, "between AI agents", 34, BLUE, show(t, 1.1), anchor="mm")
    c.caption("A berth is a reserved place where a ship docks. Here, agents dock at GPUs.", t, 1.8)


def scene_problem(c, t):
    c.heading("The problem", t)
    pos = [(140, 200), (140, 330), (140, 460), (290, 260), (290, 400), (140, 590 - 60)]
    for i, (x, y) in enumerate(pos[:5]):
        a = show(t, 0.3 + i * 0.25)
        c.agent(x, y, BLUE, f"agent {i + 1}", a)
        for j, gy in enumerate((210, 380)):
            c.arrow(x + 40, y, 830, gy + 42, RED, 0.75, ease((t - 2.2 - i * 0.12) / 0.8), 2)
    c.gpu(840, 210, "GPU 1", GREEN, show(t, 1.3), 190, 100)
    c.gpu(840, 380, "GPU 2", GREEN, show(t, 1.5), 190, 100)
    c.text(860, 145, "Very expensive", 22, AMBER, show(t, 1.6))
    c.caption("Many AI agents want a few expensive GPUs.", t, 0.5)
    for i, s in enumerate(("Who gets the GPU?", "For how long?", "Who can see whose files?")):
        c.box(420, 165 + i * 60, 330, 46, PANEL, AMBER, show(t, 3.6 + i * 0.7))
        c.text(585, 188 + i * 60, s, 21, TEXT, show(t, 3.6 + i * 0.7), anchor="mm")


def scene_booking(c, t):
    c.heading("Step 1: an agent books", t)
    c.box(60, 130, 690, 300, (10, 16, 22), LINE, show(t, 0.2))
    code = ['POST /api/bookings', '{', '  "hours": 2,', '  "gpus": 1,', '  "purpose": "train model",',
            '  "ssh_public_key": "ssh-ed25519 ..."', '}']
    total = sum(map(len, code))
    shown = int(total * ease((t - 0.6) / 3.2))
    for i, line in enumerate(code):
        part = line[:max(0, shown)]
        shown -= len(line)
        c.text(90, 160 + i * 36, part, 23, GREEN if i == 0 else TEXT, 1, "mono")
    c.agent(120, 510, BLUE, "agent")
    c.arrow(170, 510, 420, 510, BLUE, 1, ease((t - 3.9) / 0.6))
    c.text(300, 470, "one simple request", 17, MUTED, show(t, 3.9), anchor="ma")
    steps = (("queued", AMBER, 4.6), ("starting", BLUE, 5.7), ("ready", GREEN, 6.8))
    for i, (s, col, at) in enumerate(steps):
        c.pill(810, 160 + i * 78, s, col, show(t, 4.3), t >= at, 200)
        if i:
            c.arrow(910, 118 + i * 78, 910, 156 + i * 78, LINE, show(t, 4.3), 1, 3)
    c.box(440, 470, 780, 86, PANEL, GREEN, show(t, 7.2))
    c.text(465, 490, "ssh agent@your-server  with a temporary certificate", 22, TEXT, show(t, 7.3), "mono")
    c.text(465, 525, "The login expires by itself when the booking ends.", 19, GREEN, show(t, 7.7))
    c.caption("The agent asks for hours and GPUs, waits, then gets SSH access.", t, 0.5)


def scene_seats(c, t):
    c.heading("Step 2: fair sharing", t)
    c.box(420, 150, 300, 300, PANEL, LINE, show(t, 0.2))
    c.box(760, 150, 300, 300, PANEL, LINE, show(t, 0.2))
    c.text(570, 170, "Seat A", 22, MUTED, show(t, 0.2), "bold", "ma")
    c.text(910, 170, "Seat B", 22, MUTED, show(t, 0.2), "bold", "ma")
    c.gpu(495, 340, "GPU 1", GREEN, show(t, 0.3))
    c.gpu(835, 340, "GPU 2", GREEN, show(t, 0.3))

    leave = ease((t - 7.4) / 0.9)
    ay = lerp(lerp(600, 260, ease((t - 0.8) / 0.9)), -90, leave)
    by = lerp(lerp(600, 260, ease((t - 1.6) / 0.9)), -90, leave)
    c.agent(570, ay, BLUE, "agent A")
    c.agent(910, by, PURPLE, "agent B")

    enter = ease((t - 8.4) / 1.0)
    cx = lerp(220, 740, enter)
    cy = lerp(lerp(600, 240, ease((t - 3.2) / 0.8)), 250, enter)
    c.agent(cx, cy, AMBER, "agent C", show(t, 3.0))
    c.text(220, 300, "needs 2 GPUs", 19, AMBER, show(t, 3.6) * (1 - enter), anchor="ma")
    dy = lerp(600, 420, ease((t - 4.8) / 0.8))
    dy2 = lerp(dy, 240, ease((t - 8.8) / 0.8))
    c.agent(220, dy2, GREEN, "agent D", show(t, 4.6))
    c.text(220, 480, "needs 1 GPU", 19, GREEN, show(t, 5.2) * (1 - ease((t - 8.8) / 0.4)), anchor="ma")
    if 8.6 < t:
        c.line(647, 382, 833, 382, AMBER, show(t, 9), 5)
        c.text(740, 466, "agent C uses both GPUs", 19, AMBER, show(t, 9), anchor="ma")
    msg = "Two agents can work at the same time, each with its own GPU."
    if t > 3.2:
        msg = "A big booking waits for both GPUs. Later bookings cannot overtake it."
    if t > 8.2:
        msg = "First come, first served, so nobody waits forever."
    c.text(W / 2, H - 56, msg, 25, MUTED, show(t, 0.4), anchor="ma")


def scene_isolation(c, t):
    c.heading("Step 3: isolation", t)
    c.box(90, 210, 300, 230, PANEL, BLUE, show(t, 0.2))
    c.text(240, 232, "Agent VM", 23, TEXT, show(t, 0.3), "bold", "ma")
    c.agent(240, 320, BLUE, "normal user, no root", show(t, 0.4))

    c.box(470, 130, 30, 400, (70, 40, 34), RED, show(t, 0.8), r=8)
    c.text(485, 545, "firewall", 19, RED, show(t, 1), anchor="ma")

    c.box(830, 135, 330, 76, PANEL, GREEN, show(t, 3.8))
    c.text(995, 173, "Internet", 26, GREEN, show(t, 3.8), "bold", "mm")
    c.arrow(392, 225, 824, 173, GREEN, 1, ease((t - 3.9) / 0.9))

    names = ("Proxmox host", "Your other VMs", "Your files (NAS)", "Other agents")
    for i, s in enumerate(names):
        y = 245 + i * 72
        at = 1.2 + i * 0.5
        c.box(830, y, 330, 58, PANEL, LINE, show(t, at))
        c.text(995, y + 29, s, 21, TEXT, show(t, at), anchor="mm")
        c.arrow(392, 235 + i * 60, 462, y + 29, RED, 0.9, ease((t - at - 0.2) / 0.5), 3)
        c.line(505, y + 29, 826, y + 29, LINE, show(t, at) * 0.5, 2)
        c.cross(485, y + 29, 14, show(t, at + 0.7, 0.25))
    c.caption("Each agent VM reaches the internet, but nothing inside your network.", t, 0.5)


def folder(c, x, y, name, col, a=1, w=200):
    c.box(x, y + 14, w, 96, PANEL, col, a, 10)
    c.box(x, y, 84, 30, PANEL, col, a, 8)
    c.text(x + w / 2, y + 74, name, 20, TEXT, a, "mono", "mm")


def file_icon(c, x, y, col, a=1, label=""):
    c.box(x, y, 30, 38, col, col, a, 4)
    if label:
        c.text(x + 15, y + 44, label, 14, MUTED, a, anchor="ma")


def scene_storage(c, t):
    c.heading("Step 4: an own drive and a shared drive", t)
    swapped = t > 6.3
    c.box(70, 150, 340, 340, PANEL, BLUE, show(t, 0.2))
    c.text(240, 170, "Agent VM", 23, TEXT, show(t, 0.2), "bold", "ma")
    c.box(690, 150, 520, 340, PANEL, GREEN, show(t, 0.2))
    c.text(950, 170, "Storage VM", 23, TEXT, show(t, 0.2), "bold", "ma")

    a_col, b_col = (LINE, PURPLE) if swapped else (BLUE, LINE)
    folder(c, 725, 225, "A: my-work", a_col, show(t, 0.5), 220)
    folder(c, 965, 225, "common", GREEN, show(t, 0.7), 220)
    folder(c, 725, 355, "B: my-work", b_col, show(t, 0.9), 460)
    hidden = "hidden: never connected to this VM"
    if swapped:
        c.text(835, 322, "hidden from agent B", 15, RED, show(t, 6.4), anchor="mm")
    else:
        c.text(955, 450, hidden, 16, RED, show(t, 1.0), anchor="mm")

    if t < 5.0 or swapped:
        col = BLUE if t < 5.0 else PURPLE
        c.agent(240, 270, col, "agent A" if t < 5.0 else "agent B", show(t, 0.5) if t < 5 else show(t, 6.3))
    if t < 5.0:
        file_icon(c, 130, 400, AMBER, show(t, 1.1), "temp")
    if t > 2.0:
        p = ease((t - 2.0) / 0.9)
        file_icon(c, lerp(300, 820, p), lerp(400, 240, p), BLUE, 0.35 if swapped else 1)
    if t > 3.0:
        p = ease((t - 3.0) / 0.9)
        file_icon(c, lerp(300, 1060, p), lerp(400, 240, p), GREEN)
    if 5.0 <= t <= 6.3:
        pulse = abs(math.sin((t - 5.0) * 6))
        c.box(90, 215, 300, 250, mix(PANEL, RED, 0.5 * pulse), RED, 1)
        c.text(240, 340, "VM reset", 34, TEXT, 1, "bold", "mm")
    if swapped:
        c.text(1148, 259, "kept", 16, GREEN, show(t, 6.5), anchor="mm")
    msg = "Private work goes to my-work. Large models go to common, for every agent."
    if t > 4.8:
        msg = "After each booking the VM is wiped clean, but the drives stay."
    if t > 6.6:
        msg = "The next agent reuses the models but never sees your private work."
    c.text(W / 2, H - 56, msg, 25, MUTED, show(t, 0.4), anchor="ma")


def scene_power(c, t):
    c.heading("Step 5: power only when needed", t)
    on = 2.2 < t < 6.2
    c.box(430, 170, 420, 260, PANEL, GREEN if on else LINE, show(t, 0.2), width=3)
    c.text(640, 196, "GPU host", 24, TEXT, show(t, 0.2), "bold", "ma")
    c.gpu(460, 250, "GPU 1", GREEN if on else LINE, show(t, 0.3), 170)
    c.gpu(650, 250, "GPU 2", GREEN if on else LINE, show(t, 0.3), 170)
    c.pill(565, 365, "ON" if on else "OFF", GREEN if on else RED, show(t, 0.3), True, 150)
    c.agent(140, 300, BLUE, "agent", show(t, 0.5))
    c.arrow(190, 300, 420, 300, AMBER, 1 if t < 2.4 else 1 - show(t, 2.4), ease((t - 0.9) / 0.9))
    c.text(305, 256, "Wake-on-LAN", 20, AMBER, show(t, 1.0) * (1 - show(t, 2.4)), anchor="ma")
    if on:
        c.box(890, 240, 320, 120, PANEL, GREEN, show(t, 2.4))
        c.text(1050, 278, "Agents are working", 21, GREEN, show(t, 2.5), anchor="ma")
        bar = (t - 2.6) / 3.4
        c.box(920, 322, 260, 12, LINE, LINE, show(t, 2.6), 6)
        if bar > 0:
            c.box(920, 322, 260 * min(1, bar), 12, GREEN, GREEN, 1, 6)
    if t > 6.3:
        c.text(1000, 300, "Powered off again", 22, MUTED, show(t, 6.3), anchor="mm")
    msg = "When a booking arrives and the machine is off, GPUBerth wakes it."
    if t > 2.6:
        msg = "Your own VM has priority: bookings wait for your approval while you use it."
    if t > 6.0:
        msg = "After the last booking the machine returns to how it was found."
    c.text(W / 2, H - 56, msg, 25, MUTED, show(t, 0.4), anchor="ma")


def scene_end(c, t):
    c.text(W / 2, 200, "GPUBerth", 84, TEXT, show(t, 0.2), "bold", "mm")
    items = ("Booking API and fair queue", "Isolated VMs that reset after every booking",
             "Own drive and shared drive", "Open source, Apache-2.0")
    for i, s in enumerate(items):
        c.text(W / 2, 290 + i * 46, s, 27, GREEN if i == 3 else TEXT, show(t, 0.5 + i * 0.35), anchor="mm")
    c.text(W / 2, 520, "github.com/basel5099/GPUBerth", 30, BLUE, show(t, 2.2), "mono", "mm")
    c.text(W / 2, H - 56, "This video was drawn entirely with code.", 21, MUTED, show(t, 2.8), anchor="ma")


SCENES = ((6, scene_title), (8, scene_problem), (10, scene_booking), (11, scene_seats),
          (8, scene_isolation), (10, scene_storage), (9, scene_power), (6, scene_end))
TOTAL = sum(s[0] for s in SCENES)


def frame(t):
    start = 0.0
    for length, draw in SCENES:
        if t < start + length or draw is SCENES[-1][1]:
            local = t - start
            c = Canvas()
            draw(c, local)
            fade = min(1, local / 0.4, (length - local) / 0.4)
            if fade < 1:
                return Image.blend(Image.new("RGB", (W, H), BG), c.img, max(0, fade))
            return c.img
        start += length


def render(out):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    p = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
                          "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                         stdin=subprocess.PIPE)
    for i in range(int(TOTAL * FPS)):
        p.stdin.write(frame(i / FPS).tobytes())
    p.stdin.close()
    p.wait()


if __name__ == "__main__":
    if sys.argv[1] == "--still":
        frame(float(sys.argv[2])).save(sys.argv[3])
    else:
        render(sys.argv[1])
