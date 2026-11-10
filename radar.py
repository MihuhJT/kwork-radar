"""Kwork order radar: polls the project exchange, Qwen scores each new project.

Usage:
  python radar.py once      # one pass, print results
  python radar.py loop      # poll every INTERVAL seconds forever
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from tg_report import api, load_env

BASE = Path(__file__).parent
SEEN_FILE = BASE / "radar_seen.json"
OUT_FILE = BASE / "radar_found.jsonl"

KWORK_URL = "https://kwork.ru/projects?c=11"  # "Разработка и IT"
QWEN_URL = "http://localhost:1234/v1/chat/completions"
QWEN_MODEL = "qwen/qwen3.5-9b"
INTERVAL = 300
MAX_AGE_HOURS = 3      # older projects are already crowded
MAX_RESPONSES = 20     # skip projects with more offers than this
MIN_SCORE = 7
MAX_BUDGET = 15000     # bigger projects go to sellers with reviews

PROFILE = """Фрилансер-новичок на Кворке, без отзывов. Умеет: парсеры сайтов (Python, выгрузка в Excel/Google Sheets),
чат-боты для Telegram/VK/MAX, простые лендинги (HTML/Tilda), мелкие правки сайтов на WordPress,
ускорение сайта (PageSpeed), базовое SEO. НЕ делает: мобильные приложения, 1С, игры, большие CRM и маркетплейсы,
дизайн в Figma с нуля, серые схемы (накрутки, обход защит, спам, чужие аккаунты)."""

PROMPT = """{profile}

Проект с биржи:
Название: {name}
Бюджет: {price} ₽
Описание: {desc}

Оцени, стоит ли этому фрилансеру откликаться (шанс получить и сделать заказ за 1-3 дня).
Будь строгим. Ставь 7+ только если задача ПРЯМО из списка "Умеет" и понятна.
Ставь 0-3, если нужна технология не из списка (1С, Битрикс, Android, iOS, тестирование/QA, дизайн, видео, тексты)
или это серая схема. Сомневаешься - 4-5.
Ответь ТОЛЬКО JSON: {{"score": 0-10, "why": "одна короткая фраза"}}"""


def fetch_projects():
    html = requests.get(KWORK_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    i = html.index("stateData=") + len("stateData=")
    state, _ = json.JSONDecoder().raw_decode(html[i:])
    return find_wants(state) or []


def find_wants(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("wants"), list) and obj["wants"]:
            return obj["wants"]
        obj = obj.values()
    if isinstance(obj, (list, type({}.values()))):
        for v in obj:
            found = find_wants(v)
            if found:
                return found
    return None


def ask_qwen(project):
    desc = re.sub(r"<[^>]+>", " ", project.get("description") or "")[:1500]
    msg = PROMPT.format(profile=PROFILE, name=project["name"],
                        price=int(float(project["priceLimit"])), desc=desc)
    r = requests.post(QWEN_URL, timeout=180, json={
        "model": QWEN_MODEL,
        "messages": [{"role": "user", "content": msg}],
        "temperature": 0.2,
        "reasoning_effort": "none",  # qwen3.5 thinks ~80s per project otherwise
    })
    text = r.json()["choices"][0]["message"]["content"]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0))
    except (AttributeError, json.JSONDecodeError):
        return {"score": 0, "why": "qwen answer unparsed: " + text[:100]}


def notify(item):
    env = load_env()
    text = (f"🎯 {item['score']}/10 · {item['name']}\n"
            f"{item['price']} ₽ · {item['offers']} откл. · {item['age_min']} мин назад\n"
            f"{item['why']}\n{item['url']}")
    api(env["TG_REPORT_BOT_TOKEN"], "sendMessage", chat_id=env["TG_REPORT_CHAT_ID"], text=text)


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def run_once():
    seen = load_seen()
    found = []
    for p in fetch_projects():
        pid = p["id"]
        if pid in seen:
            continue
        seen.add(pid)
        age_h = (datetime.now() - datetime.fromisoformat(p["date_create"])).total_seconds() / 3600
        if (age_h > MAX_AGE_HOURS or int(p.get("kwork_count") or 0) > MAX_RESPONSES
                or float(p["priceLimit"]) > MAX_BUDGET):
            continue
        verdict = ask_qwen(p)
        item = {
            "id": pid, "name": p["name"], "price": int(float(p["priceLimit"])),
            "offers": p.get("kwork_count"), "age_min": int(age_h * 60),
            "score": verdict.get("score", 0), "why": verdict.get("why", ""),
            "url": f"https://kwork.ru/projects/{pid}",
        }
        print(f"[{item['score']}] {item['name']} — {item['price']} ₽, "
              f"{item['offers']} откл., {item['age_min']} мин — {item['why']}")
        if item["score"] >= MIN_SCORE:
            found.append(item)
            with OUT_FILE.open("a") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            notify(item)
    SEEN_FILE.write_text(json.dumps(sorted(seen)))
    return found


if __name__ == "__main__":
    if sys.argv[1:] == ["loop"]:
        while True:
            try:
                run_once()
            except Exception as e:  # keep polling on network hiccups
                print("error:", e)
            time.sleep(INTERVAL)
    else:
        run_once()
