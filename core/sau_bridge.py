from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

WAIT = 600
YOUTUBE_START = "https://studio.youtube.com"
TIKTOK_START = "https://www.tiktok.com/login?lang=en"
DOMAINS = {"youtube": ("google.com", "youtube.com", "googleusercontent.com", "gstatic.com"),
           "tiktok": ("tiktok.com", "tiktokv.com", "byteoversea.com", "ttwstatic.com")}


BROWSER_ENV = "ASTRA_SAU_BROWSER"
CHANNELS = ("chrome", "msedge")
BUILTIN = "chromium"
CHANNEL_UPLOAD = "https://studio.youtube.com/channel/%s/videos/upload?d=ud"
CHECK_WAIT = 20
VIEWPORT = {"width": 1600, "height": 1000}
DONE_WAIT = 120000
SHOTS = "astra_shots"
SHOWN_DIALOG_JS = """() => {
  const found = [];
  for (const box of document.querySelectorAll('tp-yt-paper-dialog, [role=dialog]')) {
    const area = box.getBoundingClientRect();
    const look = getComputedStyle(box);
    if (area.width > 0 && area.height > 0 && look.display !== 'none'
        && look.visibility !== 'hidden') {
      found.push(box.innerText || '');
    }
  }
  return found.join(' | ');
}"""
IDENTITY_WORDS = ("личност", "verify it", "confirm it", "it's you", "验证")
IDENTITY_WAIT = 600
IDENTITY_HEADLESS = ("Google просит подтвердить личность. В скрытом режиме это невозможно: "
                     "включите «показать браузер», выложите ролик и подтвердите личность "
                     "в открывшемся окне. Потом скрытый режим снова заработает.")
STATE = {"headless": True, "context": None}
UPLOAD_DONE = ("处理", "检查", "上传完成", "已上传", "Processing", "complete", "Checks",
               "Finished", "Обработка", "Проверка", "завершен", "Загружено: 100")


def say(text):
    print(text, flush=True)


def browser_options(choice, given):
    options = dict(given)
    options.pop("channel", None)
    if not options.get("executable_path"):
        options.pop("executable_path", None)
    if choice in CHANNELS:
        options.pop("executable_path", None)
        options["channel"] = choice
    elif choice and choice != BUILTIN:
        options["executable_path"] = choice
    else:
        options.pop("executable_path", None)
    return options


def use_channel(channel) -> str:
    channel = str(channel or "").strip()
    if not channel:
        return ""
    from uploader.youtube_uploader import main as youtube

    youtube.UPLOAD_URL = CHANNEL_UPLOAD % channel
    say("канал: %s" % channel)
    return youtube.UPLOAD_URL


def mend_youtube(wait=CHECK_WAIT):
    from uploader.youtube_uploader import main as youtube

    async def patient(account_file):
        async with youtube.async_playwright() as engine:
            browser = await engine.chromium.launch(headless=False, channel="chrome")
            try:
                context = await browser.new_context(storage_state=account_file)
                context = await youtube.set_init_script(context)
                page = await context.new_page()
                await page.goto(youtube.STUDIO_URL, wait_until="domcontentloaded")
                for _ in range(wait):
                    url = page.url
                    if "accounts.google.com" in url or "/signin" in url.lower():
                        return False
                    if "/channel/" in url:
                        return True
                    await page.wait_for_timeout(1000)
                return False
            except Exception:
                return False
            finally:
                await browser.close()

    if not hasattr(youtube.YouTubeVideo, "set_publish_date"):
        def set_publish_date(self, when=None):
            if when:
                raise RuntimeError("расписание для YouTube в social-auto-upload пока не работает")
            self.publish_date = None

        youtube.YouTubeVideo.set_publish_date = set_publish_date

    async def wait_upload(page, max_polls=360):
        last = ""
        for _ in range(max_polls):
            text = ""
            for selector in (".progress-label", "span.progress-label",
                             "ytcp-video-upload-progress"):
                spot = page.locator(selector).first
                try:
                    if await spot.count():
                        text = (await spot.inner_text()).strip()
                        if text:
                            break
                except Exception:
                    pass
            if text:
                if any(word.lower() in text.lower() for word in UPLOAD_DONE):
                    say("загрузка закончена: %s" % text[:60])
                    return True
                if text != last:
                    say("загружается: %s" % text[:60])
                    last = text
            await page.wait_for_timeout(5000)
        say("загрузка не закончилась за 30 мин, всё равно публикую")
        return False

    youtube._wait_upload_complete = wait_upload

    original_upload = youtube.YouTubeVideo.upload
    if not getattr(original_upload, "_astra", False):
        async def upload(self, playwright):
            try:
                return await original_upload(self, playwright)
            except Exception as error:
                page = last_page()
                if page is None:
                    raise
                await snapshot(page, "upload_error")
                if isinstance(error, RuntimeError) and re.search(r"[а-яё]", str(error), re.I):
                    raise
                if asks_identity(await shown_dialog_text(page)):
                    raise RuntimeError(IDENTITY_HEADLESS if STATE["headless"]
                                       else "Google просит подтвердить личность: "
                                            "подтвердите в окне и выложите ещё раз") from error
                raise RuntimeError("YouTube не показал нужный шаг загрузки (%s). Снимок "
                                   "экрана — в logs/astra_shots" % str(error).splitlines()[0]
                                   ) from error

        upload._astra = True
        youtube.YouTubeVideo.upload = upload

    original_fill = youtube._fill_editable
    if not getattr(original_fill, "_astra", False):
        async def fill(page, selector, text):
            await identity_gate(page)
            return await original_fill(page, selector, text)

        fill._astra = True
        youtube._fill_editable = fill

    original_click = youtube._click_if_present
    if not getattr(original_click, "_astra", False):
        async def click(page, selector, timeout=4000):
            await identity_gate(page)
            done = "#done-button" in selector
            if done:
                timeout = max(timeout, DONE_WAIT)
            hit = await original_click(page, selector, timeout)
            if done and not hit:
                await snapshot(page, "no_publish_button")
            return hit

        click._astra = True
        youtube._click_if_present = click
    youtube.cookie_auth = patient
    try:
        from sau_commands import youtube as command
        command.youtube_cookie_auth = patient
    except ImportError:
        pass
    return patient


