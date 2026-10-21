"""shops.json の住所から座標を取得する(国土地理院の住所検索API、キー不要)。

使い方:
  python tools/geocode.py            座標が未設定で住所がある店だけ取得して書き込む
  python tools/geocode.py --check    既存の座標も住所と照合して、ずれが大きい店を表示する(書き換えない)
  python tools/geocode.py --dry-run  取得結果を表示するだけで書き込まない
"""
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SHOPS = Path(__file__).resolve().parent.parent / "shops.json"
API = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="
WARN_KM = 0.3


def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def lookup(addr):
    with urllib.request.urlopen(API + urllib.parse.quote(addr), timeout=15) as r:
        hits = json.load(r)
    if not hits:
        return None
    lng, lat = hits[0]["geometry"]["coordinates"]
    return lat, lng, hits[0]["properties"]["title"]


def main():
    check = "--check" in sys.argv
    dry = "--dry-run" in sys.argv
    shops = json.loads(SHOPS.read_text(encoding="utf-8"))
    changed = 0
    for s in shops:
        if not s.get("addr"):
            print(f"- {s['name']}: 住所なし(スキップ)")
            continue
        has = s.get("lat") is not None
        if has and not check:
            continue
        try:
            res = lookup(s["addr"])
        except Exception as e:
            print(f"! {s['name']}: 取得失敗 ({e})")
            continue
        time.sleep(1)
        if res is None:
            print(f"! {s['name']}: 住所が見つかりません ({s['addr']})")
            continue
        lat, lng, title = res
        precise = "番地" in title or ("番" in title and "号" in title)
        if has:
            d = km(s["lat"], s["lng"], lat, lng)
            mark = "要確認" if d > WARN_KM else "OK"
            print(f"{mark} {s['name']}: 登録済みとの差 {d:.2f}km -> {title}")
            continue
        print(f"+ {s['name']}: {lat:.6f}, {lng:.6f} <- {title}" + ("" if precise else "  (番地まで特定できず、町名レベル)"))
        if not dry:
            s["lat"], s["lng"] = round(lat, 6), round(lng, 6)
            s["pos"] = "town"
            changed += 1
    if changed:
        SHOPS.write_text(json.dumps(shops, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{changed}軒の座標を shops.json に書き込みました。")


if __name__ == "__main__":
    main()
