"""Сборка финального превью: фон ролика + вырезанный стример + подписи."""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import FONTS_DIR, ComposeConfig

BUNDLED_FONT = FONTS_DIR / "Unsightly.ttf"

# Узкий жирный гротеск читается на превью лучше всего: крупно и влезает много букв.
FONT_FALLBACKS = [
    r"C:\Windows\Fonts\impact.ttf",
    r"C:\Windows\Fonts\ariblk.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    BUNDLED_FONT,
]


def load_font(size: int, path=None) -> ImageFont.FreeTypeFont:
    """Шрифт по пути; «путь#Начертание» — начертание переменного шрифта (Bold Condensed)."""
    for candidate in [path, *FONT_FALLBACKS]:
        file, _, variation = str(candidate or "").partition("#")
        if file and Path(file).exists():
            try:
                font = ImageFont.truetype(file, size)
                if variation:
                    font.set_variation_by_name(variation)
                return font
            except Exception:
                continue
    return ImageFont.load_default(size)


# --------------------------------------------------------------------- картинки


def cover_resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Масштабирует с обрезкой по центру — как background-size: cover."""
    ih, iw = img.shape[:2]
    scale = max(w / iw, h / ih)
    nw, nh = int(round(iw * scale)), int(round(ih * scale))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return r[y0:y0 + h, x0:x0 + w]


def fit_height(rgba: np.ndarray, target_h: int, max_w: int) -> np.ndarray:
    h, w = rgba.shape[:2]
    scale = target_h / h
    if w * scale > max_w:
        scale = max_w / w
    nw, nh = max(int(w * scale), 1), max(int(h * scale), 1)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(rgba, (nw, nh), interpolation=interp)


def boost_saturation(bgr: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1.0) < 1e-3:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def alpha_paste(dst: np.ndarray, rgba: np.ndarray, x: int, y: int) -> None:
    """Накладывает RGBA поверх BGR-холста по месту (x, y), обрезая по краям."""
    H, W = dst.shape[:2]
    h, w = rgba.shape[:2]
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(x, 0), max(y, 0)
    dx1, dy1 = min(x + w, W), min(y + h, H)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    fg = rgba[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    a = (fg[..., 3:4].astype(np.float32) / 255.0)
    roi = dst[dy0:dy1, dx0:dx1].astype(np.float32)
    dst[dy0:dy1, dx0:dx1] = (fg[..., :3] * a + roi * (1 - a)).astype(np.uint8)


def side_gradient(canvas: np.ndarray, side: str, strength: float = 0.35) -> np.ndarray:
    """Притемняет ту половину, где стоит стример — так вырезка читается лучше."""
    W = canvas.shape[1]
    ramp = np.linspace(0, 1, W, dtype=np.float32) ** 1.6
    if side == "left":
        ramp = ramp[::-1]
    mult = (1 - strength * ramp)[None, :, None]
    return np.clip(canvas.astype(np.float32) * mult, 0, 255).astype(np.uint8)


def _outline_layer(alpha: np.ndarray, px: int, color) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px * 2 + 1, px * 2 + 1))
    grown = cv2.dilate(alpha, k)
    layer = np.zeros((*alpha.shape, 4), np.uint8)
    layer[..., 0], layer[..., 1], layer[..., 2] = color[2], color[1], color[0]
    layer[..., 3] = grown
    return layer


def _shadow_layer(alpha: np.ndarray, px: int) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px * 2 + 1, px * 2 + 1))
    grown = cv2.dilate(alpha, k)
    blur = cv2.GaussianBlur(grown, (0, 0), px * 1.6)
    layer = np.zeros((*alpha.shape, 4), np.uint8)
    layer[..., 3] = (blur.astype(np.float32) * 0.55).astype(np.uint8)
    return layer


# -------------------------------------------------------------------- пергамент


def parchment_texture(w: int, h: int, seed: int = 7) -> np.ndarray:
    """Процедурный старый пергамент: пятнистая охра с рваным подпалённым краем."""
    w, h = max(int(w), 8), max(int(h), 8)
    rng = np.random.default_rng(seed)

    def noise(cells_y, cells_x, interp=cv2.INTER_CUBIC):
        small = rng.random((max(int(cells_y), 2), max(int(cells_x), 2))).astype(np.float32)
        return cv2.resize(small, (w, h), interpolation=interp)

    # пятнистость: крупные разводы, мелкое зерно и продольное волокно бумаги
    mottle = (0.55 * noise(h / 22, w / 22)
              + 0.30 * noise(h / 6, w / 6, cv2.INTER_LINEAR)
              + 0.15 * noise(h / 3, w / 40, cv2.INTER_LINEAR))
    mottle = cv2.GaussianBlur(mottle, (0, 0), max(w, h) * 0.003)
    mottle = cv2.normalize(mottle, None, 0.10, 1.0, cv2.NORM_MINMAX)

    dark = np.array([88, 128, 162], np.float32)     # тёплая охра (BGR)
    light = np.array([182, 212, 232], np.float32)   # выцветший крем
    img = dark + (light - dark) * mottle[..., None]

    # редкие бурые разводы, будто от воды
    stains = np.zeros((h, w), np.float32)
    for _ in range(int(rng.integers(4, 8))):
        cx, cy = int(rng.integers(0, w)), int(rng.integers(0, h))
        rx = int(rng.integers(w // 12 + 2, w // 4 + 3))
        ry = int(rng.integers(h // 8 + 2, h // 2 + 3))
        cv2.ellipse(stains, (cx, cy), (rx, ry), float(rng.integers(0, 180)), 0, 360,
                    float(rng.uniform(0.3, 0.9)), -1)
    stains = cv2.GaussianBlur(stains, (0, 0), max(w, h) * 0.02)
    img *= (1 - 0.22 * stains)[..., None]

    # рваный край: границу «съедает» шум, у самого края бумага подпалена
    margin = max(min(w, h) * 0.055, 3.0)
    dx = np.minimum(np.arange(w), w - 1 - np.arange(w)).astype(np.float32)[None, :]
    dy = np.minimum(np.arange(h), h - 1 - np.arange(h)).astype(np.float32)[:, None]
    dist = np.minimum(np.broadcast_to(dx, (h, w)), np.broadcast_to(dy, (h, w)))
    ragged = margin * (0.35 + 1.15 * noise(6, 12))
    alpha = np.clip((dist - ragged) / max(margin * 0.45, 1.0), 0, 1)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.2)

    burn = np.clip(dist / (margin * 2.8), 0, 1) ** 0.8
    img *= (0.42 + 0.58 * burn)[..., None]

    return np.dstack([np.clip(img, 0, 255).astype(np.uint8),
                      (alpha * 255).astype(np.uint8)])


def torn_alpha(w: int, h: int, seed: int = 7, margin: float | None = None,
               sides: str = "tblr") -> np.ndarray:
    """Маска 0..1 с рваным краем по сторонам sides (t — верх, b — низ, l, r)."""
    w, h = max(int(w), 8), max(int(h), 8)
    rng = np.random.default_rng(seed)
    margin = margin or max(min(w, h) * 0.035, 3.0)
    far = float(w + h)
    dx_l = np.arange(w, dtype=np.float32) if "l" in sides else np.full(w, far, np.float32)
    dx_r = (w - 1 - np.arange(w, dtype=np.float32)) if "r" in sides else np.full(w, far, np.float32)
    dy_t = np.arange(h, dtype=np.float32) if "t" in sides else np.full(h, far, np.float32)
    dy_b = (h - 1 - np.arange(h, dtype=np.float32)) if "b" in sides else np.full(h, far, np.float32)
    dist = np.minimum(np.minimum(dx_l, dx_r)[None, :], np.minimum(dy_t, dy_b)[:, None])
    # крупные зубцы и мелкая бахрома, как у рваной бумаги
    coarse = cv2.resize(rng.random((8, 24)).astype(np.float32), (w, h),
                        interpolation=cv2.INTER_CUBIC)
    fine = cv2.resize(rng.random((max(h // 6, 2), max(w // 6, 2))).astype(np.float32),
                      (w, h), interpolation=cv2.INTER_LINEAR)
    ragged = margin * (0.2 + 0.9 * coarse + 0.5 * fine)
    alpha = np.clip((dist - ragged) / max(margin * 0.25, 1.0), 0, 1)
    return cv2.GaussianBlur(alpha, (0, 0), 0.8)


def card_canvas(preview_bgr: np.ndarray, cfg: ComposeConfig, backdrop=None):
    """Фон «карточкой»: размытый тёмный задник, сверху слева — превью с рваным краем.

    Возвращает холст и нижнюю границу карточки.
    """
    W, H = cfg.width, cfg.height
    k = H / 720.0
    back = cover_resize(backdrop if backdrop is not None else preview_bgr, W, H)
    back = cv2.GaussianBlur(back, (0, 0), 22 * k)
    canvas = (back.astype(np.float32) * 0.5).astype(np.uint8)

    cw = int(W * cfg.card_width)
    ch = int(cw * 9 / 16)
    card = cover_resize(preview_bgr, cw, ch)
    alpha = torn_alpha(cw, ch, cfg.parchment_seed, margin=14 * k)
    sheet = np.dstack([card, (alpha * 255).astype(np.uint8)])
    x, y = int(-4 * k), int(-4 * k)
    shadow = np.zeros_like(sheet)
    shadow[..., 3] = (cv2.GaussianBlur(sheet[..., 3], (0, 0), 10 * k) * 0.8).astype(np.uint8)
    alpha_paste(canvas, shadow, x + int(6 * k), y + int(8 * k))
    alpha_paste(canvas, sheet, x, y)
    return canvas, y + ch


# ------------------------------------------------------------------------ текст


DASHES = ("—", "–", "-")


def split_words(text: str) -> list:
    """Текст -> абзацы из слов [(слово, акцент)]. *Слова в звёздочках* — акцентные."""
    paragraphs, accent = [], False
    for para in text.splitlines() or [""]:
        words = []
        for raw in para.split():
            # «*слово*?» — звёздочка закрывает акцент и перед знаком препинания
            closing = re.match(r"^(.+?)\*([^\w\s*]*)$", raw)
            starts, ends = raw.startswith("*"), bool(closing) and len(raw) > 1
            word = (closing.group(1) + closing.group(2) if closing else raw).strip("*")
            if starts:
                accent = True
            if word:
                words.append((word, accent))
            if ends:
                accent = False
        paragraphs.append(words)
    return paragraphs


def wrap_words(draw: ImageDraw.ImageDraw, paragraphs: list, font, max_w: float) -> list:
    """Переносит слова по ширине; явные переводы строки сохраняются."""
    space = draw.textlength(" ", font=font)
    lines = []
    for words in paragraphs:
        cur, width = [], 0.0
        for word, accent in words:
            w = draw.textlength(word, font=font)
            if cur and width + space + w > max_w:
                # тире не начинает строку, а предлог или союз («в», «и», «на») не висит
                # в конце строки: уносим их вместе с соседним словом
                carry = []
                if word in DASHES and len(cur) > 1:
                    carry.insert(0, cur.pop())
                while len(cur) > 1 and len(cur[-1][0]) <= 2 and cur[-1][0] not in DASHES:
                    carry.insert(0, cur.pop())
                lines.append(cur)
                cur = carry
                width = draw.textlength(" ".join(w for w, _ in carry), font=font)
            width += (space if cur else 0.0) + w
            cur.append((word, accent))
        lines.append(cur)
    return lines


def line_width(draw: ImageDraw.ImageDraw, line: list, font) -> float:
    return draw.textlength(" ".join(word for word, _ in line), font=font)


def wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list:
    """Переносит текст по словам; явные переводы строки сохраняются."""
    return [" ".join(word for word, _ in line)
            for line in wrap_words(draw, split_words(text), font, max_w)]


def _layout_caption(text: str, font_path, max_w: float, max_h: float, start_size: int):
    """Подбирает кегль так, чтобы блок строк влез в отведённое место."""
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    paragraphs = split_words(text)
    size = max(int(start_size), 12)
    while True:
        font = load_font(size, font_path)
        lines = wrap_words(probe, paragraphs, font, max_w)
        widths = [line_width(probe, ln, font) for ln in lines] or [0.0]
        line_h = size * 1.08
        if size <= 12 or (max(widths) <= max_w and line_h * len(lines) <= max_h):
            return font, lines, line_h, max(widths)
        size = max(int(size * 0.93), 12)


def _band(canvas: np.ndarray, top: float, strength: float = 0.82) -> np.ndarray:
    """Затемнение от top до низа кадра: сверху прозрачно, к низу почти чёрное."""
    H = canvas.shape[0]
    top = int(max(min(top, H - 2), 0))
    ramp = np.linspace(0, 1, H - top, dtype=np.float32) ** 0.7 * strength
    out = canvas.astype(np.float32)
    out[top:] *= (1 - ramp)[:, None, None]
    return out.astype(np.uint8)


def draw_caption(canvas: np.ndarray, cfg: ComposeConfig, x0: int = 0, x1: int | None = None,
                 top: float | None = None) -> np.ndarray:
    """Пишет текст капсом внизу кадра, поверх стримера.

    x0..x1 — полоса по ширине под текст (по умолчанию весь кадр), top — выше этой
    линии текст не поднимается (например, под карточкой превью).
    """
    if not cfg.caption and not cfg.badge:
        return canvas

    H, W = canvas.shape[:2]
    k = H / 720.0                     # оформление тянется за разрешением холста

    if cfg.caption:
        plate = cfg.plate
        pad_x, pad_y = int(30 * k), int(14 * k)
        x1 = W if x1 is None else x1
        margin = W * 0.035
        max_w = x1 - x0 - 2 * margin - (2 * pad_x if plate in ("box", "parchment") else 0)
        max_h = H * 0.42 if top is None else min(H * 0.42, H * 0.94 - top)
        font, lines, line_h, block_w = _layout_caption(
            cfg.caption.upper(), cfg.font_path, max_w, max_h, int(H * 0.17))

        if plate == "parchment":
            color, stroke_color = cfg.parchment_text_color, (250, 244, 230)
            stroke_w = max(int(1.5 * k), 1)
        else:
            color, stroke_color = cfg.caption_color, cfg.caption_stroke
            stroke_w = max(int(font.size / 13), 2)

        # Реальные границы букв: надстрочные знаки (Й, Ё) выходят за асцендер шрифта.
        probe = ImageDraw.Draw(Image.new("L", (1, 1)))
        boxes = [probe.textbbox((0, i * line_h),
                                " ".join(word for word, _ in ln) or " ", font=font,
                                anchor="la", stroke_width=stroke_w)
                 for i, ln in enumerate(lines)]
        ink_top = min(b[1] for b in boxes)
        ink_bottom = max(b[3] for b in boxes)
        block_h = ink_bottom - ink_top

        bottom = H * 0.955
        gap = line_h - font.size        # межстрочный зазор, им же отбиваем плашки
        text_y = bottom - (pad_y if plate in ("box", "parchment") else 0) - block_h - ink_top
        block_x = x0 + (x1 - x0 - block_w) / 2

        if plate == "band":
            canvas = _band(canvas, text_y + ink_top - H * 0.16)
        elif plate == "torn":
            sx1 = int(min(block_x + block_w + 2.2 * pad_x, W))
            sy0 = int(max(text_y + ink_top - 2.2 * pad_y, 0))
            strip = np.zeros((H - sy0, sx1, 4), np.uint8)
            strip[..., :3] = 10
            strip[..., 3] = (torn_alpha(sx1, H - sy0, cfg.parchment_seed, margin=12 * k,
                                        sides="tr") * 245).astype(np.uint8)
            alpha_paste(canvas, strip, 0, sy0)
        elif plate == "parchment":
            pw, ph = int(block_w + 2 * pad_x), int(block_h + 2 * pad_y)
            px, py = int(block_x - pad_x), int(text_y + ink_top - pad_y)
            sheet = parchment_texture(pw, ph, cfg.parchment_seed)
            shadow = np.zeros_like(sheet)
            shadow[..., 3] = (cv2.GaussianBlur(sheet[..., 3], (0, 0), 6 * k) * 0.5).astype(np.uint8)
            alpha_paste(canvas, shadow, px + int(5 * k), py + int(7 * k))
            alpha_paste(canvas, sheet, px, py)

        pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)).convert("RGBA")
        under = Image.new("RGBA", pil.size, (0, 0, 0, 0))
        top_layer = Image.new("RGBA", pil.size, (0, 0, 0, 0))
        du, dt = ImageDraw.Draw(under), ImageDraw.Draw(top_layer)
        space = dt.textlength(" ", font=font)
        rgb = lambda bgr: (bgr[2], bgr[1], bgr[0])
        for i, line in enumerate(lines):
            lw = line_width(dt, line, font)
            x = block_x + (block_w - lw) / 2
            y = text_y + i * line_h
            if plate == "box" and line:
                box = boxes[i]
                du.rectangle([x - pad_x * 0.6, text_y + box[1] - pad_y + int(gap * 0.2),
                              x + lw + pad_x * 0.6, text_y + box[3] + pad_y - int(gap * 0.2)],
                             fill=(14, 14, 14, 225))
            for word, accent in line:
                fill = rgb(cfg.accent_color if accent else color)
                if plate in ("band", "none", "torn"):   # мягкая тень — объём
                    du.text((x + 4 * k, y + 5 * k), word, font=font, fill=(0, 0, 0, 170),
                            stroke_width=stroke_w, stroke_fill=(0, 0, 0, 170), anchor="la")
                dt.text((x, y), word, font=font, fill=fill + (255,),
                        stroke_width=stroke_w, stroke_fill=rgb(stroke_color) + (255,),
                        anchor="la")
                x += dt.textlength(word, font=font) + space
        if plate != "box":
            under = under.filter(ImageFilter.GaussianBlur(3 * k))
        pil = Image.alpha_composite(Image.alpha_composite(pil, under), top_layer)
        canvas = cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)

    if cfg.badge:
        pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        font = load_font(int(H * 0.13), cfg.font_path)
        pad = int(font.size * 0.22)
        box = d.textbbox((0, 0), cfg.badge, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        bw = tw + pad * 2
        bx = int(W * 0.025) if cfg.side == "right" else int(W - bw - W * 0.025)
        by = int(H * 0.03)
        d.rectangle([bx, by, bx + bw, by + th + pad * 2], fill=(12, 12, 12))
        d.text((bx + pad - box[0], by + pad - box[1]), cfg.badge, font=font,
               fill=(255, 255, 255))
        canvas = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    return canvas


# ----------------------------------------------------------------------- сборка


def should_mirror(cfg: ComposeConfig, turn: float = 0.0) -> bool:
    """Зеркалить ли стримера: всегда, никогда или «auto» — если он смотрит из кадра."""
    if cfg.mirror == "auto":
        return turn > 0.08 if cfg.side == "right" else turn < -0.08
    return bool(cfg.mirror)


def compose(preview_bgr: np.ndarray, cutout_rgba: np.ndarray | None,
            cfg: ComposeConfig | None = None, face_box=None, turn: float = 0.0,
            backdrop=None) -> np.ndarray:
    """Собирает превью.

    face_box — место лица в вырезке (x, y, w, h): с ним и cfg.face_height стример
    ставится крупным планом по лицу. turn — поворот головы (для mirror="auto").
    backdrop — что размыть под карточкой в cfg.layout="card" (по умолчанию само превью).
    cutout_rgba=None — превью без стримера.
    """
    cfg = cfg or ComposeConfig()
    W, H = cfg.width, cfg.height
    k = H / 720.0

    card_bottom = None
    if cfg.layout == "card":
        canvas, card_bottom = card_canvas(preview_bgr, cfg, backdrop)
    else:
        canvas = cover_resize(preview_bgr, W, H)
        canvas = side_gradient(canvas, cfg.side)

    if cutout_rgba is None:          # превью без стримера: только фон и текст
        x0, x1 = 0, W
        if cfg.layout == "card":
            x1 = int(W * cfg.card_width)
        return draw_caption(canvas, cfg, x0, x1, card_bottom)

    if should_mirror(cfg, turn):
        cutout_rgba = np.ascontiguousarray(cutout_rgba[:, ::-1])
        if face_box is not None:
            fx, fy, fw, fh = face_box
            face_box = (cutout_rgba.shape[1] - fx - fw, fy, fw, fh)

    if face_box is not None and cfg.face_height > 0:
        fx, fy, fw, fh = face_box
        scale = cfg.face_height * H / max(fh, 1)
        ch, cw = cutout_rgba.shape[:2]
        size = (max(int(cw * scale), 1), max(int(ch * scale), 1))
        person = cv2.resize(cutout_rgba, size, interpolation=cv2.INTER_AREA if scale < 1
                            else cv2.INTER_LANCZOS4)
        if scale > 1.5:              # сильное увеличение мылит — чуть подтягиваем резкость
            soft = cv2.GaussianBlur(person[..., :3], (0, 0), 1.2 * scale / 2)
            person[..., :3] = cv2.addWeighted(person[..., :3], 1.6, soft, -0.6, 0)
        center = cfg.face_x if cfg.side == "right" else 1 - cfg.face_x
        x = int(center * W - (fx + fw / 2) * scale + cfg.face_offset_x * W)
        y = int(cfg.face_y * H - fy * scale)
        y = max(y, H - person.shape[0])   # плечи всегда до нижнего края
    else:
        person = fit_height(cutout_rgba, int(H * cfg.face_scale), int(W * 0.66))
        ph, pw = person.shape[:2]
        if cfg.side == "left":
            x = int(-pw * 0.03 + cfg.face_offset_x * W)
        else:
            x = int(W - pw * 0.97 + cfg.face_offset_x * W)
        y = int(H - ph + cfg.face_offset_y * H)
    person = np.dstack([boost_saturation(person[..., :3], cfg.boost_saturation),
                        person[..., 3]])
    pw = person.shape[1]

    outline = int(round(cfg.outline_px * k))
    if cfg.shadow:
        alpha_paste(canvas, _shadow_layer(person[..., 3], max(outline, int(6 * k))),
                    x + int(6 * k), y + int(6 * k))
    if outline > 0:
        alpha_paste(canvas, _outline_layer(person[..., 3], outline, cfg.outline_color), x, y)
    alpha_paste(canvas, person, x, y)

    # в стиле «карточкой» или со стримером сбоку текст идёт слева от лица
    x0, x1 = 0, W
    if cfg.caption_area == "left" or cfg.layout == "card":
        if cfg.side == "right":
            x1 = int(W * cfg.card_width)
        else:
            x0 = W - int(W * cfg.card_width)
    return draw_caption(canvas, cfg, x0, x1, card_bottom)
