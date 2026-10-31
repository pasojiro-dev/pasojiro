"""shops.json が、掲載の基準(criteria.html)と形式のルールを守っているか検査する。

使い方:  python tools/validate.py
エラーがあれば、内容を表示して終了コード1で終わる。push の前に必ず通すこと。
"""
import json
import math
import re
import sys
from pathlib import Path

SHOPS = Path(__file__).resolve().parent.parent / "shops.json"
KINDS = {"direct", "dokuritsu", "inspire"}
FEATS = {"太麺", "野菜増し", "ニンニク", "アブラ", "塊の豚", "濃いスープ", "量が多い"}
REQUIRED = ["id", "name", "group", "kind", "area", "addr", "lat", "lng", "pos", "jiro", "memo"]
DAYS = set("日月火水木金土")
ORDER = "月火水木金土日"


def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def jiro_for(n):
    return 4 if n >= 6 else 3 if n >= 4 else 2 if n == 3 else None


def days_of(d):
    s, prev, i = set(), None, 0
    while i < len(d):
        c = d[i]
        if c == "〜":
            i += 1
            e = d[i]
            for k in range(ORDER.index(prev), ORDER.index(e) + 1):
                s.add(ORDER[k])
            prev = e
        elif c == "祝":
            pass
        else:
            s.add(c)
            prev = c
        i += 1
    return s


def main():
    errs, warns = [], []
    try:
        shops = json.loads(SHOPS.read_text(encoding="utf-8"))
    except Exception as e:
        print("shops.json を読めません:", e)
        return 1
    seen = set()
    for s in shops:
        n = s.get("name", "?")
        e = lambda m: errs.append(f"{n}: {m}")
        for k in REQUIRED:
            if k not in s:
                e(f"必須項目 {k} がありません")
        if errs and any(x.startswith(n + ": 必須") for x in errs):
            continue
        if not re.fullmatch(r"[a-z0-9-]{1,40}", s["id"]):
            e("id は半角小文字・数字・ハイフンで40字まで")
        if s["id"] in seen:
            e("id が重複しています")
        seen.add(s["id"])
        if s["kind"] not in KINDS:
            e(f"kind が不正です: {s['kind']}")
        if not s["addr"]:
            e("住所がありません")
        if not (isinstance(s["lat"], (int, float)) and isinstance(s["lng"], (int, float))):
            e("座標がありません")
        elif not (24 <= s["lat"] <= 46 and 122 <= s["lng"] <= 146):
            e(f"座標が日本の範囲外です: {s['lat']}, {s['lng']}")
        if not isinstance(s["jiro"], int) or not 1 <= s["jiro"] <= 5:
            e("jiro は1〜5の整数")
        if s["kind"] == "direct":
            if s["jiro"] != 5:
                e("直系の二郎度は5")
        else:
            feat = s.get("feat", [])
            src = s.get("src", [])
            exc = bool(s.get("exception"))
            bad = [f for f in feat if f not in FEATS]
            if bad:
                e(f"特徴が決められた7つにありません: {bad}")
            if len(set(feat)) != len(feat):
                e("特徴が重複しています")
            for x in src:
                if not (isinstance(x, dict) and x.get("t") and str(x.get("u", "")).startswith("https://")):
                    e("出典は {t, u(https)} の形で書く")
            if not exc:
                if len(src) < 2:
                    e("出典が2つ未満です(例外の理由がない)")
                if len(set(feat)) < 3:
                    e("確認できた特徴が3つ未満です(例外の理由がない)")
                want = jiro_for(len(set(feat)))
                if want and s["jiro"] != want:
                    e(f"二郎度が基準と合いません(特徴{len(set(feat))}つなら{want}、実際は{s['jiro']})")
            elif not s["jiro"] <= 3:
                warns.append(f"{n}: 例外の店の二郎度が高めです")
        sched = s.get("sched")
        if sched is not None:
            cov = set()
            for r in sched:
                try:
                    cov |= days_of(r["d"])
                except Exception:
                    e(f"sched の曜日が読めません: {r.get('d')}")
                t = r.get("t")
                if t is not None:
                    for x in t:
                        if not re.fullmatch(r"\d{2}:\d{2}-(\d{2}:\d{2}|\?)", x):
                            e(f"sched の時間の形式が不正です: {x}")
            if cov != DAYS:
                e(f"sched が全曜日を覆っていません(不足: {''.join(sorted(DAYS - cov))})")
        else:
            warns.append(f"{n}: 営業時間(sched)がありません")
    # 近すぎる店・同名店
    for i, a in enumerate(shops):
        for b in shops[i + 1:]:
            try:
                d = km(a["lat"], a["lng"], b["lat"], b["lng"])
            except Exception:
                continue
            if d < 0.03 and a.get("kind") == b.get("kind"):
                warns.append(f"{a['name']} と {b['name']} が30m以内にあります(重複の疑い)")
        if sum(1 for b in shops if b.get("name") == a.get("name")) > 1:
            warns.append(f"同名の店があります: {a.get('name')}")
    print(f"{len(shops)}軒を検査: エラー{len(errs)}件, 注意{len(warns)}件")
    for m in errs:
        print("  ERROR", m)
    for m in sorted(set(warns)):
        print("  WARN ", m)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
