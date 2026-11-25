# 공유자전거 보관소 입지 알고리즘

현재 구현: **0단계 (데이터 준비, 여정 잇기)** → **1단계 (맵매칭, OSRM)** → **2단계 (대표경로 압축)** → **3단계 (격자 후보지)** → **4단계 (접근점 표)** → **5단계 (목적함수·평가 함수)** → **6단계 (MILP 최적화)** → **8단계 (결과 내보내기)** → **9단계 (PostGIS 적재)** → **10단계 (Spring Boot API)** → **11단계 (지도 웹)**.

```
src/config.py        설정 전부 (경로, DB 접속, 기간, 임계값)
src/s0_extract.py    0단계: 추출 → 좌표변환 → 통행 분할 → 튐 제거 → 통행 필터
src/s0_join.py       0단계 뒤: 같은 레코드의 세션 조각을 여정으로 잇기 (통행 단위)
src/s1_network.py    1단계 준비: 여정 주변 도로망 잘라내기, OSRM·Valhalla 데이터
src/s1_match.py      1단계: 조각별 맵매칭 + 공백은 최단경로 → 링크 시퀀스
src/s1_compare.py    1단계 엔진 2차 비교 (OSRM vs Valhalla)
src/s2_compress.py   2단계: 정확 일치 묶기 → (조건부) 유사도 묶기 → 대표경로 → 압축률 보고
tests/test_s0.py     합성 데이터로 정제 규칙 검증
tests/test_s0_join.py 합성 데이터로 여정 잇기 규칙 검증
tests/test_s1.py     경로 좌표 → 노드 → 링크 변환 검증
tests/test_s2.py     합성 데이터로 경로 묶기 규칙 검증
src/s3_candidates.py 3단계: 격자 셀 중심 → 반경 안 경로 필터 → 서울 안만 → (기존 대여소 플래그)
tests/test_s3.py     합성 데이터로 후보지 규칙 검증
src/route_lines.py   3·4단계 공통: 링크 순서대로 대표경로 선 만들기 + 선 끝 자르기
src/s4_access.py     4단계: 후보지 STRtree → 접근점 표 (a, walk_d)
tests/test_s4.py     합성 데이터로 접근점 표 검증
src/s5_objective.py  5단계: 평가 함수 (선택 후보지 → 목적함수, reduceat 벡터화) + 무작위 조합 사전 실험
tests/test_s5.py     합성 접근점 표로 평가 함수 검증 (벡터화 = 반복문)
src/s6_milp.py       6단계: 압축형 MILP (scipy HiGHS), p·변형별로 풀고 평가 함수로 목적값 대조
tests/test_s6.py     합성 표에서 MILP 최적값 = 손 계산 = 전수 탐색
src/s8_export.py     8단계: 최종 결과 → 테이블별 GeoParquet (= PostGIS 스키마) + 계약 검사
tests/test_s8.py     합성 입력으로 내보내기 값·계약 검사 검증
sql/schema.sql       9단계: 결과 DB DDL (bike 스키마, GIST 인덱스, 읽기 전용 계정 bike_reader)
src/s9_load.py       9단계: GeoParquet → PostGIS 적재 (계약 확인 → 비우기 → 넣기, 한 트랜잭션)
tests/test_s9.py     DB 없이 계약 비교·COPY 변환·schema.sql = 계약 검증
sql/existing_station_info.sql  기존 따릉이 대여소 표시용 테이블 DDL (9단계와 별도, GIST, bike_reader SELECT)
src/load_existing_stations.py  기존 대여소 엑셀 → 정제 → GeoParquet → PostGIS (지도 표시 전용)
tests/test_existing_stations.py 합성 행으로 제외 규칙·좌표 변환·DDL = 계약·9단계와 분리 검증
compose.yaml         전체 실행: 결과 DB (PostGIS 16-3.4) + Spring API + 지도 웹 (nginx). 비밀번호·포트는 루트 .env
api/                 10단계: 읽기 전용 지도 API (Spring Boot 4.1.1, Java 25, Gradle)
web/                 11단계: 지도 웹 (Vite + TypeScript, MapLibre GL 6, OpenFreeMap 배경지도)
```

## 설치

```bash
pip install -r requirements.txt
```

## 지도 서버 켜고 끄기 (docker compose)

DB, Spring API, 지도 웹이 `compose.yaml` 하나에 있다. 저장소 루트에서 실행한다. 처음 한 번 `.env.example`을 `.env`로 복사해 비밀번호를 채운다.

```bash
docker compose up -d --build   # 켜기 (코드를 바꾸지 않았으면 --build 생략). 웹 http://localhost:3000
docker compose ps              # 상태
docker compose logs -f api     # 로그 (db / api / web)
docker compose stop            # 끄기. 데이터는 볼륨(postgis_bike_pgdata)에 남는다
```

- 세 컨테이너 모두 `restart: unless-stopped`라 Docker Desktop이 켜지면 같이 켜진다 (`stop`으로 끈 것은 제외).
- `docker compose down`은 컨테이너만 지운다 (데이터 유지). **`down -v`는 DB 볼륨까지 지운다**. 그러면 9단계 DDL과 적재를 다시 해야 한다.
- 웹 컨테이너(nginx)는 `web/dist`를 서빙하고 `/api/`를 api 컨테이너로 넘긴다 (같은 주소라 CORS 불필요, gzip 압축).
  화면을 고칠 때는 `web`에서 `npm run dev` (5173)를 따로 켜면 된다. 이때도 API는 컨테이너(8080)를 쓴다.
- 포트는 `.env`의 `BIKE_PG_PORT`(DB), `BIKE_API_PORT`(8080), `BIKE_WEB_PORT`(3000)로 바꾼다.

## 실행

### 로컬 PC (DB 직접 연결)

```bash
export BIKE_S0_SOURCE=db
export BIKE_DB_URL="postgresql+psycopg2://사용자:비밀번호@localhost:5432/DB이름"
cd src
python s0_extract.py --probe     # 날짜·모델버전별 규모 확인 → config.py의 PERIOD_START/END 수정
python s0_extract.py             # 실행 (산출물이 있으면 건너뜀, --force로 재계산)
```

### 코랩 (덤프 파일 업로드)

코랩에서는 로컬 DB에 접속할 수 없으므로 DB에서 덤프를 떠서 올린다.
아래 숫자는 2026-09-01 ~ 2026-09-08(한국시간)에 앞뒤 12시간 여유를 둔 epoch ms 값이다.
기간을 바꾸면 `python -c "import pandas as pd; print(pd.Timestamp('2026-09-01', tz='Asia/Seoul').value // 10**6)"`로 다시 계산한다.

```bash
psql -d DB이름 -c "\copy (SELECT session_id::text, ts, ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat, model_version FROM track_point WHERE ts >= 1788145200000 AND ts < 1788836400000) TO 'track_point_dump.csv' CSV HEADER"
```

덤프 파일을 구글 드라이브 `MyDrive/bike_station/data/raw/`에 올리고, 코랩에서:

```python
import os
os.environ["BIKE_RAW_DUMP"] = "/content/drive/MyDrive/bike_station/data/raw/track_point_dump.csv"
%cd /content/drive/MyDrive/bike_station/src
!python s0_extract.py
```

CSV 덤프도 좌표 정밀도 손실이 없다 (검증: parquet 덤프와 좌표 차이 3×10⁻⁹ m).
중간 산출물은 전부 parquet으로 저장된다.

### 입력 만들기 (AI Hub 원본 → track_point 형식 CSV)

기본 입력은 AI Hub `186.교통수단판별 데이터`의 Training + Validation으로 만든 CSV다 (config의 `RAW_DUMP_PATH`, `SESSIONS_META_PATH` 기본값).

```bash
cd src
python s0_build_dump.py            # data/raw/track_point_v2.csv, track_point_sessions_v2.csv 생성
python s0_build_dump.py --verify <기존 track_point_sessions.csv>   # Training만으로 기존 DB 세션 규칙 재현 확인
```

규칙은 기존 DB와 같다: 좌표 없는 행 제외, 좌표 행 10개 미만 조각 제외, accuracy > 20m 제외, (사용자, 시각) 중복 제거, 사용자별 300초 넘는 간격에서 세션 분할.
Training만으로 재구성하면 기존 세션 740개와 시작·끝 시각이 모두 일치한다 (포인트 수는 합본에서 원인 불명으로 빠진 1행 때문에 739개 일치).
session_id는 `uuid5(NAMESPACE_URL, "bike_station/track_point/{user}/{start_ts}")`로 새로 만들어 기존 DB의 id와는 다르다.
세션 메타에는 AI Hub 라벨의 `detail_label`(세션 안에서 하나)을 함께 담는다.

