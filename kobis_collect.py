"""kobis_collect.py - KOBIS 일별 박스오피스를 과거부터 어제까지 빠진 날짜만 받아 CSV에 이어 붙인다.

프롬프트 10-11(빠진 날짜를 채우는 수집기)을 독자가 AI에게 넣었을 때 받을 법한 결과.
- 처음 실행: 10년 전부터 어제까지 전부 비어 있으므로 오래된 날짜부터 한도(MAX_CALLS)만큼 받는다.
- 다음 실행: 아직 비어 있는 날짜를 이어서 받는다. 다 채워진 뒤에는 어제 하루만 새로 받는다.
- 실행: KOBIS_KEY=... python kobis_collect.py
        MAX_CALLS(기본 2800), START_DATE(기본 10년 전 오늘, YYYYMMDD)로 조정
"""
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

KEY = os.environ.get("KOBIS_KEY", "").strip()
if not KEY:
    print("KOBIS_KEY가 없습니다. 실행 환경의 KOBIS_KEY에 영화진흥위원회 키를 넣어 주세요.")
    sys.exit(1)

URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"
OUT = "data/kobis_daily_log.csv"
COLS = ["날짜", "순위", "영화코드", "영화명", "일관객", "누적관객", "스크린수", "상영횟수"]
MAX_CALLS = int(os.environ.get("MAX_CALLS", "2800"))   # KOBIS 키 하루 한도(3,000회) 아래로
PAUSE = 0.2                                            # 요청 사이 잠깐 쉬기

KST = timezone(timedelta(hours=9))
today = datetime.now(KST).date()
yesterday = today - timedelta(days=1)
start_env = os.environ.get("START_DATE", "").strip()
start = datetime.strptime(start_env, "%Y%m%d").date() if start_env else today.replace(year=today.year - 10)

# 이미 받은 날짜
have = set()
if os.path.exists(OUT):
    with open(OUT, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        if header and [h.strip() for h in header] != COLS:
            print(f"{OUT}의 열이 다릅니다: {header}")
            sys.exit(1)
        for row in r:
            if row:
                have.add(row[0])

# 빠진 날짜를 오래된 순으로
missing = []
d = start
while d <= yesterday:
    s = d.strftime("%Y%m%d")
    if s not in have:
        missing.append(s)
    d += timedelta(days=1)

print(f"기간 {start:%Y-%m-%d} ~ {yesterday:%Y-%m-%d} / 이미 받은 날짜 {len(have)}일 / 빠진 날짜 {len(missing)}일 / 이번 실행 최대 {MAX_CALLS}회")

added_days = 0
added_rows = 0
failed = 0
is_new = not os.path.exists(OUT)
with open(OUT, "a", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    if is_new:
        w.writerow(COLS)
    for s in missing[:MAX_CALLS]:
        try:
            res = requests.get(URL, params={"key": KEY, "targetDt": s}, timeout=15)
            js = res.json()
        except Exception as e:
            failed += 1
            print(f"{s} 요청 실패: {type(e).__name__}")
            if failed >= 5:
                print("연속 실패가 많아 이번 실행을 멈춥니다. 다음 실행에서 이어받습니다.")
                break
            time.sleep(2)
            continue
        if "faultInfo" in js:
            # 한도 초과·키 오류 등은 여기서 멈춘다(빠진 날짜는 다음 실행에서 이어받는다)
            print(f"{s} 응답 오류: {js['faultInfo'].get('message', '')}. 이번 실행을 멈춥니다.")
            break
        rows = js.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
        if not rows:
            failed += 1
            print(f"{s} 결과 없음(집계 전이거나 자료가 없는 날). 저장하지 않습니다.")
            continue
        for m in rows:
            w.writerow([s, m.get("rank"), m.get("movieCd"), m.get("movieNm"), m.get("audiCnt"),
                        m.get("audiAcc"), m.get("scrnCnt"), m.get("showCnt")])
        added_days += 1
        added_rows += len(rows)
        failed = 0
        time.sleep(PAUSE)

remaining = len(missing) - added_days
print(f"받은 날짜 {added_days}일({added_rows}행 추가) / 남은 날짜 {max(remaining, 0)}일")
if added_days == 0 and missing:
    sys.exit(1)
