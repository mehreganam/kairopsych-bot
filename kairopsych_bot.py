import datetime
import json
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ==============================================================================
# ربات کاملاً مستقل علمی ویژه کانال @kairopsychbackup (توسط ربات @kairopsych_bot)
# (۱۰۰٪ مجزا از پروژه سایکوپاد - اختصاصی برای ترجمه و تحلیل مقالات واقعی
#  PubMed / Scopus / Web of Science در دو محور «انتقال و انتقال متقابل» و «مدیریت استرس»)
# ==============================================================================

KAIRO_BOT_TOKEN = (
    os.environ.get("KAIROPSYCH_BOT_TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN")
    or ""
).strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()

TARGET_CHANNEL = "@kairopsychbackup"
BOT_USERNAME = "kairopsych_bot"

# فایل ذخیره تاریخچه مقالات ارسال‌شده برای جلوگیری ۱۰۰٪ از ارسال مقاله تکراری در اجراهای ساعتی
STATE_FILE = "kairopsych_state.json"

# کاراکترهای کنترل جهت خط در تلگرام:
# RLM (\u200f): راست‌چین کردن خطوط فارسی
# LRM (\u200e): چپ‌چین کردن خطوط انگلیسی، DOI و قرار دادن علامت @ دقیقاً در سمت چپ آیدی کانال
RLM = "\u200f"
LRM = "\u200e"

# محورهای پژوهشی تخصصی (نوبت‌بندی چرخشی بین «انتقال و انتقال متقابل» و «مدیریت استرس»)
RESEARCH_DOMAINS = [
    {
        "key": "transference_countertransference",
        "title_fa": "تحلیل انتقال و انتقال متقابل در روان‌درمانی (Transference & Countertransference)",
        "pubmed_queries": [
            '(("transference"[Title/Abstract] OR "countertransference"[Title/Abstract]) AND ("psychotherapy"[Title/Abstract] OR "psychoanalysis"[Title/Abstract] OR "therapeutic relationship"[Title/Abstract]))',
            '(("countertransference management"[Title/Abstract] OR "transference interpretation"[Title/Abstract] OR "transference-focused"[Title/Abstract] OR "therapist emotional response"[Title/Abstract]))',
            '(("projective identification"[Title/Abstract] OR "therapeutic alliance"[Title/Abstract]) AND ("transference"[Title/Abstract] OR "countertransference"[Title/Abstract]))',
        ],
        "eupmc_query": '(TITLE_ABS:"transference" OR TITLE_ABS:"countertransference") AND (TITLE_ABS:"psychotherapy" OR TITLE_ABS:"therapeutic") AND HAS_ABSTRACT:y',
    },
    {
        "key": "stress_management",
        "title_fa": "مدیریت علمی استرس و تنظیم زیستی-روانی تنش (Stress Management & Regulation)",
        "pubmed_queries": [
            '(("stress management"[Title/Abstract] OR "stress reduction"[Title/Abstract] OR "stress regulation"[Title/Abstract]) AND ("intervention"[Title/Abstract] OR "clinical trial"[Title/Abstract] OR "psychotherapy"[Title/Abstract]))',
            '(("mindfulness-based stress reduction"[Title/Abstract] OR "cognitive appraisal"[Title/Abstract] OR "coping strategies"[Title/Abstract]) AND ("physiological stress"[Title/Abstract] OR "cortisol"[Title/Abstract] OR "burnout"[Title/Abstract]))',
            '(("occupational stress"[Title/Abstract] OR "chronic stress"[Title/Abstract] OR "allostatic load"[Title/Abstract]) AND ("stress management"[Title/Abstract] OR "resilience"[Title/Abstract]))',
        ],
        "eupmc_query": '(TITLE_ABS:"stress management" OR TITLE_ABS:"stress reduction" OR TITLE_ABS:"stress regulation") AND (TITLE_ABS:"intervention" OR TITLE_ABS:"coping" OR TITLE_ABS:"cortisol") AND HAS_ABSTRACT:y',
    },
]