### 테스트

```bash
python tests/test_s0.py
python tests/test_s0_join.py
python tests/test_s1.py
python tests/test_s2.py
python tests/test_s3.py
python tests/test_s4.py
python tests/test_s5.py
python tests/test_s6.py
python tests/test_s8.py
python tests/test_s9.py
python tests/test_existing_stations.py
```

## 산출물 (data/interim/)

| 파일 | 내용 |
|---|---|
| s0_points.parquet | 남은 통행의 포인트: trip_id, session_id, ts, lon, lat, x, y(EPSG:5179), model_version |
| s0_trips.parquet | 모든 통행 요약: 포인트 수, 거리(보정/원본), 시간, 평균속도, 시종점, keep, drop_reason |
| s0_stats.json | 단계별 제거 건수, 샘플링 간격 분포, 거리 부풀림 비율, 데이터 영역, 사용한 설정값 |

`trip_id = {session_id}_{분할순번}`. 한 세션이 5분 넘게 끊기면 `_000`, `_001`처럼 나뉜다.

## 정제 규칙 요약

| 순서 | 규칙 | 설정값 |
|---|---|---|
| 1 | 세션의 첫 포인트 시각으로 기간 판정 (자정 걸친 통행 보존) | PERIOD_START/END |
| 2 | (session_id, ts) 중복 제거 | |
| 3 | EPSG:4326 → 5179 변환 | |
| 4 | 시간 공백이 5분을 넘으면 통행 분할 | SPLIT_GAP_S = 300 |
| 5 | 튐 제거: 가장 긴 일관 구간을 기준으로, 도달 불가능한 점 제거 (속도 60km/h 초과이면서 거리 30m 초과) | SPIKE_SPEED_KMH, SPIKE_MIN_JUMP_M |
| 6 | 거리는 20m 이상 움직일 때만 누적 (GPS 잡음 보정) | DIST_MIN_STEP_M = 20 |
| 7 | 통행 제외: 포인트 10개 미만, 300m 미만, 20km 초과, 평균 3km/h 미만, 40km/h 초과 | MIN_/MAX_ 값들 |

## 통행 단위: 여정 (s0_join.py)

2026-09-28 결정 (다). 같은 사용자·같은 제공처 레코드의 연속 세션(0단계 유지 통행)을, 사이 공백 동안의 직선 이동속도가
5~25km/h(`JOIN_RIDE_KMH`)일 때만 한 여정으로 잇는다. 여정이 이후 단계의 수요 단위다 (1단계 이후 trip_id = journey_id).

- 이유: 세션은 5분 창 결측으로 잘린 조각이다. 목적함수가 경로 시종점을 기준선으로 쓰므로, 세션 그대로면 잘린 지점이 가짜 시종점이 되고 한 주행이 여러 번 세어진다.
- 공백 구간의 경로는 1단계 맵매칭이 도로망으로 채운다 (관측되지 않은 부분이라 추정이다).
- **끊긴 시간 상한** `JOIN_MAX_GAP_S` = 15분 (2026-09-28 확정): 공백이 이보다 길면 속도가 맞아도 잇지 않고, 그 지점을 여정 경계로 둔다 (양쪽 조각 모두 독립 여정으로 유지).
  공백은 '빠진 5분 창 수 × 300초 + 1초 또는 61초'로 나타나므로 5분 창 단위로 비교한다 (상한 15분이면 901·961초 공백도 잇는다).
- v2 CSV 결과 (같은 레코드 연속 쌍 158, 그중 속도 조건 통과 146):

| 끊긴 시간 상한 | 이은 쌍 | 여정 | 서울 여정 | 그중 여러 조각 | 서울 여정 시간 중 조각 사이 공백 |
|---|---|---|---|---|---|
| 15분 | 94 | 599 | 369 | 53 | 19.6% |
| 20분 | 107 | 586 | 363 | 58 | 24.3% |
| 30분 | 125 | 568 | 353 | 65 | 31.2% |
| 없음 | 146 | 547 | 342 | 74 | 43.8% |

  속도 조건을 통과한 쌍의 공백 길이 (빠진 5분 창 수): 1개 47, 2개 27, 3개 19, 4개 14, 5개 8, 6개 10, 7개 4, 8개 4, 9개 7, 10개 3, 12개 1, 23개 1, 26개 1.
  뚜렷한 끊김 없이 완만하게 줄어든다. 직선 이동속도 중앙값은 공백 길이와 상관없이 10~13km/h다.
- 민감도: 상한별 결과를 s0_join_stats.json의 `max_gap_sensitivity`에 함께 남긴다 (`JOIN_MAX_GAP_SENS_S`).
- 산출물: s0_journeys.parquet (여정 1행, in_study_region 포함), s0_journey_pieces.parquet (조각 → 여정, 공백 초·속도), s0_join_stats.json

## 1단계: 맵매칭 (s1_network.py → s1_match.py)

전체 실행 순서: `s0_extract.py → s0_join.py → s1_network.py → s1_match.py → s2_compress.py → s3_candidates.py → s4_access.py → s5_objective.py → s6_milp.py → s8_export.py → (sql/schema.sql) → s9_load.py`
(OSRM은 Docker, Valhalla는 pyvalhalla. 도로망은 여정 궤적 주변 1km의 OSM highway 전체)

**방식**
- 여정의 조각(1초 관측)마다 매칭한다. 조각 안에서 매칭이 끊긴 곳(엔진 도로망에 없는 길을 지난 곳)은 **관측 GPS 궤적으로** 잇고 링크는 넣지 않는다.
- 조각 사이 공백(최대 15분, 관측 없음)은 엔진의 **자전거 최단경로로 가정**해 잇는다.
- 링크 = 경로 좌표 중 OSM 노드 위의 점을 순서대로 이은 이웃 노드 쌍 (방향 없음). 경로가 시작·끝나는 반쪽 링크도 넣는다.

**엔진 선택: OSRM** (2차 비교, 서울 여정 369개 전체, 같은 방식)

| | OSRM | Valhalla |
|---|---|---|
| 실패 | 0 | 0 |
| 조각 안 매칭 끊김이 있는 여정 | 22% | 18% |
| 길이비 (경로 − 공백 연결) / 관측 거리, p50 / p90 | 1.01 / 1.11 | 1.00 / 1.05 |
| GPS 점 중 경로에서 30m 넘게 먼 점 비율, p50 / p90 | 0.00 / **0.00** | 0.00 / 0.07 |
| 공백 연결 61개: 우회율 p50 / p90 | 1.19 / 1.56 | 1.19 / 1.60 |
| 공백 연결 필요 속도 km/h p50 / p90 / 최대 | 14.8 / 21.5 / 38.1 | 14.8 / 21.8 / 25.0 |
| 여정당 초 | 0.04 | 0.01 |

- 고른 이유: 관측 궤적을 더 충실히 따른다 (벗어난 점 p90 0% vs 7%). 공백 연결은 비슷하다. 두 엔진 경로가 다른 곳은 주로 공백 연결 구간으로, 각자의 자전거 프로필이 다른 길을 고른다 (어느 쪽이 맞는지는 알 수 없음).
- 실행 중 고친 것
  - 조각 안 끊김을 최단경로로 이으면 도로망에 없는 30m를 돌아가느라 우회로가 생겼다 (OSRM 우회율 p90 65배, 길이비 p90 1.42). 관측 궤적으로 잇도록 바꿨다.
  - OSRM 응답의 노드 id(annotation)는 쓰지 않는다. JSON 숫자가 유효숫자 10자리로 찍혀 11자리 노드 id의 끝자리가 반올림되고, 1초 입력에서는 구간 경계마다 노드가 겹쳐 링크가 중복·누락됐다 (링크 길이 / 경로 길이 p10 0.85, p90 1.28). 경로 좌표에서 노드를 찾는 방식으로 바꾼 뒤 p10 0.99, p50 1.03 (끝 반쪽 링크를 통째로 넣어 1보다 약간 크다).

