from mcp_instance import mcp, api_client
# Parsing HTML sajta: formy, dataLayer, schetchiki, sobytiya.
# Kommentarii translitom, chtoby ne bylo krakozyabr v Windows-1251.
#
# Playwright ASYNC API + perehvat setevyh zaprosov.

import re
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from server import mcp

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.9",
}

WAIT_AFTER_MS = 6000


def _short_url(u: str, keep_params=("id", "container_id", "containerId", "tid", "ver")) -> str:
    """
    Ukorachivaem URL dlya vyvoda: host + path + tol'ko nuzhnye query-param.
    Inache mc.yandex.ru/watch/... s browser-info razduvaet otvet do megabajtov.
    """
    if not u:
        return u

    # Esli URL bez shemy — dobavlyaem https://, inache urlparse padaet
    if "://" not in u:
        u = "https://" + u.lstrip("/")

    try:
        p = urlparse(u)
    except Exception:
        return u[:120]

    base = f"{p.scheme}://{p.netloc}{p.path}"

    if p.query:
        qs = parse_qs(p.query)
        keep = []
        for k in keep_params:
            if k in qs:
                keep.append(f"{k}={qs[k][0]}")
        if keep:
            base += "?" + "&".join(keep)
    return base


def _fetch_plain(url: str, timeout: int = 30):
    """Obychnyj GET bez JS. Fallback, esli Playwright nedostupen."""
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout,
                            allow_redirects=True)
    except requests.exceptions.RequestException as e:
        return None, {"error": str(e)}, []

    if resp.status_code != 200:
        return None, {"error": f"HTTP {resp.status_code} dlya {url}"}, []

    raw = resp.content
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode(resp.apparent_encoding or "cp1251", errors="replace")
    return html, None, []


async def _fetch_js(url: str, timeout: int = 30000, wait_after: int = WAIT_AFTER_MS):
    """Zabiraem HTML posle otrisovki JS + perehvat setevyh zaprosov."""
    if not PLAYWRIGHT_AVAILABLE:
        return None, {"error": "Playwright ne ustanovlen. "
                               "Vypolni: pip install playwright && "
                               "python -m playwright install chromium"}, []

    requests_log = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="ru-RU",
            )
            page = await ctx.new_page()

            def _on_request(req):
                try:
                    requests_log.append({
                        "url": req.url,
                        "method": req.method,
                        "resource_type": req.resource_type,
                    })
                except Exception:
                    pass

            page.on("request", _on_request)

            try:
                await page.goto(url, timeout=timeout, wait_until="networkidle")
            except Exception:
                pass
            await page.wait_for_timeout(wait_after)
            html = await page.content()
            await browser.close()
    except Exception as e:
        return None, {"error": f"Playwright error: {e}"}, []

    return html, None, requests_log


async def _fetch(url: str, use_js: bool = True, timeout: int = 30):
    """Universal'naya zagruzka HTML."""
    if use_js:
        return await _fetch_js(url, timeout=timeout * 1000)
    return _fetch_plain(url, timeout=timeout)


def _parse_forms(soup: BeautifulSoup, base_url: str) -> list:
    """Ishchem formy i ih polya."""
    forms = []
    for f in soup.find_all("form"):
        fields = []
        for inp in f.find_all(["input", "select", "textarea", "button"]):
            fields.append({
                "tag": inp.name,
                "name": inp.get("name"),
                "type": inp.get("type"),
                "id": inp.get("id"),
                "required": inp.has_attr("required"),
            })
        forms.append({
            "id": f.get("id"),
            "name": f.get("name"),
            "action": f.get("action"),
            "method": (f.get("method") or "get").lower(),
            "class": f.get("class"),
            "fields_count": len(fields),
            "fields": fields,
        })
    return forms


