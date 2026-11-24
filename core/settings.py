from __future__ import annotations

from dataclasses import dataclass, fields, replace

from core.timecode import parse_time

LAYOUTS = ("auto", "split", "cam", "screen")

OUT_W = 1080
OUT_H = 1920
TOP_HEIGHT = 760
SEAM_SUBS_LIFT = 0.75
BASE_COLOR = "#FFFFFF"
HIGHLIGHT_COLOR = "#FFE500"
KEYWORD_COLOR = "#55FF55"
TITLE_COLOR = "#FFFFFF"
SUBS_FONT = "Arial Black"
TITLE_SIZE = 64
PAUSE_MIN = 0.45


def _box(value):
    if value is None:
        return None
    parts = value.split(",") if isinstance(value, str) else list(value)
    if len(parts) != 4:
        raise ValueError("рамка задаётся четырьмя числами x,y,w,h")
    return tuple(int(round(float(part))) for part in parts)


def _spans(value):
    if not value:
        return ()
    if isinstance(value, str):
        pairs = [part.split("-") for part in value.split(",") if part.strip()]
        return tuple((float(a), float(b)) for a, b in pairs)
    return tuple((float(a), float(b)) for a, b in value)


def _text(value):
    value = "" if value is None else str(value).strip()
    return value or None


@dataclass
class Render:
    source: str = ""
    out: str | None = None
    ranges: tuple = ()
    timecodes: str | None = None
    select: str | None = None
    brief: str | None = None

    layout: str = "split"
    top_height: int = TOP_HEIGHT
    cam_box: tuple | None = None
    cam_fit: bool = True
    game_box: tuple | None = None
    game_fit: bool = True

    model: str | None = None
    language: str | None = None

    subs: bool = True
    subs_x: int = OUT_W // 2
    subs_y: int | None = None
    subs_moved: bool = False
    font: str = SUBS_FONT
    color_base: str = BASE_COLOR
    color_highlight: str = HIGHLIGHT_COLOR

    title: str | None = None
    title_size: int = TITLE_SIZE
    title_color: str = TITLE_COLOR

    censor: bool = True
    blur_bg: bool = False
    drop: tuple = ()
    cut_pauses: bool = False
    pause_min: float = PAUSE_MIN

    def __post_init__(self):
        if self.layout not in LAYOUTS:
            raise ValueError("неизвестная раскладка: %s" % self.layout)
        self.cam_box = _box(self.cam_box)
        self.game_box = _box(self.game_box)
        self.ranges = _spans(self.ranges)
        self.drop = _spans(self.drop)
        for name in ("out", "timecodes", "select", "brief", "model", "language",
                     "title"):
            setattr(self, name, _text(getattr(self, name)))
        self.subs_x = int(self.subs_x)
        self.top_height = int(self.top_height)
        self.title_size = int(self.title_size)
        self.pause_min = float(self.pause_min)
        if self.subs_y is not None:
            self.subs_y = int(self.subs_y)
        told = [n for n in ("ranges", "timecodes", "select") if getattr(self, n)]
        if len(told) > 1:
            raise ValueError("что резать, сказано дважды: %s" % ", ".join(told))

    @property
    def decides_itself(self):
        return self.layout == "auto"

    def effective(self):
        me = replace(self)
        if me.decides_itself:
            me.cam_box = None
            me.cam_fit = True
            if not me.subs_moved:
                me.subs_y = None
        if me.title is None:
            me.title_size = TITLE_SIZE
            me.title_color = TITLE_COLOR
        if not me.cut_pauses:
            me.pause_min = PAUSE_MIN
        me.subs_moved = me.subs_y is not None
        return me

    def flags(self, with_out=True):
        me = self.effective()
        out = []
        if me.timecodes:
            out += ["--timecodes", me.timecodes]
        elif me.select:
            out += ["--select", me.select]
            if me.brief:
                out += ["--brief", me.brief]
        elif len(me.ranges) > 1:
            out += ["--ranges", ",".join("%.3f-%.3f" % (a, b) for a, b in me.ranges)]
        if with_out and me.out:
            out += ["--out", me.out]
        out += ["--layout", me.layout,
                "--top-height", str(me.top_height),
                "--subs-x", str(me.subs_x),
                "--font", me.font,
                "--color-base", me.color_base,
                "--color-highlight", me.color_highlight]
        if me.game_box:
            out += ["--game-box", ",".join(str(v) for v in me.game_box)]
        if not me.game_fit:
            out.append("--game-exact")
        if me.cam_box:
            out += ["--cam-box", ",".join(str(v) for v in me.cam_box)]
        if not me.cam_fit:
            out.append("--cam-exact")
        if me.subs_y is not None:
            out += ["--subs-y", str(me.subs_y)]
        if me.model:
            out += ["--model", me.model]
        if me.language:
            out += ["--language", me.language]
        if me.title:
            out += ["--title", me.title,
                    "--title-size", str(me.title_size),
                    "--title-color", me.title_color]
        if me.blur_bg:
            out.append("--blur-bg")
        if not me.subs:
            out.append("--no-subs")
        if not me.censor:
            out.append("--no-censor")
        if me.drop:
            out += ["--drop", ",".join("%.3f-%.3f" % (a, b) for a, b in me.drop)]
        if me.cut_pauses:
            out += ["--cut-pauses", "--pause-min", "%.2f" % me.pause_min]
        return out

    def command(self, engine):
        me = self.effective()
        head = list(engine) + [me.source]
        if len(me.ranges) == 1 and me.out:
            head += ["%.3f" % me.ranges[0][0], "%.3f" % me.ranges[0][1], me.out]
            return head + me.flags(with_out=False)
        return head + me.flags()


def from_args(args):
    def got(name, fallback=None):
        return getattr(args, name, fallback)

    ranges = _spans(got("ranges"))
    out = got("out")
    if not ranges:
        start = parse_time(got("start_time", got("start")))
        end = parse_time(got("end_time", got("end")))
        if start is not None and end is not None:
            ranges = ((start, end),)
            out = out or got("output")
    return Render(
        source=got("input", got("source")) or "",
        out=out,
        ranges=ranges,
        timecodes=got("timecodes"),
        select=got("select"),
        brief=got("brief"),
        layout=got("layout") or "split",
        top_height=got("top_height", TOP_HEIGHT),
        cam_box=got("cam_box"),
        cam_fit=not got("cam_exact", False),
        game_box=got("game_box"),
        game_fit=not got("game_exact", False),
        model=got("model"),
        language=got("language"),
        subs=not got("no_subs", False),
        subs_x=got("subs_x", OUT_W // 2),
        subs_y=got("subs_y"),
        subs_moved=got("subs_y") is not None,
        font=got("font", SUBS_FONT),
        color_base=got("color_base", BASE_COLOR),
        color_highlight=got("color_highlight", HIGHLIGHT_COLOR),
        title=got("title"),
        title_size=got("title_size", TITLE_SIZE),
        title_color=got("title_color", TITLE_COLOR),
        censor=not got("no_censor", False),
        blur_bg=got("blur_bg", False),
        drop=got("drop") or (),
        cut_pauses=got("cut_pauses", False),
        pause_min=got("pause_min", PAUSE_MIN),
    ).effective()


def names():
    return tuple(f.name for f in fields(Render))
