import os, re, json, time, calendar, sqlite3, hashlib, logging, requests
import urllib.parse
import feedparser
from urllib.parse import urljoin
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("agent")

TG = os.environ.get("TELEGRAM_TOKEN", "")
CHAT = os.environ.get("CHAT_ID", "")
BALE = os.environ.get("BALE_TOKEN", "")
BALE_CHAT = os.environ.get("BALE_CHAT_ID", "")
BALE_API = os.environ.get("BALE_API_BASE", "https://tapi.bale.ai").rstrip("/")
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "300"))
MAX_CHANNELS = int(os.environ.get("MAX_CHANNELS", "150"))
MAX_AGE = int(os.environ.get("MAX_AGE_DAYS", "7")) * 86400
LLM_KEY = os.environ.get("LLM_API_KEY", "")
LLM_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "fa-IR,fa;q=0.9"}

if not TG:
    log.warning("TELEGRAM_TOKEN تنظیم نشده: موارد فقط در لاگ نوشته می‌شوند و ارسال نمی‌شوند.")

# ---------- متن و کلیدواژه‌ها ----------
# normalize: نیم‌فاصله به فاصله، ی/ک عربی به فارسی، حذف کشیده، یکسان‌سازی رقم‌ها
TR = str.maketrans({"‌": " ", "ي": "ی", "ك": "ک", "ة": "ه", "ـ": "", "ﻻ": "لا",
                    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
                    "0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9"})
K1 = re.compile(r"دانش ?بنیان|شرکت ?های ?دانش|اینیشیو|شتاب ?دهنده")
K2 = re.compile(r"تسهیلات|وام|فراخوان|اعتبار|حمایت|گرنت|سرمایه ?گذاری|جذب ?منابع|مشارکت ?مالی|ضمانتنامه|"
                r"کمک ?مالی|معافیت|سود ?برگ|تأمین ?مالی|ثبت ?نام|پیش ?نهاد|دور ?تحقیقات")
BAD = re.compile(r"آگهی ?استخدام|جذب ?نیرو|فروش ?ویژه|تخفیف ?ویژه|کلاه ?بردار|قرعه ?کشی|مسابقه|جشنواره ?فيلم|"
                 r"وبینار|وب ?نار|کارگاه ?آموزشی|دوره ?آموزشی|بوت ?کمپ|اردو|ثبت ?نام ?آزادسازی|"
                 r"ازدواج|سفارش ?عروسی|زنگ ?تدریس|مدرسه ?تابستانه| IELTS|آیلتس|تول ?روز")

def flat(t):
    """یکسان‌سازی فاصله‌ها برای نمایش؛ متن فارسی دست‌نخورده می‌ماند (نیم‌فاصله حفظ می‌شود)."""
    return re.sub(r"\s+", " ", (t or "").replace("\u200f", "").replace("\u200e", "")).strip()

def norm(t):
    return re.sub(r"\s+", " ", (t or "").translate(TR)).strip()

# ---------- پلتفرم‌ها ----------
# فقط تلگرام و ایتا پیام‌ها را در HTML اولیه رندر می‌کنند (تست شده).
# بله و روبیکا با JavaScript رندر می‌شوند و صفحه‌ی عمومی‌شان قابل خزش نیست.
PLATFORMS = {
    "tg": "https://t.me/s/{}",
    "bale": "https://ble.ir/s/{}",
    "eitaa": "https://eitaa.com/{}",
}
CH_RE = re.compile(r"(?:https?://)?(?:www\.)?(t\.me|telegram\.me|ble\.ir|eitaa\.com)/(?:s/)?@?([A-Za-z][A-Za-z0-9_]{3,})")
HOST2PLAT = {"t.me": "tg", "telegram.me": "tg", "ble.ir": "bale", "eitaa.com": "eitaa"}
IGNORE = {"joinchat", "join", "share", "addstickers", "addtext", "proxy", "socks", "login", "download",
          "contact", "iv", "s", "c", "setlanguage", "addtheme", "boost", "addlist", "giftcode", "confirmemail",
          "blog", "news", "www", "telegram", "about", "faq", "privacy", "terms", "support", "channel", "group"}

os.makedirs("data", exist_ok=True)
db = sqlite3.connect("data/seen.db")
db.execute("CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY, ts REAL)")
db.execute("CREATE TABLE IF NOT EXISTS channels(key TEXT PRIMARY KEY, status TEXT)")  # new/ok/no/manual

