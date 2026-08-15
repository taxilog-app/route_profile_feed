#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""営業圏プロファイル（電話帳）を1枚にまとめて配る。

【何を配っているか】
天気の地点番号・地図の初期中心・路線の名簿・運行情報のURL。アプリはこれを見て
「どこに何を聞きに行くか」を決める。時刻表やイベントのような"荷物"ではなく
**問い合わせ先の電話帳**なので、これが無い営業圏は電車タブ・天気チップが
そもそも画面に出ない。

【なぜハブから配るか】
2026-08-15 まではアプリに焼き込まれた手書き定数37個だった。営業圏を1つ足す
たびにアプリを作り直してストアに出し直すことになり、残り45圏の足かせだった。
ここに JSON を1枚置けば届くようにする（アプリ更新なし）。
📄 route_timer_app/docs/指示書_営業圏プロファイルの配信化_2026-08-15.md

【使い方】
    python3 build.py            # src/*.json を検査して out/profiles.json を作る
    python3 build.py --check    # 検査だけ（配信物は作らない）

🔴 検査に1つでも引っかかったら**非0で止まる＝配信しない**。
   中途半端に配るより、前のまま置いておく方が安全（運転手の画面が壊れないため）。
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

VERSION = 1

# Yahoo!路線情報のエリア番号（2026-08-05 実測）。
# ⚠️ 北陸信越（新潟・富山・石川・福井・長野）は「中部」、沖縄は「九州」に入る。
YAHOO_AREAS = {"2": "北海道", "3": "東北", "4": "関東", "5": "中部",
               "6": "近畿", "7": "九州", "8": "中国", "9": "四国"}

# 気象庁の府県予報区コード（末尾4桁が0000）。47都道府県ぶん。
JMA_RE = re.compile(r"^\d{6}$")

# 日本の範囲（離島を含む大まかな箱）。ここを外れたら座標の打ち間違い。
LAT_RANGE = (24.0, 46.0)
LNG_RANGE = (122.0, 154.0)

REQUIRED = ("key", "label", "weatherAreaCode", "home", "boundaryDirs")


def load_area_roster():
    """アプリの運賃プリセットにある boundaryDir の名簿（83営業圏）。

    🔴 ここと突き合わせるのが検査の要。アプリが知らない営業圏キーを書いても
       誰にも届かないが、配信自体は成功してしまう＝静かな失敗になる。
    """
    app = os.path.expanduser("~/Developer/route_timer_app")
    dirs = set()
    for name in ("lib/models/fare_preset.dart",
                 "lib/models/fare_preset_areas.g.dart"):
        path = os.path.join(app, name)
        if not os.path.exists(path):
            return None  # アプリが手元に無い環境（CI）では省略
        with open(path, encoding="utf-8") as f:
            dirs |= set(re.findall(r"boundaryDir:\s*'([^']+)'", f.read()))
    return dirs or None


def check(profiles, roster):
    errs = []

    def bad(key, msg):
        errs.append(f"{key}: {msg}")

    seen_keys = {}
    seen_dirs = {}
    for p in profiles:
        key = p.get("key") or "(keyなし)"

        for r in REQUIRED:
            if not p.get(r):
                bad(key, f"必須項目 {r} が無い")
        if errs and key == "(keyなし)":
            continue

        if key in seen_keys:
            bad(key, "key が重複している")
        seen_keys[key] = True

        w = p.get("weatherAreaCode", "")
        if not JMA_RE.match(w):
            bad(key, f"天気の地点番号が6桁でない（{w}）")

        y = p.get("yahooAreaCode", "7")
        if y not in YAHOO_AREAS:
            bad(key, f"Yahooのエリア番号が 2〜9 でない（{y}）")

        for label, area in [("home", p.get("home"))] + \
                [("adjacent", a) for a in (p.get("adjacent") or [])]:
            if not isinstance(area, dict):
                bad(key, f"{label} の形が違う")
                continue
            lat, lng = area.get("lat"), area.get("lng")
            if not (isinstance(lat, (int, float))
                    and LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
                bad(key, f"{label} の緯度が日本の範囲外（{lat}）")
            if not (isinstance(lng, (int, float))
                    and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
                bad(key, f"{label} の経度が日本の範囲外（{lng}）")
            if not (isinstance(area.get("radiusKm"), (int, float))
                    and area["radiusKm"] > 0):
                bad(key, f"{label} の半径が正の数でない")

        for line in (p.get("individualLines") or []):
            if not (isinstance(line, list) and len(line) == 3):
                bad(key, f"運行情報の行は[URL,表示名,事業者]の3つ組（{line}）")
                continue
            if not line[0].startswith("https://transit.yahoo.co.jp/diainfo/"):
                bad(key, f"運行情報のURLの形が違う（{line[0]}）")

        for d in (p.get("boundaryDirs") or []):
            if d in seen_dirs:
                bad(key, f"営業圏キー {d} が {seen_dirs[d]} と重複している")
            seen_dirs[d] = key
            if roster is not None and d not in roster:
                bad(key, f"アプリが知らない営業圏キー {d}（誰にも届かない）")

    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="検査だけして配信物は作らない")
    args = ap.parse_args()

    files = sorted(glob.glob("src/*.json"))
    if not files:
        sys.exit("🔴 src/ に1件も無い")

    profiles = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                profiles.append(json.load(fh))
        except Exception as e:                                # noqa: BLE001
            sys.exit(f"🔴 {f} がJSONとして読めない: {e}")

    roster = load_area_roster()
    print(f"営業圏プロファイルの検査（{len(profiles)}圏"
          f"{'・アプリの名簿と突き合わせ' if roster else '・名簿なしで簡易'}）")

    errs = check(profiles, roster)
    if errs:
        print(f"\n🔴 {len(errs)}件の問題があります → 配信しません", file=sys.stderr)
        for e in errs:
            print(f"   - {e}", file=sys.stderr)
        sys.exit(1)

    covered = sum(len(p.get("boundaryDirs") or []) for p in profiles)
    total = len(roster) if roster else "?"
    print(f"🟢 検査ぜんぶ通過。{len(profiles)}圏ぶんで営業圏 {covered}/{total} を担当")

    if args.check:
        return

    profiles.sort(key=lambda p: p["key"])
    os.makedirs("out", exist_ok=True)
    with open("out/profiles.json", "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "profiles": profiles}, f,
                  ensure_ascii=False, separators=(",", ":"))
    kb = os.path.getsize("out/profiles.json") / 1024
    print(f"📝 out/profiles.json（{kb:.1f}KB）")


if __name__ == "__main__":
    main()