**v2 CSV 결과**: 서울 여정 369개 전부 매칭, 링크 12,284개, 조각 안 끊김 149곳(관측 궤적으로 이음), 공백 연결 61곳, 도로에 없는 노드 쌍 104곳(링크에서 뺌).

## 2단계: 대표경로 압축 (s2_compress.py)

같은 길을 가는 통행 500건은 최적화 입장에서 가중치 500짜리 경로 1개와 같다. 사실상 같은 통행을 묶어 경로 수를 줄인다.

**입력 계약** (1단계 본 실행이 이 형식으로 저장해야 한다. 자세한 내용은 config.py)

| 파일 | 칼럼 |
|---|---|
| s1_routes.parquet | trip_id, link_seq(list<int64>), start_x, start_y, end_x, end_y (매칭 경로 시종점, EPSG:5179) |
| s1_links.parquet | link_id, length_m (2-2), geometry (WKB, 3단계) |

**규칙**

| 단계 | 내용 | 설정값 |
|---|---|---|
| 2-1 | 방향을 없앤 뒤 key = (snap(시점), snap(종점), link_seq)가 같은 통행을 묶는다. 끝점을 키에 넣어 중간 합류 통행이 긴 경로에 흡수되지 않게 한다 | S2_ENDPOINT_GRID_M = 150 |
| 2-2 | 2-1 결과가 목표보다 많고 임계값이 정해졌을 때만. 링크 길이 가중 자카드, MinHash+LSH 후보 → 정확한 값 확인 → union-find. 시점끼리·종점끼리 150m 넘으면 묶지 않음 | S2_TARGET_ROUTES, S2_SIM_THRESHOLD(미정), S2_SIM_ENDPOINT_M |
| 2-3 | 대표 = 그룹 안 최빈 링크 시퀀스, 가중치 = 그룹 통행 수, 시종점 = 구성 통행의 중앙값 | |
| 2-4 | 원본 통행 수, 2-1 후 경로 수·압축률, 가중치 분포(최대, 중앙값, 상위 10개)를 출력하고 s2_stats.json에 저장 | S2_BAND_* |
| 2-5 | `validate_compression(objective, ...)`: 무작위 해 30개로 원본 대비 상대오차. 5단계 평가 함수가 생기면 호출 | S2_VALID_* |

**산출물**: s2_routes.parquet (route_id, link_seq, weight, n_distinct_seq, 시종점), s2_trip_route.parquet (trip_id → route_id, reversed), s2_stats.json

**설계 메모**
- 경로는 방향 없이 합친다 (2026-09-28 결정). 링크 시퀀스와 뒤집은 시퀀스 중 사전순으로 작은 쪽을 표준 방향으로 삼고, 뒤집은 통행은 시종점도 맞바꾼다. 2-2에서는 끝점을 엇갈려 맞춘 경우도 인정하고, 합쳐진 그룹의 방향을 통행이 가장 많은 쪽에 맞춘다. `s2_trip_route.reversed`는 통행이 대표경로와 반대로 지나갔는지 표시한다.
- 2-1의 끝점 비교는 격자 칸 기준이라, 몇 m 떨어진 두 점도 칸 경계를 사이에 두면 다른 끝점이 되고, 같은 칸 안에서는 최대 212m(대각선)까지 같은 끝점이 된다.
- 2-2의 union-find는 전이적이라(A~B, B~C → A~C) 끝점이 150m를 넘게 벌어진 통행도 한 그룹이 될 수 있다. 통행 끝점과 대표 끝점의 거리 분포(`endpoint_spread_m`)를 함께 출력한다.
- MinHash는 개수 자카드를 근사하므로 링크를 10m마다 토큰 하나로 나눠 넣어 길이 가중을 반영한다.
- LSH 밴드는 누락을 줄이는 쪽으로 잡는다 (`S2_LSH_WEIGHTS = (0.1, 0.9)`). 합성 시험(경로 300개, 가중 자카드 0.8 이상 쌍 281개)에서 기본값 (0.5, 0.5)은 3.9%를 놓쳤고 (0.1, 0.9)는 놓친 쌍이 없었다. 토큰 근사 오차는 0.002 이내였다.

## 3단계: 격자 후보지 (s3_candidates.py)

1. 모든 대표경로를 감싸는 사각형 + 여유 500m, 2. 격자 셀 중심을 후보지로, 3. 반경 안에 지나가는 대표경로가 없는 셀은 뺀다.
POI·설치 가능성 필터는 넣지 않는다. 대여소 후보지는 서울 안에만 두므로 서울 밖 셀은 뺀다 (`S3_CLIP_TO_STUDY_REGION`).
기존 대여소 위치를 확보하면 `BIKE_EXISTING_STATIONS`(CSV: station_id, lon, lat)로 넣는다. 반경 필터와 상관없이 `is_existing=True`로 들어간다.

산출물: s3_candidates.parquet (cand_id, x, y, n_routes = 반경 내 대표경로 수, n_trips = 그 가중치 합, is_existing, station_id), s3_stats.json

**격자·반경 결정 (2026-09-28)**: 맵매칭 전이라 서울 여정 342개의 GPS 궤적(881km)을 경로 대용으로 써서 비교했다.
'시종점 덮임' = 경로 시점과 종점 모두 반경 안에 후보지가 있는 경로 비율 (나머지 약 4%는 서울 밖 시종점이라 어떤 설정에서도 못 덮음).

| 격자 | 반경 | 최소 경로 | 후보지 | 시종점 덮임 |
|---|---|---|---|---|
| 200 | 300 | 1 | 5,191 | 95.9% |
| 300 | 300 | 1 | 2,305 | 95.3% |
| **400** | **300** | **1** | **1,288** | **95.3%** |
| 500 | 300 | 1 | 827 | 85.1% |
| 200 | 100 | 1 | 2,271 | 61.7% |
| 300 | 150 | 3 | 285 | 18.4% |
| 500 | 300 | 3 | 275 | 43.9% |

- 통행이 적어도 서울 전역에 흩어져 있어, 후보지 수는 통행 수가 아니라 경로가 지나간 면적에 따라 정해진다.
- 반경을 줄이면 후보지는 줄지만 시종점이 후보지 반경 밖으로 빠진다. 반경이 격자/√2 이상이어야 모든 시종점 근처에 후보지가 남는다.
- 서울 안 시종점 전부를 반경 300m 안에 두는 최소 후보지 수가 약 314개(탐욕 근사)라, 목표 150~300개는 시종점을 잃어야만 가능하다.
- 처음에는 시종점을 잃지 않는 가장 큰 격자인 400m를 골랐다. 그런데 MILP 규모를 정하는 건 후보지 수가 아니라 접근점 표 행 수이고
  200m여도 약 1.1만 행이라 **200m로 확정**했다 (2026-09-28). 후보지 위치 오차가 최대 141m다.

**접근점 표** (s3_access.parquet: 경로마다 반경 안 후보지와 거리). MILP 규모는 후보지 수가 아니라 이 표의 행 수로 정해진다.
반경 300m, GPS 궤적 대용, 끊긴 시간 상한별:

| 격자 | 상한 | 서울 여정 | 후보지 | 접근점 행 | 경로당 후보지 평균 / 중앙값 / p90 / 최대 |
|---|---|---|---|---|---|
| 400 | 15분 | 369 | 1,062 | 2,675 | 7.2 / 6 / 13 / 35 |
| 400 | 20분 | 363 | 1,116 | 2,770 | 7.6 / 6 / 14 / 45 |
| 400 | 30분 | 353 | 1,169 | 2,960 | 8.4 / 6 / 15 / 52 |
| 400 | 없음 | 342 | 1,288 | 3,317 | 9.7 / 6 / 20.6 / 70 |
| 300 | 20분 | 363 | 1,982 | 4,829 | 13.3 / 10 / 25 / 81 |
| 200 | 20분 | 363 | 4,464 | 10,863 | 29.9 / 23 / 56 / 179 |

모든 후보지가 적어도 한 경로에 걸린다 (반경 필터로 만들었으므로). '안 걸리는 후보지 제거'로 줄일 것은 없다.
규모 판단 기준 `S3_ACCESS_ROWS_WARN = 100,000`: 넘으면 ① 안 걸리는 후보지 제거 ② 격자 키우기 순서로 줄인다.