def clean_raw_html(raw_text):
    if not raw_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&zwnj;", "‌")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def load_kairo_state():
    """خواندن تاریخچه مقالات ارسال‌شده (PMID / DOI) و آخرین محور پژوهشی"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {
                        "sent_paper_ids": list(data.get("sent_paper_ids", [])),
                        "last_domain_index": int(
                            data.get("last_domain_index", -1)
                        ),
                    }
        except Exception as e:
            print(f"State read note: {e}")
    return {"sent_paper_ids": [], "last_domain_index": -1}


def save_kairo_state(sent_paper_ids, last_domain_index):
    """ذخیره شناسه‌های مقالات ارسال‌شده برای جلوگیری از تکرار"""
    payload = {
        "sent_paper_ids": list(sent_paper_ids)[-600:],
        "last_domain_index": int(last_domain_index),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"State save note: {e}")


def fetch_from_pubmed(domain_obj, sent_ids):
    """جستجوی مقالات واقعی و داوری‌شده در PubMed (نمایه‌شده در MEDLINE / Scopus / Web of Science)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KairoPsychAcademicBot/1.0)"
    }
    queries = domain_obj["pubmed_queries"].copy()
    random.shuffle(queries)

    for q in queries:
        try:
            encoded_q = urllib.parse.quote(q)
            sort_mode = random.choice(["pub_date", "relevance"])
            search_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                f"?db=pubmed&term={encoded_q}+AND+hasabstract&retmode=json&retmax=35&sort={sort_mode}"
            )
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                search_data = json.loads(resp.read().decode("utf-8"))

            id_list = search_data.get("esearchresult", {}).get("idlist", [])
            unseen_pmids = [
                pmid for pmid in id_list if f"PMID:{pmid}" not in sent_ids
            ]
            if not unseen_pmids:
                continue

            random.shuffle(unseen_pmids)
            for chosen_pmid in unseen_pmids[:6]:
                fetch_url = (
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                    f"?db=pubmed&id={chosen_pmid}&retmode=xml"
                )
                req_f = urllib.request.Request(fetch_url, headers=headers)
                with urllib.request.urlopen(req_f, timeout=15) as resp_f:
                    xml_bytes = resp_f.read()

                root = ET.fromstring(xml_bytes)
                art = root.find(".//PubmedArticle")
                if art is None:
                    continue

                title = (art.findtext(".//ArticleTitle") or "").strip()
                journal = (
                    art.findtext(".//Journal/Title")
                    or "Peer-Reviewed Psychology Journal"
                ).strip()
                pub_year = (
                    art.findtext(".//JournalIssue/PubDate/Year")
                    or art.findtext(".//JournalIssue/PubDate/MedlineDate")
                    or "2024"
                ).strip()[:4]

                # استخراج نام نویسندگان
                authors = []
                for au in art.findall(".//AuthorList/Author")[:3]:
                    last = (au.findtext("LastName") or "").strip()
                    fore = (au.findtext("Initials") or "").strip()
                    if last:
                        authors.append(f"{last} {fore}".strip())
                authors_str = (
                    ", ".join(authors) + (" et al." if len(authors) == 3 else "")
                    if authors
                    else "Research Team"
                )

                # استخراج چکیده کامل ساختاریافته
                abstract_parts = []
                for ab in art.findall(".//AbstractText"):
                    label = ab.attrib.get("Label", "")
                    txt = "".join(ab.itertext()).strip()
                    if txt:
                        abstract_parts.append(
                            f"{label}: {txt}" if label else txt
                        )
                abstract_full = "\n".join(abstract_parts)

                # استخراج DOI واقعی برای لینک مستقیم و قابل پیگیری
                doi = None
                for eid in art.findall(".//ArticleId"):
                    if eid.attrib.get("IdType") == "doi":
                        doi = (eid.text or "").strip()
                        break

                unique_key = f"DOI:{doi}" if doi else f"PMID:{chosen_pmid}"
                if unique_key in sent_ids or f"PMID:{chosen_pmid}" in sent_ids:
                    continue

                link = (
                    f"https://doi.org/{doi}"
                    if doi
                    else f"https://pubmed.ncbi.nlm.nih.gov/{chosen_pmid}/"
                )
                pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{chosen_pmid}/"

                if title and len(abstract_full) > 120:
                    return {
                        "unique_id": f"PMID:{chosen_pmid}",
                        "doi_id": unique_key,
                        "domain_fa": domain_obj["title_fa"],
                        "title_en": title,
                        "authors": authors_str,
                        "journal": journal,
                        "year": pub_year,
                        "pmid": chosen_pmid,
                        "doi": doi or f"PMID:{chosen_pmid}",
                        "abstract_en": abstract_full,
                        "primary_url": link,
                        "pubmed_url": pubmed_url,
                        "index_badge": "PubMed / MEDLINE / Scopus / Web of Science",
                    }
        except Exception as e:
            print(f"PubMed query note: {e}")

    return None


