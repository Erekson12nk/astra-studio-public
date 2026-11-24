from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SUITES = [
    ("learn_test.py", "связка баз clipper и astra", False),
    ("studio_test.py", "окно: вкладки, таймкоды, расшифровка", True),
    ("cache_test.py", "общий кеш расшифровки", True),
    ("rolik_test.py", "режим «Ролики» целиком", True),
    ("studio_build_test.py", "окно: сборка роликов", True),
    ("face_test.py", "выбор вебки: устойчивое лицо против крупного", True),
    ("layout_test.py", "раскладки кадра: два блока, вебка, экран", True),
    ("profile_test.py", "профили: фото, срок хранения, защита клипов", False),
    ("keep_test.py", "срок хранения роликов: выбор в окне и подметание", True),
    ("fold_test.py", "тонкие настройки свёрнуты, но работают", True),
    ("fetch_test.py", "стрим качается по ссылке из окна", True),
    ("notify_test.py", "долгая работа: процент в заголовке, мигание", True),
    ("scroll_test.py", "содержимое прокручивается, окно не распирает", True),
    ("welcome_test.py", "первое окно: профиль стримера", True),
    ("rerank_test.py", "реранкер: обучение на сравнениях и оценках", False),
    ("webcam_test.py", "поиск вебки по снимку, крупный план", True),
    ("gui_layout_test.py", "окно astra: «Сама решит» и покраска списков", True),
    ("adopt_test.py", "клипы из astra возвращаются в базу clipper", False),
    ("embed_test.py", "монтаж astra внутри Студии", True),
    ("cachedir_test.py", "разбор и чистка кеша", False),
    ("anchor_test.py", "якорь вебки собирается до рендера", True),
    ("scan_audio_test.py", "звук стрима читается один раз, не дважды", True),
    ("packages_test.py", "раскладка репозитория и точки входа", False),
    ("encoder_test.py", "выбор кодировщика и объяснение отказа", False),
    ("pairs_test.py", "подбор пар для оценки клипов", False),
    ("theme_test.py", "тема одна на оба окна", True),
    ("settings_test.py", "настройки рендера: окно и движок", True),
    ("compare_test.py", "сравнение фрагментов между собой", False),
    ("profiles_home_test.py", "профили в папке программы, своё хранилище", True),
    ("clean_test.py", "цензура внутри Студии", True),
    ("export_test.py", "выгрузка моментов: один стрим на файл", False),
    ("picks_test.py", "ваш выбор как обучающий сигнал", False),
    ("missed_test.py", "клипы, пропущенные поиском, идут в обучение", False),
    ("learnlog_test.py", "журнал обучения, сброс, колесо и Ctrl+V", True),
    ("pauses_test.py", "вырезание пауз не съедает сам клип", True),
    ("jingle_test.py", "звук доната по одному образцу", False),
    ("away_test.py", "где стример отошёл: лицо, заставка, тишина", True),
    ("donations_test.py", "донаты по теме и нет, голоса, вырезы в клипах и роликах", True),
    ("voice_ui_test.py", "окно «Голос и донаты» и сборка ролика с проверкой", True),
    ("openai_compat_test.py", "LM Studio и OpenAI-совместимые: схема, откат, окно", True),
    ("upload_test.py", "выгрузка на YouTube и TikTok через sau", True),
    ("views_test.py", "просмотры YouTube из Studio без API", True),
    ("taste_test.py", "просмотры → правила вкуса → задание нейронке", True),
    ("autopilot_test.py", "полуавтопилот: стрим → очередь → одобрение → выгрузка", True),
    ("service_test.py", "служба: сторожа всех стримеров, работа по одному, замок", False),
    ("telegram_test.py", "анонсы в телеграме и запись эфиров не с твича", False),
    ("roliks_test.py", "ролики по играм: главы твича, сборка, очередь", False),
    ("reactions_test.py", "реакции: смена видео, уточнение моделью, кадры", False),
    ("cleanup_test.py", "чистка диска, место, расписание роликов, уведомления", False),
    ("thumb_test.py", "превью: зеркало, без обводки, текст во всю ширину", False),
    ("live_test.py", "запись звука эфира и звук из неё на местах мута", False),
    ("design_test.py", "оформление: палитра, иконки, анимации вкладок", True),
    ("launch_test.py", "запуск: режимы, прогноз тиктоков и роликов, учёба на своих выгрузках", False),
    ("mute_test.py", "мут твича: где заглушено и свой звук на это место", True),
]

HEAVY = {"studio_sense_test.py"}


def interpreter(needs_heavy):
    if not needs_heavy:
        return sys.executable
    for path in (os.path.join(REPO, ".venv", "Scripts", "python.exe"),
                 os.path.join(os.path.dirname(REPO), "astra", ".venv",
                              "Scripts", "python.exe"),
                 os.path.join(REPO, "astra", ".venv", "Scripts", "python.exe")):
        if os.path.isfile(path):
            return path
    return sys.executable


def main():
    only = sys.argv[1:]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("ASTRA_STUDIO_SETTINGS",
                   os.path.join(tempfile.gettempdir(), "astra_studio_tests",
                                "settings.json"))
    os.makedirs(os.path.dirname(env["ASTRA_STUDIO_SETTINGS"]), exist_ok=True)
    results = []
    for name, title, heavy in SUITES:
        if only and not any(part in name for part in only):
            continue
        exe = interpreter(heavy)
        print("=" * 72)
        print("%s  —  %s" % (name, title))
        print("=" * 72)
        started = time.time()
        try:
            done = subprocess.run([exe, os.path.join(HERE, name)], env=env,
                                  timeout=1800)
            code = done.returncode
        except subprocess.TimeoutExpired:
            code = -1
            print("  ПРЕВЫШЕНО ВРЕМЯ")
        results.append((name, code, time.time() - started))
        print()

    print("=" * 72)
    bad = 0
    for name, code, took in results:
        mark = "прошло" if code == 0 else "ПРОВАЛ"
        if code != 0:
            bad += 1
        print("  %-24s %-8s %5.1f с" % (name, mark, took))
    print("  итого: %d наборов, провалов %d" % (len(results), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