**맵매칭 경로 기준 실제 결과** (격자 200m, 반경 300m, 상한 15분, 서울 여정 369개):
후보지 4,132개, 접근점 표 10,663행 (경로당 평균 28.9, 중앙값 23, p90 53, 최대 141, 최소 6), 시종점 덮임 96.8%.
경로 선은 4단계와 같은 선(route_lines.py: 링크 순서대로 잇고 2단계 시종점에서 자름)을 쓴다 (2026-09-28).
전에는 링크를 순서 없이 모은 도형이라 끝 링크 돌출 구간이 반경 판정에 들어가 후보지 4,188개, 11,012행이었다.
2단계는 369개 → 369개로 압축이 없다 (같은 링크 시퀀스와 시종점을 공유하는 여정이 없음).

## 4단계: 접근점 표 (s4_access.py)

대여소는 경로 위에 있을 필요가 없다 (도보 300m, `S4_WALK_M`). 후보지가 경로를 만나는 지점을 표로 만든다. 이 표의 행 수가 실질적인 문제 규모다.

1. 대표경로 선: `link_seq` 순서대로 링크를 이어 방향 있는 선 하나로 만든다 (1단계는 매칭 선 모양을 저장하지 않는다). 링크 방향은 앞뒤 링크와 공유하는 노드로 정하고, 이어지지 않는 곳은 직선으로 잇는다.
   - 링크 = 경로가 지나간 이웃한 OSM 노드 쌍 사이 직선. 노드는 교차로만이 아니라 도로 모양을 그리는 꼭짓점 전부다 (1단계 `nodes.parquet`). 곧은 도로는 노드가 드물어 링크가 길다 (전체 중앙값 19.5m, p90 72.6m).
   - **선 끝 자르기** (`ROUTE_TRIM_TO_ENDPOINTS`, 2026-09-28 결정 A): 1단계는 경로 시작·끝의 반쪽 링크를 링크 전체로 넣으므로, 선을 2단계 대표 시점·종점에서 자른다. 시점은 첫 링크에만, 종점은 마지막 링크에만 투영한다 (선 전체에 투영하면 출발지로 되돌아오는 경로에서 종점이 선 앞부분으로 간다).
2. 후보지 점 STRtree → 경로마다 반경 안 후보지 (`dwithin`).
3. `walk_d` = 후보지에서 선까지 직선거리, `a` = 선 위 가장 가까운 점의 누적거리 (경로 시작 기준).

산출물: s4_access.parquet (route_id, cand_id, a, walk_d, weight, route_len), s4_route_lines.parquet (route_id, route_len, n_breaks, break_m, repeat_ratio, trim_start_m, trim_end_m, geometry), s4_stats.json

**v2 실데이터 결과** (격자 200m, 반경 300m, 대표경로 369개, 후보지 4,132개, 선 끝 자르기 켬, 2026-09-28 전체 실행)

| 지표 | 값 |
|---|---|
| 전체 행 수 | 10,663 (자르기 전 11,013) |
| 도달 불가 경로 | 0 |
| 경로당 후보지 수 평균 / 중앙값 / p90 / 최대 / 최소 | 28.9 / 23 / 53 / 141 / 6 |
| 후보지별 걸린 경로 수 평균 / 중앙값 / p90 / 최대 | 2.58 / 2 / 6 / 13 |
| 어떤 경로에도 안 걸리는 후보지 | 0 (3단계도 같은 선을 쓰도록 맞춤. 맞추기 전 56개 → 3단계 후보지 4,188 → 4,132) |
| 끊겨 직선으로 이은 곳 | 경로 70개, 92곳, 8.1km (길이의 1.2%) |
| 선 끝에서 자른 길이 | 27.0km (자르기 전 길이의 3.9%), 경로당 중앙값 52m, 최대 544m |
| 3단계 표와 차이 | 없음 (같은 선을 쓰므로 쌍·거리 모두 일치) |

2026-09-28 결정
- 끝점 행 (a = 0 또는 경로 길이, 후보지가 경로 끝 바깥): 그대로 둔다. 자른 뒤 시점 1,158행, 종점 1,140행. walk_d 중앙값 212·208m (중간 행 148m).
- 링크 반복 경로 61개 (단계별 실행 때 62개, 아래 '전체 실행' 참고): `repeat_ratio`(= (링크 수 − 서로 다른 링크 수) / 링크 수) 칼럼으로 남기고 민감도 분석에서 다시 본다. 중앙값 0.05, 최대 0.38.
- 반경 원 안을 여러 조각으로 지나는 행 759개: 조각 사이 경로상 간격 분포만 출력한다 (중앙값 0m = 꺾임·교차로 잘린 것, p90 366m, 최대 2,452m).
- 선 끝 돌출: 2단계 시종점에서 자른다 (A). 서울 경계와는 무관하다 (편차 100m 초과 66건 중 63건이 서울 안 끝점).

## 5단계: 목적함수와 평가 함수 (s5_objective.py)

경로 f에 걸린 선택 후보지들의 a 중 `혜택(f) = max(a) − min(a)` (2개 이상일 때, 아니면 0), `목적함수 = Σ weight_f × 혜택(f)`. 중간 대여소는 값에 영향이 없다.

- 변형 (`S5_MODE`): `absolute` = m 그대로 (긴 통행에 무게), `ratio` = 혜택 / 경로 길이 (0~1, 통행 건수를 공평하게). 7단계에서 비교한다.
- 최소 탑승 거리 (`S5_MIN_RIDE_M` = 500m): 혜택이 이 값 미만이면 0. ratio에서도 나누기 전 m 값으로 판정한다. 정확히 500m는 인정.
- 6단계 최적화는 **MILP만** 쓴다 (2026-09-28 결정, 휴리스틱 없음). 평가 함수는 사전 실험, 2-5 압축 오차 검증, MILP 해의 목적값 대조에 쓴다.
- 구현: `Evaluator(access)`가 접근점 표를 route_id, a 순으로 정렬해 두고, `evaluate(선택 후보지 id 집합) → (목적함수 값, 경로별 혜택 배열)`.
  선택 안 된 행을 ±inf로 바꿔 `np.maximum.reduceat` / `np.minimum.reduceat`로 경로별 최대·최소를 한 번에 구한다.
  행이 없는 경로(도달 불가)는 reduceat에 넣지 않고 0으로 채운다. `evaluate_loop`는 검증용 반복문 구현이다.
- 2-5 압축 오차 검증: 2단계 압축이 없어(369 → 369, 가중치 전부 1) 원본과 압축 목적함수가 같다. 따로 돌리지 않았다.

산출물: s5_random_eval.parquet (p, sample, obj_absolute, obj_ratio, n_routes_benefit, n_routes_below_min), s5_stats.json

**v2 실데이터 사전 실험** (접근점 표 10,663행, 경로 369개, 후보지 4,132개, p별 무작위 1,000개)

| 지표 | 값 |
|---|---|
| 상한 (후보지 전부 선택) | absolute 593,352 m, ratio 348.3 |
| 어떤 해로도 혜택 0인 경로 | 14개 (걸린 후보지 최대 간격 292~499m, 모두 경로 길이 500m 미만) |
| 벡터화 vs 반복문 (해 120개) | 최대 차이 absolute 4.7e-10, ratio 1.7e-13 |
| 평가 1회 | `evaluate` 0.12~0.22ms (p 10~1000), `evaluate_mask` 약 0.10ms, 반복문 약 24ms |

| p | absolute 중앙값 (상한 대비) | ratio 중앙값 (상한 대비) | 혜택 > 0 경로 중앙값 | 0인 해 비율 |
|---|---|---|---|---|
| 10 | 0 (0) | 0 (0) | 0 | 62.8% |
| 50 | 24,784 (0.042) | 7.03 (0.020) | 13 | 0% |
| 100 | 74,069 (0.125) | 23.1 (0.066) | 39 | 0% |
| 200 | 169,536 (0.286) | 61.3 (0.176) | 93 | 0% |
| 500 | 362,068 (0.610) | 169.6 (0.487) | 216 | 0% |
| 1000 | 504,303 (0.850) | 274.7 (0.789) | 310 | 0% |

## 6단계: MILP 최적화 (s6_milp.py)

압축형 MILP. 쌍 변수 y[f][i][j](경로당 후보지 수 제곱) 대신 경로별 가장 뒤·가장 앞 커버 위치만 다룬다. 솔버는 scipy.optimize.milp (HiGHS 내장).