def last_page():
    context = STATE.get("context")
    try:
        pages = list(context.pages) if context is not None else []
    except Exception:
        return None
    return pages[-1] if pages else None


async def shown_dialog_text(page) -> str:
    try:
        return str(await page.evaluate(SHOWN_DIALOG_JS) or "").lower()
    except Exception:
        return ""


def asks_identity(text) -> bool:
    return any(word in text for word in IDENTITY_WORDS)


async def identity_gate(page, wait=IDENTITY_WAIT):
    if not asks_identity(await shown_dialog_text(page)):
        return False
    await snapshot(page, "identity")
    if STATE["headless"]:
        raise RuntimeError(IDENTITY_HEADLESS)
    say("Google просит подтвердить личность: подтвердите в открытом окне браузера, "
        "программа ждёт до %d мин." % (wait // 60))
    for _ in range(wait):
        await page.wait_for_timeout(1000)
        if not asks_identity(await shown_dialog_text(page)):
            say("личность подтверждена, продолжаю")
            return True
    raise RuntimeError("личность не подтверждена за %d мин" % (wait // 60))


async def snapshot(page, name) -> str:
    import time

    folder = Path.cwd() / "logs" / SHOTS
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("%s_%s.png" % (time.strftime("%Y%m%d_%H%M%S"), name))
        await page.screenshot(path=str(target), full_page=True)
        text = await page.locator("body").inner_text()
        target.with_suffix(".txt").write_text(text[:20000], encoding="utf-8")
        say("снимок экрана: %s" % target)
        return str(target)
    except Exception as error:
        say("снимок не сохранился: %s" % error)
        return ""


def use_viewport() -> int:
    patched = 0
    for module, waits in (("patchright.async_api", True), ("patchright.sync_api", False)):
        try:
            kind = __import__(module, fromlist=["Browser"]).Browser
        except (ImportError, AttributeError):
            continue
        original = getattr(kind, "new_context", None)
        if original is None or getattr(original, "_astra", False):
            continue
        if waits:
            async def wrapped(self, *args, _original=original, **kwargs):
                kwargs.setdefault("viewport", dict(VIEWPORT))
                context = await _original(self, *args, **kwargs)
                STATE["context"] = context
                return context
        else:
            def wrapped(self, *args, _original=original, **kwargs):
                kwargs.setdefault("viewport", dict(VIEWPORT))
                return _original(self, *args, **kwargs)
        wrapped._astra = True
        kind.new_context = wrapped
        patched += 1
    return patched


def use_browser(choice) -> bool:
    choice = str(choice or "").strip()
    if not choice:
        return False
    kinds = []
    try:
        from patchright.async_api import BrowserType as AsyncType
        kinds.append(AsyncType)
    except ImportError:
        pass
    try:
        from patchright.sync_api import BrowserType as SyncType
        kinds.append(SyncType)
    except ImportError:
        pass
    for kind in kinds:
        for name in ("launch", "launch_persistent_context"):
            original = getattr(kind, name, None)
            if original is None or getattr(original, "_astra", False):
                continue

            def wrapped(self, *args, _original=original, **kwargs):
                STATE["headless"] = bool(kwargs.get("headless", True))
                return _original(self, *args, **browser_options(choice, kwargs))

            wrapped._astra = True
            setattr(kind, name, wrapped)
    return bool(kinds)


def prepare() -> Path:
    import sau_cli
    root = Path(sau_cli.__file__).resolve().parent
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import playwright.async_api
    except ImportError:
        import patchright
        import patchright.async_api
        sys.modules["playwright"] = patchright
        sys.modules["playwright.async_api"] = patchright.async_api
    return root


def account_file(root, service, account) -> Path:
    folder = root / "cookies"
    folder.mkdir(exist_ok=True)
    return folder / ("%s_%s.json" % (service, account))


def own_cookies(state, service):
    keep = DOMAINS[service]
    cookies = [item for item in state.get("cookies", [])
               if str(item.get("domain", "")).lstrip(".").endswith(keep)]
    origins = [item for item in state.get("origins", [])
               if any(mark in str(item.get("origin", "")) for mark in keep)]
    return {"cookies": cookies, "origins": origins}


async def signed_in(service, page, context) -> bool:
    if service == "youtube":
        return "/channel/" in page.url
    for item in await context.cookies("https://www.tiktok.com"):
        if item.get("name") == "sessionid" and item.get("value"):
            return True
    return False


async def login(service, target, cdp_url):
    from patchright.async_api import async_playwright
    start = YOUTUBE_START if service == "youtube" else TIKTOK_START
    async with async_playwright() as engine:
        if cdp_url:
            browser = await engine.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
        else:
            browser = await engine.chromium.launch(headless=False)
            context = await browser.new_context()
        page = await context.new_page()
        await page.goto(start, wait_until="domcontentloaded")
        say("Войдите в аккаунт в открывшемся окне браузера, программа ждёт до %d мин."
            % (WAIT // 60))
        for _ in range(WAIT):
            if await signed_in(service, page, context):
                await page.wait_for_timeout(2000)
                state = await context.storage_state()
                target.write_text(json.dumps(own_cookies(state, service), ensure_ascii=False),
                                  encoding="utf-8")
                say("SAVED %s" % target)
                if cdp_url:
                    await page.close()
                else:
                    await browser.close()
                return True
            await asyncio.sleep(1)
        say("ERROR: вход не завершён за %d мин" % (WAIT // 60))
        return False


async def tiktok_check(target) -> bool:
    if not target.is_file():
        return False
    from uploader.tk_uploader.main_chrome import cookie_auth
    return bool(await cookie_auth(str(target)))


async def tiktok_upload(target, args):
    if not target.is_file():
        raise RuntimeError("нет входа в TikTok: сначала «Войти»")
    from uploader.tk_uploader.main_chrome import TiktokVideo
    tags = [part.strip().lstrip("#") for part in str(args.tags or "").split(",") if part.strip()]
    caption = " ".join(part for part in (args.title, args.desc) if part)
    video = TiktokVideo(caption, str(Path(args.file).resolve()), tags, 0, str(target))
    video.headless = not args.headed
    await video.main()

STUDIO_LIST = "https://studio.youtube.com/channel/%s/videos/%s"
LIST_TABS = ("short", "upload")
ROWS_WAIT = 30
PAGES = 20
ROWS_JS = """() => {
  const cell = (row, name) => {
    const box = row.querySelector('[class*="tablecell-' + name + '"]');
    return box ? (box.innerText || '').trim() : '';
  };
  return Array.from(document.querySelectorAll('ytcp-video-row')).map(row => {
    const title = row.querySelector('#video-title');
    return {
      title: title ? (title.innerText || title.textContent || '').trim() : '',
      links: Array.from(row.querySelectorAll('a[href]')).map(a => a.href),
      visibility: cell(row, 'visibility'),
      date: cell(row, 'date'),
      views: cell(row, 'views'),
      comments: cell(row, 'comments'),
      likes: cell(row, 'likes'),
    };
  });
}"""
NEXT_JS = """() => {
  const button = document.querySelector('#navigate-after');
  if (!button) return false;
  if (button.hasAttribute('disabled') || button.getAttribute('aria-disabled') === 'true') {
    return false;
  }
  return true;
}"""
SIGNED_OUT = ("Вход в YouTube устарел: нажмите «Войти» во вкладке «Выгрузка» "
              "и войдите заново")
IDENTITY_STOP = ("Google просит подтвердить личность. Просмотры не прочитаны: "
                 "подтвердите личность сами (например, выложив ролик с окном браузера) "
                 "и нажмите «Обновить просмотры» ещё раз.")


def signed_out(url) -> bool:
    url = str(url or "")
    return "accounts.google.com" in url or "/signin" in url.lower()


async def wait_rows(page, wait=ROWS_WAIT) -> int:
    for _ in range(wait * 2):
        if signed_out(page.url):
            raise RuntimeError(SIGNED_OUT)
        if asks_identity(await shown_dialog_text(page)):
            await snapshot(page, "identity")
            raise RuntimeError(IDENTITY_STOP)
        count = await page.locator("ytcp-video-row").count()
        if count:
            await page.wait_for_timeout(1500)
            return await page.locator("ytcp-video-row").count()
        await page.wait_for_timeout(500)
    return 0


async def read_list(page, url, tab, pages=PAGES):
    await page.goto(url, wait_until="domcontentloaded")
    found = []
    for number in range(pages):
        if not await wait_rows(page):
            break
        rows = await page.evaluate(ROWS_JS)
        for row in rows:
            row["tab"] = tab
            say("ROW " + json.dumps(row, ensure_ascii=False))
        found.extend(rows)
        if number + 1 >= pages or not await page.evaluate(NEXT_JS):
            break
        first = rows[0].get("title") if rows else ""
        await page.locator("#navigate-after").first.click()
        for _ in range(40):
            await page.wait_for_timeout(500)
            again = await page.evaluate(ROWS_JS)
            if again and again[0].get("title") != first:
                break
    return found


async def own_channel(page) -> str:
    await page.goto(YOUTUBE_START, wait_until="domcontentloaded")
    for _ in range(CHECK_WAIT * 2):
        if signed_out(page.url):
            raise RuntimeError(SIGNED_OUT)
        hit = re.search(r"/channel/(UC[\w-]{22})", page.url)
        if hit:
            return hit.group(1)
        await page.wait_for_timeout(500)
    raise RuntimeError("YouTube Studio не открыл канал: укажите канал во вкладке «Выгрузка»")


async def youtube_views(target, args):
    from patchright.async_api import async_playwright
    test_page = str(args.page or "").strip()
    if not test_page and not target.is_file():
        raise RuntimeError("нет входа в YouTube: сначала «Войти»")
    headless = bool(test_page.startswith("file:")) and args.headless
    choice = os.environ.get(BROWSER_ENV, "")
    async with async_playwright() as engine:
        browser = await engine.chromium.launch(
            **browser_options(choice, {"headless": headless}))
        try:
            context = await browser.new_context(
                storage_state=str(target) if target.is_file() else None,
                viewport=dict(VIEWPORT))
            STATE["context"] = context
            page = await context.new_page()
            if test_page:
                return await read_list(page, test_page, "test", args.pages)
            channel = str(args.channel or "").strip() or await own_channel(page)
            say("канал: %s" % channel)
            found = []
            for tab in LIST_TABS:
                say("читаю %s" % ("Shorts" if tab == "short" else "видео"))
                found.extend(await read_list(page, STUDIO_LIST % (channel, tab), tab,
                                             args.pages))
            return found
        finally:
            await browser.close()


def parser():
    top = argparse.ArgumentParser(prog="sau_bridge")
    top.add_argument("service", choices=("youtube", "tiktok"))
    top.add_argument("action", choices=("login", "check", "upload-video", "views"))
    top.add_argument("--account", required=True)
    top.add_argument("--cdp-url", default="")
    top.add_argument("--file", default="")
    top.add_argument("--title", default="")
    top.add_argument("--desc", default="")
    top.add_argument("--tags", default="")
    top.add_argument("--headed", action="store_true")
    top.add_argument("--headless", action="store_true")
    top.add_argument("--channel", default="")
    top.add_argument("--page", default="")
    top.add_argument("--pages", type=int, default=PAGES)
    return top


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = prepare()
    choice = os.environ.get(BROWSER_ENV, "")
    if use_browser(choice):
        say("браузер: %s" % choice)
    use_viewport()
    if argv[:1] == ["--channel"] and len(argv) > 1:
        use_channel(argv[1])
        argv = argv[2:]
    if argv[:1] == ["--sau"]:
        import sau_cli
        if argv[1:2] == ["youtube"]:
            try:
                mend_youtube()
            except ImportError:
                pass
        try:
            return int(sau_cli.main(argv[1:]) or 0)
        except Exception as error:
            say("ERROR: %s" % error)
            return 1
    args = parser().parse_args(argv)
    target = account_file(root, args.service, args.account)
    try:
        if args.action == "login":
            return 0 if asyncio.run(login(args.service, target, args.cdp_url)) else 1
        if args.action == "views":
            if args.service != "youtube":
                raise RuntimeError("просмотры читаются только с YouTube")
            rows = asyncio.run(youtube_views(target, args))
            say("ROWS %d" % len(rows))
            return 0
        if args.service != "tiktok":
            raise RuntimeError("через обёртку для YouTube доступен только вход")
        if args.action == "check":
            good = asyncio.run(tiktok_check(target))
            say("valid" if good else "invalid")
            return 0 if good else 1
        asyncio.run(tiktok_upload(target, args))
        say("TikTok video upload submitted: %s" % args.file)
        return 0
    except Exception as error:
        say("ERROR: %s" % error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
