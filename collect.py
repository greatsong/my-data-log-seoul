"""collect.py - 서울 실시간 인구 혼잡도를 한 번 받아 CSV에 남긴다.

프롬프트 10-1(한 번 받아 파일에 남기기)과 프롬프트 10-4(장소 목록 전체로 넓히기)를
독자가 AI에게 그대로 넣었을 때 받을 법한 결과를 재현한 것이다.
- 실행: SEOUL_KEY=... python collect.py            (강남역 한 곳)
        SEOUL_KEY=... COLLECT_ALL=1 python collect.py  (목록 전체)
"""
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

KST = timezone(timedelta(hours=9))
BASE = "http://openapi.seoul.go.kr:8088"
AREA_CSV = Path("data/seoul_area_all.csv")
LOG_CSV = Path("data/seoul_congestion_log.csv")
COLUMNS = ["수집시각", "기준시각", "코드", "지역명", "혼잡도", "인구min", "인구max", "위도", "경도"]
TIMEOUT = 10          # 요청 하나에 허용하는 시간(초)
RETRY = 2             # 일시적인 실패를 다시 시도하는 횟수
SLEEP = float(os.environ.get("REQUEST_INTERVAL", "0.3"))   # 요청 사이의 간격(초)
TARGET_NAME = "강남역"


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S%z")


def read_key() -> str:
    key = os.environ.get("SEOUL_KEY", "").strip()
    if not key:
        print("SEOUL_KEY 환경변수가 없습니다. 깃헙 액션의 Secrets 또는 실행 환경에 "
              "서울 열린데이터광장에서 발급받은 키를 SEOUL_KEY라는 이름으로 넣어 주세요.")
        sys.exit(1)
    return key


def load_areas() -> pd.DataFrame:
    areas = pd.read_csv(AREA_CSV)
    areas.columns = [c.strip().lstrip("﻿") for c in areas.columns]
    need = {"코드", "지역명", "위도", "경도"}
    missing = need - set(areas.columns)
    if missing:
        print(f"{AREA_CSV}에 필요한 열이 없습니다: {sorted(missing)} / 지금 열: {list(areas.columns)}")
        sys.exit(1)
    if os.environ.get("COLLECT_ALL", "") == "1":
        return areas
    picked = areas[areas["지역명"] == TARGET_NAME]
    if picked.empty:
        print(f"장소 목록에서 '{TARGET_NAME}'을 찾지 못했습니다. 지역명 열을 확인해 주세요.")
        sys.exit(1)
    return picked


def fetch_one(key: str, code: str):
    """한 장소를 요청한다. 성공하면 API 응답 항목, 실패하면 None."""
    url = f"{BASE}/{key}/json/citydata_ppltn/1/5/{code}"
    for attempt in range(RETRY + 1):
        try:
            res = requests.get(url, timeout=TIMEOUT)
            res.raise_for_status()
            body = res.json()
            rows = body.get("SeoulRtd.citydata_ppltn")
            if not rows:
                result = body.get("RESULT", {})
                # 키가 들어간 주소는 출력하지 않는다
                print(f"  [{code}] 응답에 데이터가 없습니다: {result.get('RESULT.CODE')} {result.get('RESULT.MESSAGE')}")
                return None
            return rows[0]
        except (requests.RequestException, ValueError) as err:
            if attempt < RETRY:
                time.sleep(1 + attempt)
                continue
            print(f"  [{code}] 요청 실패({type(err).__name__}) - 이 장소는 건너뜁니다.")
            return None
    return None


def to_row(item: dict, area: dict, collected: str):
    """API 응답을 저장할 한 행으로 바꾼다. 값이 비어 있으면 None을 돌려준다."""
    level = (item.get("AREA_CONGEST_LVL") or "").strip()
    pmin, pmax = item.get("AREA_PPLTN_MIN"), item.get("AREA_PPLTN_MAX")
    if not level or pmin in (None, "") or pmax in (None, ""):
        return None                      # 실패한 값을 0명이나 '여유'로 채우지 않는다
    base = (item.get("PPLTN_TIME") or "").strip()
    if base:
        # API는 분 단위까지 준다. 저장 형식을 수집시각과 맞추되 없는 초를 지어내지 않도록 00으로 둔다.
        base = f"{base}:00+0900" if len(base) == 16 else base
    return {
        "수집시각": collected,
        "기준시각": base,               # 없으면 빈 칸
        "코드": area["코드"],
        "지역명": item.get("AREA_NM") or area["지역명"],
        "혼잡도": level,
        "인구min": int(pmin),
        "인구max": int(pmax),
        "위도": area["위도"],
        "경도": area["경도"],
    }


def load_existing_keys() -> set:
    """이미 저장된 (코드, 기준시각) 조합을 읽어 둔다."""
    if not LOG_CSV.exists():
        return set()
    old = pd.read_csv(LOG_CSV, dtype=str).fillna("")
    if list(old.columns) != COLUMNS:
        print(f"기존 {LOG_CSV}의 열이 다릅니다.\n  지금 파일: {list(old.columns)}\n  이 프로그램: {COLUMNS}\n"
              "같은 열로 맞추거나 다른 파일 이름을 사용해 주세요.")
        sys.exit(1)
    return {(c, t) for c, t in zip(old["코드"], old["기준시각"]) if t}


def main():
    key = read_key()
    areas = load_areas()
    seen = load_existing_keys()
    collected = now_kst()

    new_rows, added, dup, failed, no_base = [], 0, 0, 0, 0
    print(f"수집 시작: {len(areas)}곳 (수집시각 {collected})")
    for _, area in areas.iterrows():
        item = fetch_one(key, area["코드"])
        if item is None:
            failed += 1
            time.sleep(SLEEP)
            continue
        row = to_row(item, area, collected)
        if row is None:
            failed += 1
            time.sleep(SLEEP)
            continue
        if not row["기준시각"]:
            no_base += 1
            print(f"  [{area['코드']}] 기준시각이 없어 중복 판단이 제한됩니다. 수집시각으로만 남깁니다.")
            new_rows.append(row)
            added += 1
        elif (row["코드"], row["기준시각"]) in seen:
            dup += 1
        else:
            seen.add((row["코드"], row["기준시각"]))
            new_rows.append(row)
            added += 1
        time.sleep(SLEEP)

    if new_rows:
        LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
        is_new = not LOG_CSV.exists()
        with LOG_CSV.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            if is_new:
                writer.writeheader()
            writer.writerows(new_rows)

    print(f"추가 {added}건 / 중복 {dup}건 / 실패 {failed}건"
          + (f" (기준시각 없음 {no_base}건)" if no_base else ""))
    if failed == len(areas) and len(areas) > 0:
        print("모든 요청이 실패했습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()