| 변수 | 뜻 |
|---|---|
| x[k] (이진) | 후보지 k 설치 |
| z[f] (이진) | 경로 f가 혜택을 받는가 (최소 탑승 거리 때문에 추가) |
| s[n], t[n] (연속 0~1, 접근점 행마다) | 행 n이 경로의 뒤쪽 끝 / 앞쪽 끝으로 뽑혔는가 (u, v 선형화용 보조변수) |
| u[f], v[f], g[f] | 뒤쪽 끝 위치, 앞쪽 끝 위치, 혜택 |

최대화 Σ c_f g[f] (c_f = weight, ratio면 weight / 경로 길이). 제약: s ≤ x, t ≤ x, Σs = Σt = z, u ≤ Σ a·s, v ≥ Σ a·t, g ≤ u − v, u − v ≥ 500·z, Σx ≤ p.

- s, t는 설치된 행 위의 분포라 u ≤ 선택 중 최대 a, v ≥ 선택 중 최소 a가 되고, 최대화라 등호로 붙는다. 대여소가 하나뿐이면 u = v, 혜택 0.
- 최소 탑승 거리(5단계 `S5_MIN_RIDE_M`)는 제시된 모형에 없어서 z[f]와 u − v ≥ 500·z를 더했다. 이것이 없으면 MILP 값이 평가 함수 값과 달라진다.
- 전처리: 후보지를 전부 골라도 혜택이 0인 경로 14개와, 남은 경로에 걸리지 않는 후보지 32개를 뺀다 (결과는 바뀌지 않는다).
- 검증: MILP 목적값 = 선택된 x를 5단계 평가 함수로 다시 잰 값 (`S6_CHECK_REL_TOL`). 합성 테스트에서는 전수 탐색 최적값과 비교한다.
- 설정: `S6_P_LIST` (p 미정, 5단계와 같은 목록), `S6_MODES`, `S6_MIP_REL_GAP` = 1e-4, `S6_TIME_LIMIT_S` = 600.

산출물: s6_selected.parquet (mode, p, cand_id, used, x, y, is_existing, station_id), s6_routes.parquet (mode, p, route_id, u, v, g, benefit_m, benefit, start_cand, end_cand), s6_stats.json

**v2 실데이터 결과** (2026-09-28 전체 실행. 변수 26,488개 중 이진 4,455개, 제약 23,099개, 12회 풀이 합계 571초, 모두 상대 갭 1e-4 이내, MILP = 평가 함수)

| p | absolute (상한 대비) | 무작위 최대 대비 | 시간(초) | ratio (상한 대비) | 무작위 최대 대비 | 시간(초) |
|---|---|---|---|---|---|---|
| 10 | 66,194 (0.112) | 4.71배 | 108.9 | 27.09 (0.078) | 7.84배 | 30.6 |
| 50 | 241,434 (0.407) | 4.11배 | 314.0 | 104.79 (0.301) | 5.68배 | 35.1 |
| 100 | 362,830 (0.611) | 3.05배 | 19.1 | 174.20 (0.500) | 4.51배 | 21.8 |
| 200 | 497,584 (0.839) | 2.12배 | 14.9 | 270.68 (0.777) | 3.26배 | 19.5 |
| 500 | 593,337 (1.000) | 1.47배 | 1.9 | 348.31 (1.000) | 1.77배 | 1.9 |
| 1000 | 593,337 (1.000) | 1.12배 | 1.8 | 348.31 (1.000) | 1.18배 | 1.7 |

p = 500에서 이미 상한(후보지 전부를 고른 값)에 닿는다 (absolute는 갭 2.6e-5 안). 혜택을 받을 수 있는 경로 355개 전부가 혜택을 받는다.
최적해는 하나가 아니다: 목적값이 같아도 실행마다 고른 위치가 다를 수 있다 (단계별 실행과 겹친 위치: absolute p=10 8/10, p=100 90/100, p=500 222/500). 7단계 검증에서 다룬다.

**전체 실행 (2026-09-28)**: v2 CSV로 `s0_extract → … → s6_milp`를 `--force`로 한 번에 실행 (약 14분: 1단계 도로망 183초, 6단계 574초, 나머지 1분 미만).
단계별로 따로 실행했을 때와 비교해 0·3단계 산출물은 같고, 1단계에서 경로 1개(`7aa6bf58…_000`)만 달랐다.
지나간 링크 집합은 같지만 조각 안 매칭 끊김 근처의 링크 4개 왕복이 빠져 경로 길이가 548m → 500m가 됐다 (OSRM 데이터를 다시 만들 때 생긴 차이로 보인다).
이 경로가 최소 탑승 거리를 넘을 수 없게 되어 혜택 0 경로가 13 → 14개, 상한이 593,898 → 593,352m가 됐다. 이 README의 4~6단계 수치는 전체 실행 값이다.

## 8단계: 결과 확정 및 내보내기 (s8_export.py)

최종 결과를 테이블별 GeoParquet(EPSG:5179)으로 `data/output/export/`에 내보낸다. **이 테이블 구조(`s8_export.SCHEMA`)가 그대로 PostGIS 스키마다.**
스키마를 바꾸면 9단계 DDL과 10단계 Spring 쿼리를 함께 고친다. 분석을 다시 돌려도 스키마가 같으면 웹 코드는 바뀌지 않는다.

| 테이블 | 칼럼 (기본키 굵게) | 행 수 (v2) |
|---|---|---|
| candidates | **cand_id**, sgg_code, n_routes, n_trips, is_existing, station_id, boot_freq, geom(Point) | 4,132 |
| scenarios | **scenario_id**, cond_id, mode, build_type, walk_m, trip_unit, p, objective, upper_bound, n_selected, n_routes_benefit, mip_gap, status | 12 |
| scenario_selection | **scenario_id, cand_id**, used (선택된 것만) | 2,737 |
| routes | **route_id**, weight, route_len_m, obs_ratio, repeat_ratio, straight_ratio, n_breaks, geom(LineString) | 369 |
| route_benefit | **scenario_id, route_id**, benefit_m, benefit, start_cand, end_cand (혜택 > 0만, 없는 행 = 0) | 2,711 |
| existing_stations | **station_id**, cand_id, sgg_code, geom(Point) | 0 |
| coverage_curve | **cond_id, p**, mode, build_type, walk_m, trip_unit, objective, upper_bound | 12 |
| districts | **sgg_code**, sgg_name, area_km2, n_candidates, n_routes, route_km, geom(MultiPolygon) | 25 |

- 시나리오 = 조건 + p. `cond_id = {mode}-{build_type}-w{walk_m}-{trip_unit}`, `scenario_id = {cond_id}-p{p}` (예: `absolute-new-w300-journey_gap15m-p100`). 같은 조건이면 다시 돌려도 같은 id다.
- 시나리오는 긴 형태로 저장한다. 민감도 결과는 s6 형식 산출물에 `build_type, walk_m, trip_unit` 칼럼을 담아 오면 scenarios에 조건별 행으로 들어간다. 없으면 config 기본값(`S8_BUILD_TYPE_DEFAULT`, `S4_WALK_M`, `S8_TRIP_UNIT_DEFAULT`)으로 채운다.
- 기존 대여소 기준선 점수는 `build_type = "existing"` 시나리오 행으로 둔다 (선택 목록, 경로별 혜택, 목적값이 같은 테이블로 비교된다).
- 웹이 계산하지 않도록 비율·구 코드를 칼럼으로 만든다: `obs_ratio` = 경로에 묶인 여정의 관측 시간 합 / 전체 시간 합, `straight_ratio` = 직선 이음 길이 / 경로 길이, `candidates.sgg_code` (구별 집계를 공간 조인 없이).
- 계약 검사 (하나라도 틀리면 저장 안 함): 기본키 중복·NULL, 외래키, 시나리오별 선택 수 = n_selected, Σ weight × benefit = objective (상대 1e-6), 혜택 경로 수, 커버리지 곡선 = 시나리오 목적값, 도형 유효·타입·EPSG:5179.
- 7단계 입력 계약 (있으면 쓴다): `s7_bootstrap.parquet` (cand_id, boot_freq), `s7_coverage.parquet` (mode, p, objective [, upper_bound, 조건 칼럼]).

**현재 비어 있는 것** (7단계 미구현, `manifest.json`의 `pending`): boot_freq 전부 NULL, 커버리지 곡선은 6단계 p(10~1000)만, 기존 대여소 없음(기준선·증설 시나리오 없음), 민감도 조건 1개(도보 300m, 여정 15분).