def is_new(uid):
    return db.execute("SELECT 1 FROM seen WHERE id=?", (uid,)).fetchone() is None

def mark(uid):
    db.execute("INSERT OR IGNORE INTO seen VALUES(?,?)", (uid, time.time()))

def commit():
    db.commit()

# ---------- شبکه ----------
def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code in (429, 503):
                last = Exception(f"http {r.status_code}"); time.sleep(2 + 3 * i); continue
            r.raise_for_status()
            return r
        except Exception as e:
            last = e; time.sleep(1 + 2 * i)
    raise last

def post(url, **kw):
    return requests.post(url, timeout=30, **kw)

def ts_of(entry):
    p = entry.get("published_parsed") or entry.get("updated_parsed")
    try:
        return calendar.timegm(p) if p else None
    except Exception:
        return None

# ---------- ارسال ----------
def send_one(base, chat, text):
    if not (base and chat):
        return False
    for i in range(5):
        try:
            r = post(f"{base}/sendMessage", json={"chat_id": chat, "text": text[:3900]})
        except Exception as e:
            log.warning("خطای ارسال (%s): %s", base.split("/")[2], e); time.sleep(2 + 3 * i); continue
        if r.ok:
            return True
        if r.status_code == 429:
            try:
                wait = int(r.json().get("parameters", {}).get("retry_after", 5))
            except Exception:
                wait = 5
            log.warning("محدودیت نرخ ارسال: %d ثانیه صبر", wait); time.sleep(wait + 1); continue
        if r.status_code >= 500:
            time.sleep(2 + 3 * i); continue
        log.warning("ارسال رد شد %s: %s", r.status_code, r.text[:200])
        return False
    return False

def send_all(text):
    if not TG:
        log.info("[-] %s", flat(text)[:260]); return
    if send_one(f"https://api.telegram.org/bot{TG}", CHAT, text):
        time.sleep(0.4)  # رعایت سقف تلگرام (~۲۰ پیام در دقیقه)
    if BALE and BALE_CHAT:
        send_one(f"{BALE_API}/bot{BALE}", BALE_CHAT, text)
        time.sleep(0.4)

# ---------- فیلتر هوشمند اختیاری ----------
def llm_ok(title, body):
    if not (LLM_KEY and LLM_URL and LLM_MODEL):
        return True
    prompt = (f"متن: {title}\n{body[:700]}\n\n"
              "آیا این یک تسهیلات/وام/فراخوان/حمایت مالی قابل درخواست برای شرکت‌های دانش‌بنیان است "
              "(نه صرفاً خبر، تحلیل، تبلیغ یا گزارش)؟ فقط YES یا NO.")
    try:
        r = post(f"{LLM_URL}/chat/completions", headers={"Authorization": f"Bearer {LLM_KEY}"},
                 json={"model": LLM_MODEL, "temperature": 0, "max_tokens": 16,
                       "messages": [{"role": "user", "content": prompt}]}, )
        if not r.ok:
            log.warning("llm http %s", r.status_code); return True
        return "YES" in r.json()["choices"][0]["message"]["content"].upper()
    except Exception as e:
        log.warning("llm خطا: %s", e); return True

# ---------- کشف خودکار کانال‌ها ----------
def discover(text):
    for host, name in CH_RE.findall(norm(text) or ""):
        if name.lower() in IGNORE:
            continue
        if db.execute("SELECT COUNT(*) FROM channels WHERE status!='no'").fetchone()[0] >= MAX_CHANNELS:
            return
        db.execute("INSERT OR IGNORE INTO channels VALUES(?, 'new')", (f"{HOST2PLAT[host]}:{name.lower()}",))
    commit()

def n_ch():
    return db.execute("SELECT COUNT(*) FROM channels WHERE status!='no'").fetchone()[0]

# ---------- دریافت‌کننده‌ها: خروجی (متن، لینک، زمان) ----------
def real_link(url):
    """آدرس نهایی را از میان‌جی‌های خبری (Bing/…) بیرون می‌کشد."""
    try:
        if not url:
            return url
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        for k in ("url", "u", "target", "dest", "link"):
            if k in q and q[k][0].startswith("http"):
                return q[k][0]
    except Exception:
        pass
    return url

def fetch_rss(url):
    d = feedparser.parse(url, request_headers=UA)
    if not d.entries:
        raise Exception(f"no items (status={getattr(d, 'status', '?')}, bozo={d.bozo})")
    for e in d.entries:
        s = re.sub("<[^>]+>", " ", e.get("summary", "") or e.get("title", ""))
        text = flat(f"{e.get('title', '')} {s}")
        if text:
            yield text, real_link(e.get("link", "")), ts_of(e)