def _parse_counters_from_html(html: str) -> dict:
    """Schetchiki, kotorye vidny pryamo v HTML."""
    metrika_ids = set()
    ga_ids = set()
    ytm_hits = []
    metrika_scripts = []

    for m in re.finditer(r"ym\(\s*(\d{5,12})", html):
        metrika_ids.add(m.group(1))
    for m in re.finditer(r"MetrikaTagID['\"]?\s*[:=]\s*['\"]?(\d{5,12})", html):
        metrika_ids.add(m.group(1))
    for m in re.finditer(
        r"new\s+Ya\.Metrika\s*\(\s*\{[^}]*?id['\"]?\s*:\s*(\d{5,12})",
        html, re.S,
    ):
        metrika_ids.add(m.group(1))

    # Ssylki na mc.yandex.* — v HTML chasto bez shemy, poetomu _short_url
    # sam dobavlyaet https://, esli ego net.
    for m in re.finditer(r"mc\.yandex\.[a-z\.]+/[^\"'\s<>]*", html):
        metrika_scripts.append(_short_url(m.group(0)))
    for m in re.finditer(r"(?:YA\.Metrika2|yandex_metrika|metrika/tag\.js)",
                         html):
        metrika_scripts.append(m.group(0))

    for m in re.finditer(
        r"gtag\s*\(\s*['\"]config['\"]\s*,\s*['\"]([A-Z0-9\-]+)['\"]", html
    ):
        ga_ids.add(m.group(1))
    for m in re.finditer(r"['\"](G|UA|GT|GTAG)-[A-Z0-9\-]{4,}['\"]", html):
        ga_ids.add(m.group(0).strip("'\""))

    for m in re.finditer(
        r"(?:tagmanager\.yandex|api\.ytm\.yandex)[^\"'\s<]*", html
    ):
        ytm_hits.append(_short_url(m.group(0)))
    for m in re.finditer(
        r"(?:ytm[_-]?container[_-]?id|containerId)['\"]?\s*[:=]\s*['\"]?(\d{4,12})",
        html, re.I,
    ):
        ytm_hits.append(f"containerId={m.group(1)}")

    return {
        "metrika_ids": sorted(metrika_ids),
        "ga_ids": sorted(ga_ids),
        "ytm_mentions": list(dict.fromkeys(ytm_hits))[:10],
        "metrika_scripts": list(dict.fromkeys(metrika_scripts))[:10],
    }


def _parse_counters_from_network(requests_log: list) -> dict:
    """
    Tochnaya informatsiya iz setevyh zaprosov.
    Vse URL sokrashchaem cherez _short_url.
    """
    metrika_ids = set()
    metrika_watch = set()
    metrika_other = set()
    ga_ids = set()
    ytm_container_ids = set()
    third_party = set()

    THIRD_PARTY_DOMAINS = (
        "google-analytics.com", "googletagmanager.com",
        "facebook.net", "connect.facebook.net",
        "vk.com/rtrg", "top-fwz1.mail.ru",
        "mc.yandex.", "tagmanager.yandex", "api.ytm.yandex",
    )

    for r in requests_log:
        u = r.get("url", "")
        if not u:
            continue

        # 1. tag.js?id=NNN
        m = re.search(r"mc\.yandex\.[a-z\.]+/metrika/tag[^?]*\?[^#]*\bid=(\d{5,12})", u)
        if m:
            metrika_ids.add(m.group(1))

        # 2. watch/NNN
        m = re.search(r"mc\.yandex\.[a-z\.]+/watch/(\d{5,12})", u)
        if m:
            metrika_watch.add(m.group(1))

        # 3. drugie zaprosy k Metrike
        if "mc.yandex." in u and "/metrika/" in u:
            metrika_other.add(_short_url(u))

        # 4. GA / gtag
        m = re.search(r"[?&]tid=([A-Z0-9\-]+)", u)
        if m and ("google-analytics.com" in u or "googletagmanager.com" in u):
            ga_ids.add(m.group(1))
        m = re.search(r"googletagmanager\.com/gtag/js\?id=([A-Z0-9\-]+)", u)
        if m:
            ga_ids.add(m.group(1))

        # 5. YTM kontejnery
        m = re.search(r"(?:containerId|container_id)=(\d{4,12})", u, re.I)
        if m and "yandex" in u:
            ytm_container_ids.add(m.group(1))

        # 6. Storonnij load
        if any(d in u for d in THIRD_PARTY_DOMAINS):
            third_party.add(_short_url(u))

    return {
        "metrika_ids_from_network": sorted(metrika_ids),
        "metrika_watch_ids": sorted(metrika_watch),
        "ga_ids_from_network": sorted(ga_ids),
        "ytm_container_ids_from_network": sorted(ytm_container_ids),
        "metrika_other_requests": sorted(metrika_other)[:15],
        "third_party_hits": sorted(third_party)[:30],
    }