## 9단계: PostGIS 적재 (sql/schema.sql → s9_load.py)

스키마는 Python 쪽이 소유한다. `sql/schema.sql`이 테이블을 만들고, `s9_load.py`는 데이터만 넣는다 (`to_postgis()`가 테이블을 만들지 않는다).
Spring은 `schema.sql`이 만드는 읽기 전용 계정 `bike_reader`로만 접속한다.

```bash
# 1) 결과 DB 컨테이너 (루트 .env.example을 .env로 복사해 비밀번호를 채운다. db만 켤 때: docker compose up -d db)
docker compose up -d db
# 2) DDL (처음 한 번, 스키마를 바꿨을 때 다시). reader_password = Spring 계정 비밀번호
docker compose exec -T db psql -U postgres -d bike_station -v reader_password="$BIKE_READER_PASSWORD" -f - < sql/schema.sql
# 3) 적재 (분석을 다시 돌릴 때마다: s8_export.py --force 뒤에)
export BIKE_RESULT_DB_URL="postgresql+psycopg2://postgres:<관리자 비밀번호>@localhost:<BIKE_PG_PORT>/bike_station"
cd src && python s9_load.py          # --check: 계약 확인만
```

- DDL: bike 스키마 테이블 8개 (칼럼은 8단계 표와 같음), 기본키·외래키·CHECK(mode, build_type, 비율 0~1, benefit_m > 0), geom 4개에 GIST, 외래키 반대 방향·구 코드 btree.
  다시 실행하면 bike 결과 테이블을 지우고 새로 만든다. `reader_password` 변수가 없으면 전부 롤백된다.
- `bike_reader`: LOGIN, `default_transaction_read_only = on`, `search_path = bike, public`, bike 스키마 USAGE + 테이블 SELECT만.
- 적재: DB 칼럼 이름·순서·타입·도형 타입·SRID가 `s8_export.SCHEMA`와 다르면 넣지 않고 멈춘다. 같으면 한 트랜잭션에서 TRUNCATE → COPY(EWKB, 5179 그대로) → 행 수·SRID 검사 → ANALYZE → 커밋. 실패하면 롤백되어 전 데이터가 남는다.
- 접속 정보는 환경변수로만: `BIKE_RESULT_DB_URL` (적재용, 0단계 원본 DB의 `BIKE_DB_URL`과 별개), `BIKE_PG_ADMIN_PASSWORD`, `BIKE_READER_PASSWORD`. 저장소에는 `.env.example`만 둔다.

**확인 (2026-09-29, 임시 컨테이너 postgis/postgis:16-3.4)**: 8개 테이블 적재 1.0초 (다시 실행해도 같은 결과).
bike_reader로 `ST_AsGeoJSON(ST_Transform(geom, 4326))` 조회 가능 (서울 좌표), DB에서 다시 합산한 Σ weight × benefit = scenarios.objective.
bike_reader의 INSERT·UPDATE·TRUNCATE·DROP·CREATE는 읽기 전용 설정을 꺼도 권한 오류로 막힘. `ST_DWithin` 조회가 GIST 인덱스를 씀.
DB 칼럼 타입을 바꾸면 적재를 거부하고, 마지막 테이블에서 실패시키면 전부 롤백되어 전 데이터가 남는 것을 확인했다.

## 기존 따릉이 대여소 표시 (sql/existing_station_info.sql → load_existing_stations.py)

지도에 기존 대여소를 빨간 점으로 띄우기만 한다. 기준선 점수, 일치율, 커버 구간 계산은 하지 않고 분석 단계(1~8단계)와 연결하지 않는다.

- 입력: 서울시 공공자전거 대여소 정보 엑셀 (`config.EXISTING_STATIONS_XLSX`, 환경변수 `BIKE_EXISTING_STATIONS_XLSX`가 우선). 원본은 읽기만 한다.
  시트 `대여소현황`, 1~5행이 병합된 다중 헤더라 헤더 없이 6행부터 읽고 칼럼 이름(A~J)은 코드에서 붙인다. 헤더 칸(대여소 번호·이름·자치구·위도·경도)이 예상과 다르면 멈춘다.
- 테이블 `bike.existing_station_info (station_id text PK, station_name, gu_name, geom Point 5179)`. 9단계 `bike.existing_stations`(후보지에 묶인 기존 대여소, 8단계 계약)와 별개라
  후보지 외래키가 없고, `schema.sql`의 DROP과 `s9_load.py`의 TRUNCATE 대상이 아니다 (9단계를 다시 돌려도 남는다).
- 정제: 위경도 빈 칸 / 숫자 아님 / 0 / 서울 범위(위도 37.4~37.7, 경도 126.7~127.3) 밖은 제외하고 사유별로 센다. 좌표는 고치지 않는다. 대여소 이름은 앞뒤 공백만 지운다.
  대여소 번호가 비었거나 겹치면 멈춘다.
- 적재: 칼럼이 DDL과 다르면 멈춘다. 한 트랜잭션에서 TRUNCATE → INSERT(파라미터 바인딩, EWKB 5179) → 행 수·SRID 검사 → ANALYZE → 커밋.

```bash
# DDL (처음 한 번. schema.sql 뒤에 실행한다: bike 스키마와 bike_reader가 있어야 함)
docker compose exec -T db psql -U postgres -d bike_station -f - < sql/existing_station_info.sql
# 적재 (다시 실행하면 비우고 새로 넣는다)
export BIKE_RESULT_DB_URL="postgresql+psycopg2://postgres:<관리자 비밀번호>@localhost:<BIKE_PG_PORT>/bike_station"
cd src && python load_existing_stations.py        # --no-db: parquet까지만
```

**엑셀 확인 (26.6월 기준 파일, 2026-09-29)**: 데이터 2,789행, 위경도 빈 칸·0·서울 범위 밖 0행, 위도·경도 뒤바뀜 0행, 번호 중복 0, 좌표 중복 0,
이름 앞뒤 공백 2,003행. 자치구는 이름만 있고 코드는 없다 (25개 구). 제외 0행 → 2,789행 적재.

**확인 (2026-09-29, bike-postgis-perf + `java -jar`)**: DB 2,789행 (번호 중복 0, 이름 빈 값 0, 앞뒤 공백 0, SRID 5179), 다른 bike 테이블 행 수 그대로.
`/api/existing-stations` 응답 540,463B (gzip 없음), 좌표 소수점 최대 6자리, 좌표 범위 = 엑셀 범위. 클라이언트 왕복 (워밍업 5회 뒤 50회) 중앙값 31.7ms, p95 144.6ms, 최대 315.3ms.
headless Chrome: 처음엔 꺼짐 (API 호출 없음) → 켜면 2,789개 렌더링, 레이어 순서 빨간 점 < 파란 점 → 점 클릭 시 이름·번호 팝업 → 끄면 0개 → 다시 켜도 다시 받지 않음, 콘솔 오류 없음.

## 10단계: 웹 백엔드 API (api/, Spring Boot)

읽기 전용 API. PostGIS를 조회·집계만 하고 계산하지 않는다. Spring Boot 4.1.1 (3.x는 2026-06-30 무료 지원 종료로 4.x 선택), Java 25, Gradle 9.7.1.

```
com.example.bikestation
├── controller   요청 받기·파라미터 검증 (BboxParser, @NotBlank/@Positive), 오류 = ProblemDetail
├── service      시나리오 확인(없으면 404), 비율·합계 조합
├── repository   JPA 리포지토리 + 공간 네이티브 쿼리 (결과 = 인터페이스 프로젝션)
├── domain       엔티티 Scenario, CoverageCurvePoint, Candidate, District, ExistingStationInfo (@Immutable, geom 매핑 안 함)
├── dto          응답 record (엔티티를 그대로 내보내지 않음)
└── config       CORS, 설정값, 응답 시간 로그 필터
```

