#!/usr/bin/env python3
"""掲載済みの店の「閉店」と「営業時間」を、食べログの店ページで点検する。

使い方:
  python3 tools/check_shops.py --region 東北 --limit 60          # 点検だけ(shops.json は変えない)
  python3 tools/check_shops.py --region 東北 --limit 60 --apply  # 閉店の店を外し、営業時間の違いを直す
地域: 北海道 東北 関東 中部 近畿 中国四国 九州沖縄 直系(全国) すべて
食べログのURLが src にない店は、点検できないので一覧に出す(自分で検索して確かめる)。
"""
import argparse, json, re, subprocess, sys, time, html as H
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGIONS = {
    "北海道": "北海道",
    "東北": "青森県 岩手県 宮城県 秋田県 山形県 福島県",
    "関東": "茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県",
    "中部": "新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県",
    "近畿": "三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県",
    "中国四国": "鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県",
    "九州沖縄": "福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県",
}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
JP = dict(zip(DAYS, "月火水木金土日"))


def pref_of(addr):
    m = re.match(r"(北海道|東京都|京都府|大阪府|.{2,3}?県)", addr or "")
    return m.group(1) if m else ""


def region_of(shop):
    p = pref_of(shop.get("addr", ""))
    for r, ps in REGIONS.items():
        if p in ps.split():
            return r
    return "直系(全国)"


def curl(url):
    r = subprocess.run(["curl", "-sS", "-m", "40", "-L", "-A", "Mozilla/5.0", url], capture_output=True)
    return r.stdout.decode("utf-8", "ignore")


def to_text(h):
    t = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", h, flags=re.S)
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", t)))


def t24(s):
    m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", s.strip())
    if not m:
        return None
    return int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0), int(m.group(2))


def parse_hours(txt):
    """食べログ(英語版)の営業時間の文字列 → (sched, closed)。全曜日を読めないときは None。"""
    txt = txt.split("■")[0]
    segs = re.findall(
        r"((?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Public Holiday)(?:, )?)+)\s+((?:Closed|(?:\d{1,2}:\d{2} [AP]M - \d{1,2}:\d{2} [AP]M(?: L\.O\. [^0-9A-Za-z]*\d{1,2}:\d{2} [AP]M)?(?: Food \d{1,2}:\d{2} [AP]M)?\s*)+))",
        txt)
    if not segs:
        return None
    sched, covered, closed = [], set(), []
    for dayspec, body in segs:
        days = [d for d in DAYS if re.search(r"\b" + d + r"\b", dayspec)]
        hol = "Public Holiday" in dayspec
        if not days and not hol:
            continue
        jd = "".join(JP[d] for d in days) + ("祝" if hol else "")
        if "Closed" in body:
            sched.append({"d": jd, "t": []})
            closed.append(jd)
        else:
            ts = []
            for a, b in re.findall(r"(\d{1,2}:\d{2} [AP]M) - (\d{1,2}:\d{2} [AP]M)", body):
                x, y = t24(a), t24(b)
                if not x or not y:
                    return None
                s, e = x[0] * 60 + x[1], y[0] * 60 + y[1]
                if e <= s:
                    e += 1440
                ts.append("%02d:%02d-%02d:%02d" % (x[0], x[1], e // 60, e % 60))
            if not ts:
                return None
            sched.append({"d": jd, "t": ts})
        covered |= set(days)
    if covered != set(DAYS):
        return None
    return sched, ("".join(closed) if closed else "なし")


def tabelog_url(shop):
    for s in shop.get("src", []):
        m = re.match(r"https://tabelog\.com/(?:en/)?([a-z]+/A\d+/A\d+/\d{7,8})/?", s["u"])
        if m:
            return "https://tabelog.com/en/" + m.group(1) + "/"
    return None


def inspect(url):
    t = to_text(curl(url))
    if "Restaurant name" not in t:
        return dict(status="NOFETCH")
    head = t[:900]
    if re.search(r"(?i)permanently closed|This restaurant is closed|閉店", head):
        status = "CLOSED"
    elif re.search(r"(?i)listing is on hold|on hold|掲載保留|relocated|移転", head):
        status = "HOLD"
    else:
        status = "OK"
    hrs = re.search(r"Business hours (.*?) (?:Average price|Payment|Hours and closed)", t)
    visits = re.findall(r"Visited on ([A-Za-z]+, \d{4})", t)[:1]
    return dict(status=status, hours=parse_hours(hrs.group(1)[:400]) if hrs else None, visit=visits[0] if visits else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="すべて")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    path = ROOT / "shops.json"
    shops = json.loads(path.read_text(encoding="utf-8"))
    pool = [s for s in shops if a.region == "すべて" or region_of(s) == a.region]
    pool = pool[a.offset:a.offset + a.limit]
    no_url, closed_ids, hold, changed = [], [], [], []
    for s in pool:
        u = tabelog_url(s)
        if not u:
            no_url.append(s["name"])
            continue
        r = inspect(u)
        time.sleep(1)
        if r["status"] == "NOFETCH":
            print(f"取得できず  {s['id']}  {s['name']}")
            continue
        if r["status"] == "CLOSED":
            closed_ids.append(s["id"])
            print(f"閉店        {s['id']}  {s['name']}  {u}")
        elif r["status"] == "HOLD":
            hold.append(s["id"])
            print(f"掲載保留    {s['id']}  {s['name']}  {u}  (閉店・移転の可能性。ほかの出典でも確かめる)")
        if r["status"] == "OK" and r["hours"]:
            sched, closed = r["hours"]
            if sched != s.get("sched") or closed != s.get("closed"):
                changed.append((s["id"], sched, closed))
                print(f"営業時間差  {s['id']}  {s['name']}  定休日 {s.get('closed')} → {closed}")
    print(f"\n点検 {len(pool)}軒 / 閉店 {len(closed_ids)} / 掲載保留 {len(hold)} / 営業時間の違い {len(changed)} / 食べログURLなし {len(no_url)}")
    if no_url:
        print("食べログURLなし:", "、".join(no_url[:40]))
    if a.apply:
        by = {s["id"]: s for s in shops}
        for i, sched, closed in changed:
            by[i]["sched"], by[i]["closed"] = sched, closed
        shops = [s for s in shops if s["id"] not in set(closed_ids)]
        path.write_text(json.dumps(shops, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"shops.json を更新: 外した店 {len(closed_ids)} / 営業時間を直した店 {len(changed)}")


if __name__ == "__main__":
    sys.exit(main())