def fetch_from_europepmc_scopus_wos(domain_obj, sent_ids):
    """جستجوی مکمل در پایگاه Europe PMC / Crossref (پوشش‌دهنده ژورنال‌های Scopus و Web of Science)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KairoPsychAcademicBot/1.0)"
    }
    try:
        encoded_q = urllib.parse.quote(domain_obj["eupmc_query"])
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={encoded_q}&resultType=core&pageSize=25&format=json"
        )
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = data.get("resultList", {}).get("result", [])
        random.shuffle(results)
        for item in results:
            title = clean_raw_html(item.get("title") or "")
            abstract = clean_raw_html(item.get("abstractText") or "")
            doi = (item.get("doi") or "").strip()
            pmid = (item.get("pmid") or "").strip()
            journal = (
                item.get("journalInfo", {})
                .get("journal", {})
                .get("title")
                or item.get("journalTitle")
                or "International Peer-Reviewed Journal"
            )
            year = str(item.get("pubYear") or "2024")
            author_str = (item.get("authorString") or "Academic Authors").strip()

            uid = f"DOI:{doi}" if doi else f"PMID:{pmid}"
            if not title or len(abstract) < 120 or uid in sent_ids:
                continue

            primary_url = (
                f"https://doi.org/{doi}"
                if doi
                else (
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                    if pmid
                    else "https://europepmc.org"
                )
            )
            return {
                "unique_id": f"PMID:{pmid}" if pmid else uid,
                "doi_id": uid,
                "domain_fa": domain_obj["title_fa"],
                "title_en": title,
                "authors": author_str[:80],
                "journal": journal,
                "year": year,
                "pmid": pmid or "N/A",
                "doi": doi or pmid,
                "abstract_en": abstract,
                "primary_url": primary_url,
                "pubmed_url": (
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                    if pmid
                    else primary_url
                ),
                "index_badge": "Scopus / Web of Science / PubMed",
            }
    except Exception as e:
        print(f"EuropePMC/Scopus/WoS lookup note: {e}")

    return None


def get_available_gemini_models():
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        req = urllib.request.Request(list_url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            models = []
            for m in data.get("models", []):
                if "generateContent" in m.get(
                    "supportedGenerationMethods", []
                ) and "gemini" in m.get("name", ""):
                    models.append(m["name"].replace("models/", ""))
            return models
    except Exception:
        return []


def call_gemini(prompt):
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    preferred_models = [
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
        "gemini-2.0-flash",
        "gemini-2.5-flash",
    ]
    for m in get_available_gemini_models():
        if (
            m not in preferred_models
            and "tts" not in m
            and "image" not in m
            and "embedding" not in m
        ):
            preferred_models.append(m)

    last_error = None
    for model_name in preferred_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return (
                    res_data["candidates"][0]["content"]["parts"][0]["text"]
                    .strip()
                )
        except Exception as e:
            last_error = e
    raise RuntimeError(f"All Gemini models failed: {last_error}")


def telegram_api_request(method, payload):
    url = f"https://api.telegram.org/bot{KAIRO_BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"Telegram API Error [{e.code}]: {err_body}")
        raise


def send_kairo_post(text, buttons):
    for mode in ["HTML", None]:
        out_text = text if mode == "HTML" else clean_raw_html(text)
        payload = {
            "chat_id": TARGET_CHANNEL,
            "text": out_text,
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": buttons},
        }
        if mode:
            payload["parse_mode"] = mode
        try:
            return telegram_api_request("sendMessage", payload)
        except urllib.error.HTTPError as e:
            if e.code == 403 or mode is None:
                raise


def run_hourly_kairo_academic_post():
    """انتخاب چرخشی موضوع (انتقال/انتقال متقابل یا مدیریت استرس)، استخراج مقاله واقعی، ترجمه تخصصی فارسی و ارسال به @kairopsychbackup"""
    state = load_kairo_state()
    sent_ids = set(state["sent_paper_ids"])
    next_domain_idx = (state["last_domain_index"] + 1) % len(RESEARCH_DOMAINS)
    chosen_domain = RESEARCH_DOMAINS[next_domain_idx]

    print(f"🔎 Searching real paper for domain: {chosen_domain['title_fa']}")
    paper = fetch_from_pubmed(chosen_domain, sent_ids)
    if not paper:
        paper = fetch_from_europepmc_scopus_wos(chosen_domain, sent_ids)

    if not paper:
        # در صورتی که شبکه موقتاً پاسخ نداد، چرخش روی محور دوم
        alt_idx = (next_domain_idx + 1) % len(RESEARCH_DOMAINS)
        paper = fetch_from_pubmed(RESEARCH_DOMAINS[alt_idx], sent_ids)
        if paper:
            next_domain_idx = alt_idx

    if not paper:
        raise RuntimeError(
            "Could not fetch a new academic paper from PubMed/Scopus/WoS APIs."
        )

    prompt = f"""