def fetch_html(url, selector=None):
    """تیترهای لیستِ اخبار/فراخوان یک سایت. لینک‌های منو، صفحه‌بندی و جست‌وجو کنار گذاشته می‌شوند.
    اگر selector داده شود فقط همان انتخاب می‌شود (برای سایت‌هایی که آیتم‌شان الگوی آدرسی مشخصی ندارد)."""
    r = get(url)
    discover(r.text)
    soup = BeautifulSoup(r.text, "html.parser")
    page = url.split("#")[0]
    skip = re.compile(r"/(category|page|tag|archive|search|login|about|contact|help|rss|feed)/?", re.I)
    item = re.compile(r"/\d{3,}(/|$)|/(news|article|post|content|detail|show|file)/", re.I)
    anchors = []
    if selector:
        for el in soup.select(selector):
            a = el if el.name == "a" else el.find("a", href=True)
            if a:
                anchors.append((el.get_text(" ", strip=True), a["href"]))
    else:
        anchors = [(a.get_text(" ", strip=True), a["href"]) for a in soup.find_all("a", href=True)]
    done, items, others = set(), [], []
    for text, href in anchors:
        t = flat(text)
        href = urljoin(page, href.split("#")[0])
        if len(t) < 15 or "?" in href or href in done or href.rstrip("/") == page.rstrip("/") or skip.search(href):
            continue
        done.add(href)
        (items if (selector or item.search(href)) else others).append((t, href, None))
    yield from (items or [x for x in others if len(x[0]) > 25])

# ---------- کانال‌ها ----------
def iso_ts(el):
    d = (el.get("datetime") or "") if el else ""
    try:
        return calendar.timegm(time.strptime(d[:19], "%Y-%m-%dT%H:%M:%S")) if d else None
    except Exception:
        return None

def page_chan(html, pfx):
    """ساختار مشترک پیش‌نمایش عمومی تلگرام (tgme_) و ایتا (etme_)."""
    soup = BeautifulSoup(html, "html.parser")
    head = soup.select_one(f".{pfx}_channel_info")
    meta = flat(head.get_text(" ", strip=True)) if head else ""
    items = []
    for m in soup.select(f".{pfx}_widget_message"):
        if "service_message" in (m.get("class") or []):  # «سنجاق پیغام» و پیام‌های سیستمی ایتا
            continue
        t = m.select_one(f".{pfx}_widget_message_text")
        if not t:
            continue
        txt = flat(t.get_text(" ", strip=True))
        if not txt:
            continue
        a = m.select_one(f"a.{pfx}_widget_message_date")
        items.append((txt, (a.get("href") or "") if a else "", iso_ts(m.select_one("time"))))
    return meta, items

def page_bale(html, name):
    """بله: پیش‌نمایش عمومی ble.ir/s/<channel>؛ کلاس‌ها هش‌شده‌اند، پس پیشوند نام کافی است."""
    soup = BeautifulSoup(html, "html.parser")
    h = soup.select_one("[class*=Profile_name]")
    meta = flat(h.get_text(" ", strip=True)) if h else ""
    items, day = [], None
    for el in soup.select("[class*=DateDivider_Date], [class*=MessageItem_messageWrapper]"):
        if "DateDivider" in " ".join(el.get("class") or []):
            day = iso_ts(el)  # بله فقط جداکننده‌ی روز دارد، نه ساعت هر پیام
            continue
        t = el.select_one("[class*=Text_text]")
        txt = flat((t or el).get_text(" ", strip=True))
        if len(txt) < 20:
            continue
        sid = el.get("data-sid") or ""
        items.append((txt, f"https://ble.ir/s/{name}/{sid}" if sid else "", day))
    return meta, items

def fetch_channel(key):
    plat, name = key.split(":", 1)
    url = PLATFORMS[plat].format(name)
    r = get(url)
    discover(r.text)
    meta, items = page_bale(r.text, name) if plat == "bale" else page_chan(r.text, {"tg": "tgme", "eitaa": "etme"}[plat])
    items = [(t, urljoin(url, link) if link.startswith("/") else link, ts) for t, link, ts in items]
    return meta, items, url

