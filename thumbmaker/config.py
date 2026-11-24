"""Настройки пайплайна."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "model_cache"
OUT_DIR = ROOT / "out"
FONTS_DIR = ROOT / "assets" / "fonts"

for _d in (MODELS_DIR, OUT_DIR, FONTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Готовые размеры холста. YouTube принимает превью до 2 МБ и показывает 1280x720,
# но исходник лучше держать крупнее — из него потом можно резать что угодно.
PRESETS = {
    "1280x720 (HD)": (1280, 720),
    "1920x1080 (Full HD)": (1920, 1080),
    "2560x1440 (2K)": (2560, 1440),
    "3840x2160 (4K)": (3840, 2160),
}


# Шрифты для надписи: название в интерфейсе -> путь («#Начертание» для переменных).
# Все с кириллицей и есть в любой Windows; свой .ttf тоже можно указать путём.
FONTS = {
    "Impact": r"C:\Windows\Fonts\impact.ttf",
    "Bahnschrift узкий": r"C:\Windows\Fonts\bahnschrift.ttf#Bold Condensed",
    "Bahnschrift": r"C:\Windows\Fonts\bahnschrift.ttf#Bold",
    "Arial Black": r"C:\Windows\Fonts\ariblk.ttf",
    "Segoe UI Black": r"C:\Windows\Fonts\seguibl.ttf",
    "Trebuchet жирный": r"C:\Windows\Fonts\trebucbd.ttf",
    "Verdana жирный": r"C:\Windows\Fonts\verdanab.ttf",
}

# Что подложить под текст: название в интерфейсе -> значение ComposeConfig.plate.
PLATES = {
    "Затемнение снизу": "band",
    "Плашка под строками": "box",
    "Рваная полоса": "torn",
    "Пергамент": "parchment",
    "Без подложки": "none",
}


@dataclass
class ScanConfig:
    """Как сканируем стрим в поисках лица."""

    coarse_samples: int = 48       # сколько кадров берём равномерно по всему стриму
    refine_top: int = 4            # вокруг скольких лучших мест делаем плотный до-скан
    refine_radius: float = 4.0     # +/- секунд вокруг найденного пика
    refine_samples: int = 6        # сколько кадров в каждом таком окне
    skip_head: float = 0.04        # пропустить первые 4% (заставки/оффлайн-экран)
    skip_tail: float = 0.03        # и последние 3%
    max_height: int = 1080         # качество кадров, которое тянем из стрима
    workers: int = 6               # параллельных ffmpeg-запросов
    live_window: float = 90.0      # для live: сколько секунд с живого края писать
    live_fps: float = 0.5          # и с какой частотой резать кадры


@dataclass
class FaceConfig:
    """Требования к лицу и вес эмоций."""

    min_face_ratio: float = 0.06   # минимальная доля ширины кадра, которую занимает лицо
    max_yaw: float = 0.45          # 0 — строго в камеру, 1 — профиль
    blur_floor: float = 4.0        # абсолютный минимум (реальный порог — относительный)
    # Вклад блендшейпов в «яркость эмоции». Плюс — экспрессия, минус — штраф.
    weights: dict[str, float] = field(default_factory=lambda: {
        "jawOpen": 1.60,
        "mouthSmileLeft": 1.15, "mouthSmileRight": 1.15,
        "mouthFunnel": 0.70, "mouthPucker": 0.45,
        "mouthStretchLeft": 0.55, "mouthStretchRight": 0.55,
        "mouthUpperUpLeft": 0.40, "mouthUpperUpRight": 0.40,
        "mouthFrownLeft": 0.60, "mouthFrownRight": 0.60,
        "mouthShrugUpper": 0.35, "mouthShrugLower": 0.25,
        "browInnerUp": 0.95,
        "browOuterUpLeft": 0.85, "browOuterUpRight": 0.85,
        "browDownLeft": 0.70, "browDownRight": 0.70,
        "eyeWideLeft": 0.95, "eyeWideRight": 0.95,
        "eyeSquintLeft": 0.35, "eyeSquintRight": 0.35,
        "cheekSquintLeft": 0.30, "cheekSquintRight": 0.30,
        "cheekPuff": 0.45,
        "noseSneerLeft": 0.40, "noseSneerRight": 0.40,
        "eyeBlinkLeft": -1.10, "eyeBlinkRight": -1.10,
        "mouthClose": -0.35,
    })


@dataclass
class ComposeConfig:
    """Как собираем финальную картинку."""

    width: int = 1920              # холст крупнее 720p: мелочи вроде текста не мылятся
    height: int = 1080
    side: str = "right"            # на какой стороне стоит стример: right | left
    face_scale: float = 0.98       # высота вырезки от высоты холста
    face_offset_x: float = 0.0     # сдвиг вырезки, в долях ширины холста
    face_offset_y: float = 0.02    # + вниз (лицо чуть свисает за нижний край)
    layout: str = "classic"        # classic — фон во весь кадр; card — превью ролика
                                   # карточкой с рваным краем слева сверху, за ней размытие
    card_width: float = 0.66       # ширина карточки от ширины холста
    face_height: float = 0.0       # крупный план: высота лица от высоты холста;
                                   # 0 — стример целиком по face_scale
    face_x: float = 0.77           # где центр лица по ширине (для стримера справа)
    face_y: float = 0.17           # где верх лица по высоте
    caption_area: str = "full"     # full — текст во всю ширину, left — слева от стримера
    mirror: bool | str = True      # стример отзеркален; "auto" — только если смотрит
                                   # из кадра, а не в сторону фона
    outline_px: int = 0            # белая обводка вокруг стримера, 0 — без неё
    outline_color: tuple[int, int, int] = (255, 255, 255)
    shadow: bool = True
    boost_saturation: float = 1.12  # лёгкое «сочное» тонирование вырезки
    caption: str = ""              # крупный текст внизу во всю ширину; переносится по
    badge: str = ""                # словам сам, перевод строки в тексте тоже уважается
    font_path: str = ""            # свой .ttf; пусто — Impact, за ним Arial Black
    plate: str = "band"            # под текстом: band — затемнение снизу, box — плашка
                                   # под каждой строкой, torn — чёрная рваная полоса,
                                   # parchment — пергамент, none — ничего
    accent_color: tuple[int, int, int] = (0, 214, 255)          # жёлтый (BGR) для *слов*
    parchment_seed: int = 7        # другой seed — другой рисунок пятен и рваного края
    parchment_text_color: tuple[int, int, int] = (30, 40, 62)   # тёмно-коричневый (BGR)
    caption_color: tuple[int, int, int] = (255, 255, 255)       # текст без пергамента
    caption_stroke: tuple[int, int, int] = (0, 0, 0)


@dataclass
class Settings:
    scan: ScanConfig = field(default_factory=ScanConfig)
    face: FaceConfig = field(default_factory=FaceConfig)
    compose: ComposeConfig = field(default_factory=ComposeConfig)