| 엔드포인트 | 기능 | 쿼리 |
|---|---|---|
| `GET /api/scenarios` | 시나리오 목록 (목적값, 상한 대비 비율) | JPA |
| `GET /api/coverage-curve?scenario=` | 시나리오가 속한 조건의 곡선 (p, 목적값, 상한 대비) | JPA |
| `GET /api/stations?scenario=&bbox=` | 선택 대여소 GeoJSON FeatureCollection. bbox 없으면 전부 | `ST_Intersects` |
| `GET /api/stations/{id}/coverage?radius=300` | 후보지 반경 안 대표경로·통행 수 (가까운 순) | `ST_DWithin` |
| `GET /api/districts/{gu}/stats?scenario=` | 구 코드(11710) 또는 이름(송파구). 선택 대여소 수, 구를 지나는 통행 중 혜택 받는 비율 (행 수·가중치 기준) | `ST_Within` + 집계 |
| `GET /api/routes?scenario=` | 통행 선 GeoJSON + 그 시나리오의 혜택 (없으면 null) (11단계에서 추가) | 없음 |
| `GET /api/existing-stations` | 기존 따릉이 대여소 GeoJSON (번호, 이름, 자치구). 지도 표시 전용 | 없음 |
| `GET /api/districts` | 구 경계 GeoJSON + 구별 기본 통계. 경계는 5179에서 5m 허용오차로 단순화한 뒤 4326으로 변환, `ST_Multi`로 MultiPolygon 통일 (11단계에서 추가) | `ST_SimplifyPreserveTopology` |

- 좌표계: DB는 5179 그대로, 응답 도형만 `ST_AsGeoJSON(ST_Transform(geom, 4326), 6)`(모든 공간 응답 소수점 6자리)으로 DB에서 만들어 문자열 그대로 싣는다 (`@JsonRawValue`).
  bbox(4326)는 반대로 `ST_Transform(ST_MakeEnvelope(..., 4326), 5179)`로 바꿔 비교한다 (geom 쪽을 바꾸면 GIST 인덱스를 못 씀).
- bbox = `minLon,minLat,maxLon,maxLat`. 숫자 4개, 유한, 경위도 범위, 최소 < 최대가 아니면 400. radius는 0 < r ≤ 2000.
- 파라미터는 `:name` 바인딩만. `ddl-auto=validate`, `open-in-view=false`, Hikari `read-only`.
- 구별 커버율 = 구 경계와 만나는 통행 중 그 시나리오에서 혜택(> 0)을 받는 경로 비율 (`routeCoverageRate`), 통행 수 가중 (`tripCoverageRate`).

**실행**
```bash
export JAVA_HOME=<JDK 25>        # 예) C:/Users/user/.jdks/openjdk-25.0.1
export DB_URL=jdbc:postgresql://localhost:5432/bike_station DB_PASSWORD=<reader_password>   # DB_USERNAME 기본 bike_reader
export CORS_ALLOWED_ORIGINS=http://localhost:5173                                         # 쉼표로 여러 개
cd api && ./gradlew bootRun
./gradlew test                   # Docker 필요 (Testcontainers)
```

**테스트** (46개, Testcontainers `postgis/postgis:16-3.4`): 9단계 `sql/schema.sql`과 `sql/existing_station_info.sql`을 psql로 그대로 실행하고 `fixture.sql` 합성 데이터를 넣은 뒤,
Spring은 운영처럼 `bike_reader`로 접속한다. 그래서 `ddl-auto=validate`가 실제 DDL과 맞는지, 읽기 전용 권한이 막히는지(SQLSTATE 42501)도 함께 검증된다.
리포지토리(JPA·공간 쿼리 거리·bbox·구 집계), 전체 API(GeoJSON 형식, 400·404, CORS), bbox 파서를 확인한다.

**응답 시간** (2026-09-29, 실데이터 전체 적재, 로컬 Docker PostGIS + `java -jar`, 워밍업 5회 뒤 50회, `api/scripts/measure_api.py`)

| 엔드포인트 | 서버 중앙값 (p95) | 클라이언트 중앙값 (p95) | 응답 크기 |
|---|---|---|---|
| 시나리오 목록 | 4.7ms (6.5) | 7.4ms (9.5) | 4.3KB |
| 커버리지 곡선 | 5.7ms (7.0) | 8.3ms (10.1) | 0.6KB |
| 대여소 p=100 전체 / p=1000 전체 (506개) | 10.9ms (14.8), 두 경우 합산 | 10.9ms / 13.6ms | 22.7KB / 114KB |
| 대여소 p=1000 + bbox (서울 중심부) | 9.2ms (11.5) | 11.4ms (14.2) | 17.5KB |
| 대여소 반경 300m (통행이 가장 많이 걸린 후보지 1102) | 4.3ms (7.1) | 6.3ms (8.4) | 0.8KB |
| 구별 커버율 (송파구, p=100) | 7.4ms (8.2) | 9.3ms (10.1) | 0.3KB |
| 통행 선 (통행 369개, 꼭짓점 21,960개) | 약 31ms (첫 호출 374ms) | – | 609KB (p=100) / 613KB (p=1000) |
| 기존 대여소 (2,789개) | – | 31.7ms (144.6) | 540KB |
| 구 경계 (25개, 5m 단순화) | 약 17ms | – | 69KB |

서버 시간은 요청마다 `api.timing` 로그에 남는다 (`RequestTimingFilter`). 실데이터 정합성: 후보지 1102 반경 300m 경로 13개 = 3단계 `n_routes`,
송파구 선택 대여소(ST_Within) 9개 = `sgg_code` 기준 9개, 구를 지나는 경로 67개 = 8단계 `districts.n_routes`.

## 11단계: 지도 웹 (web/, Vite + TypeScript + MapLibre GL)

모든 데이터는 Spring API에서 받는다 (DB·파일에 직접 접근하지 않음). 좌표는 EPSG:4326 그대로 소스에 넣고 도형 계산은 하지 않는다.
배경지도는 OpenFreeMap(키 없음) Positron / Dark, 지명은 한글(`name:ko`)만 보이게 바꾼다. 화면 용어는 "대표경로" 대신 "통행"을 쓴다.

```bash
cd web
cp .env.example .env            # VITE_API_BASE=http://localhost:8080 (Spring API 주소)
npm install
npm run dev                     # http://localhost:5173 (API의 CORS 기본 허용 주소)
npm run build                   # 타입 검사 + dist/ (index.html, limits.html)
```

| 화면 | 내용 | API |
|---|---|---|
| 시나리오 전환 | 목적함수(거리 합 / 통행 비율 합). 도보 반경·통행 단위·설치 방식은 값이 두 개 이상일 때만 버튼이 나타난다 (민감도 결과가 들어오면 자동으로) | `/api/scenarios` |
| p 슬라이더 | 그 조건에 있는 p만 고를 수 있다. 목적값(상한 대비), 선택 대여소 수, 혜택 받는 통행 수 | `/api/stations`, `/api/routes` |
| 지도 | 선택 대여소(파랑, 끝으로 안 쓰인 것은 회색), 혜택 받는 통행(주황) / 없는 통행(회색), 구 경계. 대여소를 누르면 도보 반경 안 통행을 진하게, 구를 누르면 구별 커버율 | `/api/stations/{id}/coverage`, `/api/districts`, `/api/districts/{gu}/stats` |
| 기존 대여소 (빨간 점) | 체크박스 "기존 대여소"로 켜고 끈다 (기본 꺼짐, 처음 켤 때 한 번 받는다). 선택 대여소(파랑) 아래에 그리고 크기·테두리는 파란 점과 같다. 누르면 이름·번호 팝업 | `/api/existing-stations` |
| 커버리지 곡선 | x = p (로그 눈금), y = 상한 대비 목적값. 십자선 툴팁, 점을 누르면 그 p로, 표로 보기 | `/api/coverage-curve` |
| 기존 대여소 비교 | `build_type = existing` 시나리오(기준선)가 있으면 목적값 비교, 없으면 "데이터 없음" 안내 | `/api/scenarios` |
| 한계 설명 (`limits.html`) | 고정 설명 + API에서 매번 만드는 "지금 데이터 상태" (통행 수, 통행 단위, 도보 반경, 부트스트랩·기존 대여소·민감도 유무, 상한 도달 p, 경계 단순화) | 위 API들 |

- 색: 대여소 파랑 `#2a78d6`, 혜택 받는 통행 주황 `#eb6834` (어두운 테마 `#3987e5` / `#d95926`). 색각 이상 시뮬레이션 포함 팔레트 검사를 두 테마 모두 통과했다.
  기존 대여소 빨강 `#d62f2f` (어두운 테마 `#ef5350`)은 나중에 더했고 이 팔레트 검사는 하지 않았다 (주황 선과는 모양(점/선)으로 구분). 색은 `src/style.css`의 CSS 변수 한 곳에서 정하고 지도 레이어도 거기서 읽는다.