# ---------- پردازش ----------
def handle(text, link, ts, source, official, silent):
    text = flat(text)
    if not text:
        return 0
    t = norm(text)  # برای تطبیق کلیدواژه‌ها و اثر انگشت
    uid = hashlib.md5((link + text[:120]).encode()).hexdigest()
    cid = "c" + hashlib.md5(re.sub(r"\W+", "", t)[:180].encode()).hexdigest()  # حذف تکراری بین منابع
    known = not is_new(uid) or not is_new(cid)
    mark(uid); mark(cid)
    if known or silent:
        return 0
    if ts and time.time() - ts > MAX_AGE:
        return 0
    if not K2.search(t) or BAD.search(t) or not (K1.search(t) or official):
        return 0
    if not llm_ok(text[:160], text):
        return 0
    send_all(f"🔔 {text[:900]}\n\n📌 {source}" + (f"\n{link}" if link else ""))
    return 1

def run_source(s, seed):
    fn = fetch_rss if s["type"] == "rss" else (lambda u: fetch_html(u, s.get("selector")))
    n = 0
    for text, link, ts in fn(s["url"]):
        if link:
            discover(link)
        n += handle(text, link, ts, s["name"], s.get("official", False), seed)
    return n

def run_channel(key, status, seed):
    silent = seed or status == "new"  # اولین بررسی هر کانال: فقط ثبت، بدون ارسال
    meta, items, url = fetch_channel(key)
    any_k1 = bool(K1.search(norm(meta))) or any(K1.search(norm(t)) for t, _, _ in items)
    title = flat(meta.split("@")[0].replace("✔", ""))[:60]  # نام کانال برای نمایش در پیام
    n = 0
    for text, link, ts in items:
        n += handle(text, link or url, ts, f"{key} · {title}", False, silent)
    if status == "new":  # کانال بی‌ربط یا غیرقابل‌خواندن کنار گذاشته می‌شود
        db.execute("UPDATE channels SET status=? WHERE key=?", ("ok" if any_k1 else "no", key))
        commit()
    if not items:
        log.info("%s: پست متن‌دار استخراج نشد", key)
    return n

def load_manual():
    if not os.path.exists("channels.txt"):
        return
    for line in open("channels.txt", encoding="utf-8"):
        line = line.split("#")[0].strip()  # توضیح کنار خط نادیده گرفته می‌شود
        if line and ":" in line:
            plat, name = line.split(":", 1)
            if plat in PLATFORMS and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", name):
                db.execute("INSERT OR IGNORE INTO channels VALUES(?, 'manual')", (f"{plat}:{name.lower()}",))
            else:
                log.warning("خط نامعتبر در channels.txt: %s", line)
    commit()

def cycle(seed):
    try:
        sources = json.load(open("sources.json", encoding="utf-8"))
    except Exception as e:
        log.error("sources.json خوانده نشد: %s", e); return
    load_manual()
    for s in sources:
        if s.get("disabled"):
            continue
        try:
            log.info("%s: %d مورد جدید", s["name"], run_source(s, seed))
        except Exception as e:
            log.warning("%s خطا: %s", s.get("name"), str(e)[:160])
    for key, status in db.execute("SELECT key,status FROM channels WHERE status!='no'").fetchall():
        try:
            n = run_channel(key, status, seed)
            if n:
                log.info("%s: %d مورد جدید", key, n)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if status == "new" and code in (403, 404, 410):  # کانال مرده یا بسته: برای همیشه کنار گذاشته می‌شود
                db.execute("UPDATE channels SET status='no' WHERE key=?", (key,))
                commit()
                log.info("%s: کانال در دسترس نیست (%d)، حذف شد", key, code)
            else:
                log.warning("%s خطا: %s", key, str(e)[:160])
        except Exception as e:
            log.warning("%s خطا: %s", key, str(e)[:160])
    db.execute("DELETE FROM seen WHERE ts < ?", (time.time() - 60 * 86400,))  # بزرگ نمی‌شود
    commit()

def main():
    seed = db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
    if seed:
        log.info("اجرای اول: فقط ثبت موارد موجود، بدون ارسال")
    load_manual()  # تا شمارش کانال‌ها در همین لاگ درست باشد
    log.info("منابع: %d | کانال‌ها: %d | هر %d ثانیه", len(json.load(open("sources.json", encoding="utf-8"))), n_ch(), INTERVAL)
    while True:
        try:
            cycle(seed)
            if seed:
                send_all("✅ ایجنت فعال شد. از این لحظه موارد جدید ارسال می‌شود.")
                seed = False
        except Exception as e:
            log.error("چرخه: %s", e)
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