def _parse_datalayer(html: str) -> list:
    """dataLayer.push / dataLayer = [] / window.dataLayer init."""
    events = []

    for m in re.finditer(r"dataLayer\.push\s*\(\s*(\{.*?\})\s*\)", html, re.S):
        events.append({"type": "push", "raw": m.group(1)[:500]})

    for m in re.finditer(r"dataLayer\s*=\s*(\[[^\]]*\])\s*;", html, re.S):
        events.append({"type": "assign", "raw": m.group(1)[:500]})

    for m in re.finditer(
        r"window\.dataLayer\s*=\s*window\.dataLayer\s*\|\|\s*\[\s*\]", html
    ):
        events.append({"type": "init", "raw": m.group(0)})

    return events[:20]


def _parse_inline_events(soup: BeautifulSoup) -> list:
    """Inline-obrabotchiki onclick / onsubmit / onchange i t.p."""
    events = []
    handlers = ("onclick", "onsubmit", "onchange", "oninput", "onload",
                "onscroll", "onfocus")
    for el in soup.find_all(True):
        for h in handlers:
            if el.has_attr(h):
                events.append({
                    "tag": el.name,
                    "id": el.get("id"),
                    "class": el.get("class"),
                    "handler": h,
                    "code": (el.get(h) or "")[:200],
                })
    return events[:50]


def _parse_js_listeners(html: str) -> list:
    """addEventListener('click', ...) i t.p. — po syromu HTML."""
    listeners = []
    for m in re.finditer(r"addEventListener\s*\(\s*['\"]([a-z]+)['\"]", html):
        listeners.append(m.group(1))
    uniq = {}
    for t in listeners:
        uniq[t] = uniq.get(t, 0) + 1
    return [{"event": k, "count": v} for k, v in sorted(uniq.items())]


@mcp.tool()
async def fetch_page(url: str, use_js: bool = True) -> dict:
    """Zabiraem HTML stranicy. Vozvrashchaem razmer i nachalo teksta."""
    html, err, _ = await _fetch(url, use_js=use_js)
    if err:
        return err
    return {
        "url": url,
        "use_js": use_js,
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "length": len(html),
        "preview": html[:1000],
    }


@mcp.tool()
async def analyze_site(url: str, use_js: bool = True) -> dict:
    """
    Polnyj analiz HTML stranicy.
    Vse URL v otvete ukorocheny.
    """
    html, err, requests_log = await _fetch(url, use_js=use_js)
    if err:
        return err

    soup = BeautifulSoup(html, "html.parser")

    counters_html = _parse_counters_from_html(html)
    counters_net = _parse_counters_from_network(requests_log) if requests_log else {
        "metrika_ids_from_network": [],
        "metrika_watch_ids": [],
        "ga_ids_from_network": [],
        "ytm_container_ids_from_network": [],
        "metrika_other_requests": [],
        "third_party_hits": [],
    }

    metrika_all = sorted(set(
        counters_html["metrika_ids"] + counters_net["metrika_ids_from_network"]
        + counters_net["metrika_watch_ids"]
    ))
    ga_all = sorted(set(
        counters_html["ga_ids"] + counters_net["ga_ids_from_network"]
    ))

    return {
        "url": url,
        "use_js": use_js,
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "length": len(html),
        "requests_total": len(requests_log),
        "title": (soup.title.string.strip() if soup.title and soup.title.string
                  else None),
        "forms": _parse_forms(soup, url),
        "counters": {
            "metrika_ids": metrika_all,
            "metrika_watch_ids": counters_net["metrika_watch_ids"],
            "ga_ids": ga_all,
            "ytm_container_ids": counters_net["ytm_container_ids_from_network"],
            "metrika_scripts": counters_html["metrika_scripts"],
            "metrika_other_requests": counters_net["metrika_other_requests"],
            "third_party_hits": counters_net["third_party_hits"],
        },
        "datalayer": _parse_datalayer(html),
        "inline_events": _parse_inline_events(soup),
        "js_listeners": _parse_js_listeners(html),
    }