شما مترجم و پژوهشگر ارشد روان‌شناسی بالینی و روان‌درمانی هستید.
مقاله علمی و داوری‌شده زیر از پایگاه‌های معتبر جهانی ({paper['index_badge']}) در حوزه «{paper['domain_fa']}» استخراج شده است:

Original Title: {paper['title_en']}
Authors: {paper['authors']}
Journal & Year: {paper['journal']} ({paper['year']})
Identifier (DOI / PMID): {paper['doi']}
Abstract:
{paper['abstract_en']}

وظیفه شما:
یک پست علمی، دقیق، وفادار به متن اصلی و در عین حال روان و ساختاریافته به زبان فارسی برای کانال تلگرام آماده کنید که شامل بخش‌های زیر باشد (بین هر بخش حتماً یک خط خالی کامل فاصله بگذارید):

1. خط اول: ترجمه دقیق و علمیِ عنوان مقاله به فارسی با تگ <b>📄 [ترجمه فارسی عنوان مقاله]</b>
2. بخش «🎯 <b>مسئله و هدف پژوهش:</b>» در ۲ تا ۳ جمله روشن.
3. بخش «🔬 <b>روش و یافته‌های کلیدی مقاله:</b>» ترجمه دقیق نتایج و مکانیسم‌های کشف‌شده در پژوهش در ۳ تا ۴ جمله.
4. بخش «💡 <b>کاربرد بالینی و عملی (Clinical Implications):</b>» این یافته چه کمکی به درمانگران، مراجعان یا مدیریت عملی این پدیده می‌کند؟ (در ۲ تا ۳ جمله).

قوانین بسیار مهم:
- هیچ‌چیز از خودتان اضافه یا تحریف نکنید؛ ترجمه و سنتز باید ۱۰۰٪ وفادار به چکیده مقاله اصلی باشد.
- در انتهای متن مشخصات انگلیسی مقاله یا آیدی کانال را ننویسید (چون بخش شناسنامه کتابشناختی و لینک قابل پیگیری به صورت خودکار در فوتر اضافه می‌شود).
- فقط از تگ‌های مجاز HTML تلگرام (<b> و <i>) استفاده کنید و از علامت ** یا # خودداری کنید.
"""
    persian_translation = call_gemini(prompt).strip()

    # ساخت فوتر کتابشناختی کاملاً قابل پیگیری (عناوین فارسی راست‌چین و مشخصات انگلیسی/DOI/آیدی چپ‌چین)
    post_text = (
        f"{RLM}🏛 <b>حوزه پژوهشی: {paper['domain_fa']}</b>\n\n"
        f"{persian_translation}\n\n"
        f"{RLM}━━━━━━━━━━━━━━\n"
        f"{RLM}📑 <b>شناسنامه و مشخصات قابل پیگیری مقاله (Original Reference):</b>\n"
        f"{LRM}• <b>Title:</b> <i>{paper['title_en']}</i>\n"
        f"{LRM}• <b>Authors:</b> {paper['authors']}\n"
        f"{LRM}• <b>Journal:</b> {paper['journal']} ({paper['year']})\n"
        f"{LRM}• <b>Database:</b> {paper['index_badge']}\n"
        f"{LRM}• <b>DOI / ID:</b> {paper['doi']}\n\n"
        f"{LRM}📢 {LRM}{TARGET_CHANNEL}"
    )

    buttons = [
        [{
            "text": "🔬 مشاهده منبع اصلی مقاله (DOI / PubMed)",
            "url": paper["primary_url"],
        }]
    ]

    res = send_kairo_post(post_text, buttons)

    # ثبت در حافظه برای عدم تکرار در ساعات بعدی
    sent_ids.add(paper["unique_id"])
    sent_ids.add(paper["doi_id"])
    save_kairo_state(list(sent_ids), next_domain_idx)

    print(
        f"✅ Sent verifiable paper ({paper['doi']}) to {TARGET_CHANNEL} via"
        f" @{BOT_USERNAME}"
    )
    return res


if __name__ == "__main__":
    if not KAIRO_BOT_TOKEN or not GEMINI_API_KEY:
        raise ValueError(
            "KAIROPSYCH_BOT_TOKEN (or TELEGRAM_BOT_TOKEN) and GEMINI_API_KEY"
            " must be set."
        )

    run_hourly_kairo_academic_post()
