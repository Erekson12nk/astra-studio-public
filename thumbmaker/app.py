"""Веб-интерфейс: вставляешь две ссылки — получаешь готовое превью."""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cv2
import gradio as gr

from thumbmaker.config import PLATES, PRESETS, Settings
from thumbmaker import pipeline, sources


PHOTO = "Фото стримера"
STREAM = "Ссылка на стрим"


def _rgb(bgr):
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _bgr(rgb):
    """Gradio отдаёт картинку в RGB, дальше по коду везде BGR."""
    if rgb is None:
        return None
    if rgb.ndim == 3 and rgb.shape[2] == 4:
        return cv2.cvtColor(rgb, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _settings(samples, min_face, max_yaw, side, scale, off_x, off_y,
              outline, shadow, caption, badge, res, plate, seed, mirror) -> Settings:
    s = Settings()
    s.scan.coarse_samples = int(samples)
    s.face.min_face_ratio = float(min_face)
    s.face.max_yaw = float(max_yaw)
    c = s.compose
    c.side = side
    c.face_scale = float(scale)
    c.face_offset_x = float(off_x)
    c.face_offset_y = float(off_y)
    c.outline_px = int(outline)
    c.shadow = bool(shadow)
    c.caption = caption or ""
    c.badge = badge or ""
    c.width, c.height = PRESETS.get(res, (1920, 1080))
    c.plate = PLATES.get(plate, "band")
    c.mirror = bool(mirror)
    c.parchment_seed = int(seed)
    return s


def switch_source(source):
    """Показывает нужное поле: загрузку фото или строку со ссылкой на стрим."""
    is_photo = source == PHOTO
    return gr.update(visible=is_photo), gr.update(visible=not is_photo)


def run(video_url, source, photo, stream_url, samples, top_k, min_face, max_yaw,
        side, scale, off_x, off_y, outline, shadow, caption, badge,
        res_preset, plate, seed, mirror, progress=gr.Progress()):
    if not video_url:
        raise gr.Error("Нужна ссылка на ролик — с неё берётся превью-фон.")
    use_photo = source == PHOTO
    if use_photo and photo is None:
        raise gr.Error("Загрузите фото стримера.")
    if not use_photo and not stream_url:
        raise gr.Error("Вставьте ссылку на стрим.")

    s = _settings(samples, min_face, max_yaw, side, scale, off_x, off_y,
                  outline, shadow, caption, badge, res_preset, plate, seed, mirror)

    def report(stage, done=0, total=0):
        progress((done / total) if total else 0, desc=stage)

    try:
        res = pipeline.build(video_url, None if use_photo else stream_url, s,
                             top_k=int(top_k),
                             photo=_bgr(photo) if use_photo else None,
                             progress=report)
    except sources.SourceError as exc:
        raise gr.Error(str(exc))

    path = pipeline.save(res.thumbnail)
    best = res.candidates[0].hit
    if use_photo:
        gallery = [(_rgb(res.candidates[0].thumb), f"фото · {best.label}")]
        info = f"**{res.title or 'без названия'}**  \nЛицо с фото: {best.label}"
    else:
        gallery = [(_rgb(c.thumb), f"#{i} · {c.timecode()} · {c.hit.label} · {c.hit.score:.0f}")
                   for i, c in enumerate(res.candidates, 1)]
        info = (f"**{res.title or 'без названия'}**  \n"
                f"Кандидатов: {len(res.candidates)} · лучший: "
                f"{best.label} ({best.score:.0f}) на {res.candidates[0].timecode()}")
    return _rgb(res.thumbnail), gallery, info, str(path), res, 0


def recompose(state, index, samples, top_k, min_face, max_yaw, side, scale,
              off_x, off_y, outline, shadow, caption, badge,
              res_preset, plate, seed, mirror):
    if state is None:
        raise gr.Error("Сначала соберите превью.")
    s = _settings(samples, min_face, max_yaw, side, scale, off_x, off_y,
                  outline, shadow, caption, badge, res_preset, plate, seed, mirror)
    idx = min(max(int(index or 0), 0), len(state.candidates) - 1)
    img = pipeline.render(state.preview, state.candidates[idx].hit, s)
    return _rgb(img), str(pipeline.save(img))


def on_select(evt: gr.SelectData):
    return evt.index


CSS = ".gallery-item img {object-fit: cover}"

with gr.Blocks(title="Thumb Maker") as demo:
    gr.Markdown("## Thumb Maker\nПревью из ролика + лицо стримера: с загруженного фото "
                "или с яркой эмоцией, найденной в стриме.")

    video_url = gr.Textbox(label="Ссылка на ролик (берём превью)",
                           placeholder="https://www.youtube.com/watch?v=...")

    source = gr.Radio([PHOTO, STREAM], value=PHOTO, label="Откуда берём лицо")
    photo = gr.Image(label="Фото стримера", type="numpy", sources=["upload", "clipboard"],
                     height=220)
    stream_url = gr.Textbox(label="Ссылка на стрим (режем лицо)", visible=False,
                            placeholder="https://www.youtube.com/watch?v=... / twitch")

    with gr.Row():
        caption = gr.Textbox(label="Текст на превью", placeholder="HOW TO FISH",
                             lines=2, max_lines=4, scale=2,
                             info="Пишется капсом во всю ширину и переносится сам; "
                                  "Shift+Enter — перенос вручную, *слово* — жёлтым")
        badge = gr.Textbox(label="Бейдж", placeholder="#2", scale=1)
        go = gr.Button("Собрать превью", variant="primary", scale=1)

    with gr.Row():
        res_preset = gr.Dropdown(list(PRESETS), value="1920x1080 (Full HD)",
                                 label="Разрешение", scale=2)
        plate = gr.Dropdown(list(PLATES), value="Затемнение снизу", label="Под текстом",
                            scale=1)
        seed = gr.Slider(0, 99, value=7, step=1, label="Рисунок пергамента", scale=1)

    with gr.Accordion("Настройки", open=False):
        with gr.Row():
            samples = gr.Slider(12, 200, value=48, step=4,
                                label="Кадров сканировать (для стрима)")
            top_k = gr.Slider(3, 20, value=8, step=1, label="Кандидатов показать")
        with gr.Row():
            min_face = gr.Slider(0.02, 0.30, value=0.06, step=0.01, label="Мин. размер лица")
            max_yaw = gr.Slider(0.1, 1.0, value=0.45, step=0.05, label="Допустимый поворот головы")
        with gr.Row():
            side = gr.Radio(["right", "left"], value="right", label="Сторона стримера")
            scale = gr.Slider(0.5, 1.3, value=0.98, step=0.02, label="Размер стримера")
            outline = gr.Slider(0, 24, value=0, step=1, label="Обводка, px")
            mirror = gr.Checkbox(value=True, label="Отзеркалить стримера")
            shadow = gr.Checkbox(value=True, label="Тень")
        with gr.Row():
            off_x = gr.Slider(-0.4, 0.4, value=0.0, step=0.01, label="Сдвиг по X")
            off_y = gr.Slider(-0.3, 0.3, value=0.02, step=0.01, label="Сдвиг по Y")

    result = gr.Image(label="Готовое превью", type="numpy", height=420)
    info = gr.Markdown()
    download = gr.File(label="Файл")

    gr.Markdown("### Кандидаты — кликните, чтобы пересобрать с другой эмоцией "
                "(для стрима их несколько, для фото — один)")
    gallery = gr.Gallery(label="Эмоции из стрима", columns=6, height=220,
                         object_fit="cover", allow_preview=False)
    picked = gr.Number(value=0, visible=False)
    redo = gr.Button("Пересобрать с текущими настройками")

    state = gr.State()

    controls = [samples, top_k, min_face, max_yaw, side, scale, off_x, off_y,
                outline, shadow, caption, badge, res_preset, plate, seed, mirror]

    source.change(switch_source, inputs=source, outputs=[photo, stream_url])
    go.click(run, inputs=[video_url, source, photo, stream_url, *controls],
             outputs=[result, gallery, info, download, state, picked])
    gallery.select(on_select, outputs=picked).then(
        recompose, inputs=[state, picked, *controls], outputs=[result, download])
    redo.click(recompose, inputs=[state, picked, *controls], outputs=[result, download])

if __name__ == "__main__":
    demo.launch(inbrowser=True, css=CSS, theme=gr.themes.Soft())