- 선택 상태는 주소에 남는다 (`?scenario=...`). 같은 시나리오를 다시 고르면 다시 받지 않는다 (통행 선이 약 600KB).
- MapLibre 6은 워커 경로를 실행 중에 계산하므로 `maplibre-gl-worker.mjs?worker&url`로 따로 묶어 `setWorkerUrl()`에 넘긴다.
- 확인 (2026-09-29, 실데이터, Chrome 헤드리스): 대여소·구 클릭, p·목적함수 전환, 주소 반영, 390px 폭에서 가로 넘침 없음, 콘솔 오류 없음.
- 구 경계 단순화(5m)로 생긴 인접 구 사이 틈: 가장 큰 조각(마포구·서대문구 경계 신촌로, 2,061m², 평균 폭 약 2.4m)이 확대 17에서 폭 2~3px 틈으로 보이고, 확대 15에서는 거의 안 보인다. 표시용이라 그대로 두었다 (구별 집계는 단순화 전 경계).
- 통행 수 342 / 369: 342는 여정 잇기 공백 상한이 없을 때 서울 여정 수, 369는 2026-09-28 확정한 15분 상한일 때 값이다 (`s0_join_stats.json`의 `max_gap_sensitivity`). 최종 결과는 369 기준이다.

## 입력 데이터에 대해 확인된 사실 (2022-09-15 ~ 10-30 실데이터, s0_session_links.py)

- 제공처 원본(`korea_gps_merged.csv`)은 **1분(최대 60점) 세그먼트** 단위이고 세그먼트마다 수단 라벨이 하나다. 레코드(segment_id의 4번째 필드)는 수단이 하나뿐이다.
- `track_point.csv`는 BIKE만 뽑은 것이고, 상위 추출 단계에서 **accuracy > 20m 포인트(1,005점)** 가 빠져 있다. 원본의 (사용자, 시각) 중복 360건은 좌표까지 같다.
- 세션 안 61초 공백과 세션 사이 공백 시간대에는 원본에도 어떤 수단의 데이터도 없다 (BIKE 라벨만 뽑아서 생긴 구멍이 아니다).
- 공백의 원인은 원본의 구성 방식이다. segment_id 5번째 필드 = 32자리 창 id + 순번(0~4).
  한 레코드를 **5분 창**으로 나누고 창마다 1분 조각 5개 중 **정확히 4개만** 들어 있다 (BIKE 창 830개 중 800개, 전 수단 동일).
  빠진 순번은 0~4에 고르게 퍼져 있다 → 빠진 순번이 1~3이면 세션 안 61초 구멍, 0이나 4면 4분짜리 세션이 된다.
  창 사이 공백은 301·601·901초처럼 5분의 배수로, 5분 창이 통째로 빠진 것이다.
  데이터셋을 만들 때 조각을 나눠 둔(예: 학습/검증 분할) 흔적으로 보이며, 빠진 조각이 다른 파일에 있는지 확인이 필요하다.
- 서울 통행 기준 추정이 필요한 시간: 세션 그대로 쓰면 14.8% (61초 구멍), 레코드로 묶으면 50.6%.
- 원 데이터셋: AI Hub `186.교통수단판별 데이터` (01-1.정식개방데이터, BIKE만, `C:\Users\user\Downloads`).
  - GPS BIKE 조각: Training 3,476 + Validation 434 = 3,910. `korea_gps_merged.csv`는 **Training만** 합친 것이다 (3,261).
    Training 중 빠진 215개는 행의 99.4%가 좌표 없는 빈 조각이라 쓸 데이터가 없다.
  - Validation 434개는 각각 5분 창의 빠진 1분 조각이다. 더하면 5분 창 869개 중 434개가 5조각으로 완성되고,
    나머지 435개 창은 여전히 1조각이 빈다 (공개되지 않은 Test 분할로 추정).
  - 라벨 파일: 초 단위 `label`(전부 1), `detail_label`(4: 3,486조각·113명, 10: 424조각·7명). 조각·레코드 안에서 값이 바뀌지 않는다.
    10의 의미는 데이터 설명서의 코드표로 확인해야 한다 (조각 속도 중앙값 17.1km/h로 4의 12.5km/h보다 빠르다).
  - Validation까지 쓰면 서울 레코드 기준 세션 안 구멍 비율 14.8% → 7.2%, 관측 시간 33.5 → 38.0시간.
    레코드 전체 시간 중 비관측은 55% → 50%로 거의 그대로다 (5분 창 자체가 빠진 부분은 원본에도 없음).
- 같은 레코드 안의 연속 세션은 90%가 공백 동안 5~25km/h로 이동한 거리만큼 떨어져 있다 (다른 레코드 사이는 4%). 세션은 긴 통행의 관측된 조각으로 보인다.

## 알려진 한계 (지금은 그대로 두고 기록만 함)

| 항목 | 내용 | 실데이터 규모 |
|---|---|---|
| 튐 필터의 계단형 이동 | 위치가 한 번 옮겨진 뒤 그대로 유지되면(계단형), 이상 이동 자체는 남고 옮겨진 뒤 정상 점들이 시간이 쌓여 60km/h 안에 도달 가능해질 때까지 지워진다. 진행 방향 위의 따라잡기(1초 33m)도 튐으로 지운다. | 튐 제거 통행 3개, 9점 (예: ddf80d15, f057bbea) |
| 여정 잇기의 긴 공백 | (해결: `JOIN_MAX_GAP_S` 추가) 상한 전에는 (다) 규칙에 공백 길이 상한이 없었다. 이은 공백은 중앙값 691초, 상위 10% 경계 2,401초, 최대 7,900초(2.2시간)다. 긴 공백을 이은 여정은 중간 경로 추정이 불확실하다. | 이은 146쌍 중 30분 이상 31쌍, 40분 이상 17쌍, 1시간 이상 3쌍 |
| 공백 연결은 가정이다 | 조각 사이 공백(최대 15분)의 경로는 OSRM 자전거 최단경로로 가정한다. Valhalla와 다른 길을 고르는 경우가 있다. 2곳은 필요 속도가 35·38km/h로, 실제로는 더 짧은(도로망에 없는) 길로 갔을 가능성이 있다. | 공백 연결 61곳, 필요 속도 25km/h 초과 2곳 |
| 도로에 없는 노드 쌍 | 경로 좌표에서 찾은 연속 노드가 OSM 웨이에서 이웃이 아니면 링크에서 뺀다 (매칭 끊김 앞뒤 등). | 104곳 |
| 경로 선 양 끝 (해결: 4단계에서 자름) | 1단계가 경로 시작·끝의 반쪽 링크를 링크 전체로 넣어 선이 실제 시종점보다 더 나가 있었다 (끝 링크가 긴 도로 구간). 4단계에서 2단계 시종점으로 자른다. 남은 문제: 시종점이 끝 링크 위에 없는 경로(대부분 조각 안 매칭 끊김이 있는 여정)는 자르지 못하고, 선이 실제 시종점보다 짧게 시작·끝난다. 3단계도 같은 선을 쓴다. | 자른 길이 27.0km (3.9%). 시종점이 선에서 1m 넘게 떨어진 곳 10곳(시점 5, 종점 5), 최대 74m |
| 공백 분할 경계 | `SPLIT_GAP_S = 300`은 `> 300`으로 판정하므로 정확히 300초 공백은 나누지 않는다. 그 공백은 586m 직선으로 남는다. | 1건 (f5b0ae78) |
| 기존 대여소 점이 통행 선을 가림 (웹) | 빨간 점은 선택 대여소(파랑) 아래지만 통행 선(주황) 위에 그린다. 서울 전체 배율에서 켜면 주황 선이 대부분 가려진다. 기본은 꺼짐이다. | 2,789개 |
| "기존 대여소 비교" 문구 (웹) | 빨간 점을 켜도 이 패널은 "기존 대여소 데이터가 아직 없습니다"라고 나온다. 기준선 시나리오(`build_type = existing`)가 없다는 뜻이고 위치 데이터와는 별개다. | – |
| API 응답 압축 없음 | 모든 엔드포인트가 압축 없이 나간다. 로컬에서는 차이가 작아 그대로 둔다. 다른 기기에서 접속하게 배포할 때 `server.compression.enabled: true`를 켠다. | 기존 대여소 540,463B (gzip이면 76,382B), 통행 선 609KB |
