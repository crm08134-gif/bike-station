-- =============================================================================
-- 9단계: 결과 DB 스키마 (PostGIS)
--
-- 스키마는 Python 쪽이 소유한다. 테이블 구조는 src/s8_export.py의 SCHEMA와 칼럼 이름·순서·타입이 같아야 한다
-- (int64 -> bigint, float64 -> double precision, string -> text, bool -> boolean, geom -> geometry(타입, 5179)).
-- 스키마를 바꾸면 s8_export.SCHEMA, 이 파일, Spring 엔티티·쿼리를 함께 고친다.
--
-- 이 파일은 적재 전에 먼저 실행한다. 다시 실행하면 bike 스키마의 결과 테이블을 지우고 새로 만든다 (데이터는 s9_load.py로 다시 넣는다).
-- 적재 스크립트는 테이블을 만들지 않고 데이터만 넣는다.
--
-- 실행 (psql 변수 reader_password 필수: Spring 읽기 전용 계정 비밀번호. 파일에 비밀번호를 쓰지 않는다)
--   psql -U postgres -d bike_station -v reader_password="$BIKE_READER_PASSWORD" -f sql/schema.sql
-- =============================================================================
\set ON_ERROR_STOP on

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS bike;

DROP TABLE IF EXISTS bike.route_benefit, bike.scenario_selection, bike.coverage_curve, bike.scenarios,
                     bike.existing_stations, bike.candidates, bike.routes, bike.districts CASCADE;

-- 구 경계와 구별 통계
CREATE TABLE bike.districts (
    sgg_code      text PRIMARY KEY,
    sgg_name      text NOT NULL,
    area_km2      double precision NOT NULL,
    n_candidates  bigint NOT NULL,
    n_routes      bigint NOT NULL,
    route_km      double precision NOT NULL,
    geom          geometry(MultiPolygon, 5179) NOT NULL
);

-- 후보지 (격자 셀 중심 + 기존 대여소)
CREATE TABLE bike.candidates (
    cand_id      bigint PRIMARY KEY,
    sgg_code     text REFERENCES bike.districts (sgg_code),
    n_routes     bigint NOT NULL,                  -- 반경 안 대표경로 수
    n_trips      bigint NOT NULL,                  -- 그 가중치 합
    is_existing  boolean NOT NULL,
    station_id   text,
    boot_freq    double precision CHECK (boot_freq BETWEEN 0 AND 1),   -- 부트스트랩 선택 빈도 (없으면 NULL)
    geom         geometry(Point, 5179) NOT NULL
);

-- 기존 대여소 (기준선 점수는 scenarios의 build_type = 'existing' 행)
CREATE TABLE bike.existing_stations (
    station_id  text PRIMARY KEY,
    cand_id     bigint NOT NULL REFERENCES bike.candidates (cand_id),
    sgg_code    text REFERENCES bike.districts (sgg_code),
    geom        geometry(Point, 5179) NOT NULL
);

-- 대표경로
CREATE TABLE bike.routes (
    route_id        bigint PRIMARY KEY,
    weight          bigint NOT NULL CHECK (weight > 0),                     -- 묶인 통행 수
    route_len_m     double precision NOT NULL,
    obs_ratio       double precision CHECK (obs_ratio BETWEEN 0 AND 1),     -- 관측 시간 비율
    repeat_ratio    double precision CHECK (repeat_ratio BETWEEN 0 AND 1),  -- 링크 반복 비율
    straight_ratio  double precision CHECK (straight_ratio BETWEEN 0 AND 1),-- 직선으로 이은 길이 비율
    n_breaks        bigint NOT NULL,
    geom            geometry(LineString, 5179) NOT NULL
);

-- 시나리오 = 조건(cond_id) + p. 민감도 결과는 조건별 행으로 추가된다
CREATE TABLE bike.scenarios (
    scenario_id       text PRIMARY KEY,            -- '{cond_id}-p{p}'
    cond_id           text NOT NULL,               -- '{mode}-{build_type}-w{walk_m}-{trip_unit}'
    mode              text NOT NULL CHECK (mode IN ('absolute', 'ratio')),
    build_type        text NOT NULL CHECK (build_type IN ('new', 'expand', 'existing')),
    walk_m            bigint NOT NULL CHECK (walk_m > 0),
    trip_unit         text NOT NULL,
    p                 bigint NOT NULL CHECK (p >= 0),
    objective         double precision NOT NULL,
    upper_bound       double precision,
    n_selected        bigint NOT NULL,
    n_routes_benefit  bigint NOT NULL,
    mip_gap           double precision,
    status            text NOT NULL,
    UNIQUE (cond_id, p)
);

-- 시나리오별 선택 후보지 (선택된 것만)
CREATE TABLE bike.scenario_selection (
    scenario_id  text NOT NULL REFERENCES bike.scenarios (scenario_id),
    cand_id      bigint NOT NULL REFERENCES bike.candidates (cand_id),
    used         boolean NOT NULL,                 -- 혜택 경로의 앞·뒤 끝으로 실제로 쓰였나
    PRIMARY KEY (scenario_id, cand_id)
);

-- 시나리오별 경로 혜택 (혜택 > 0만, 없는 행 = 0). 목적값 = sum(routes.weight * benefit)
CREATE TABLE bike.route_benefit (
    scenario_id  text NOT NULL REFERENCES bike.scenarios (scenario_id),
    route_id     bigint NOT NULL REFERENCES bike.routes (route_id),
    benefit_m    double precision NOT NULL CHECK (benefit_m > 0),
    benefit      double precision NOT NULL,        -- 변형 적용 (absolute: m, ratio: benefit_m / 경로 길이)
    start_cand   bigint NOT NULL REFERENCES bike.candidates (cand_id),
    end_cand     bigint NOT NULL REFERENCES bike.candidates (cand_id),
    PRIMARY KEY (scenario_id, route_id)
);

-- 조건별 커버리지 곡선 (p -> 목적값)
CREATE TABLE bike.coverage_curve (
    cond_id      text NOT NULL,
    mode         text NOT NULL CHECK (mode IN ('absolute', 'ratio')),
    build_type   text NOT NULL CHECK (build_type IN ('new', 'expand', 'existing')),
    walk_m       bigint NOT NULL CHECK (walk_m > 0),
    trip_unit    text NOT NULL,
    p            bigint NOT NULL CHECK (p >= 0),
    objective    double precision NOT NULL,
    upper_bound  double precision,
    PRIMARY KEY (cond_id, p)
);

-- 공간 인덱스
CREATE INDEX districts_geom_gist         ON bike.districts         USING gist (geom);
CREATE INDEX candidates_geom_gist        ON bike.candidates        USING gist (geom);
CREATE INDEX existing_stations_geom_gist ON bike.existing_stations USING gist (geom);
CREATE INDEX routes_geom_gist            ON bike.routes            USING gist (geom);
-- 조회용 인덱스 (외래키 반대 방향, 구별 집계)
CREATE INDEX candidates_sgg_code_idx          ON bike.candidates (sgg_code);
CREATE INDEX scenario_selection_cand_id_idx   ON bike.scenario_selection (cand_id);
CREATE INDEX route_benefit_route_id_idx       ON bike.route_benefit (route_id);

-- -----------------------------------------------------------------------------
-- Spring 전용 읽기 전용 계정
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bike_reader') THEN
        CREATE ROLE bike_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO bike_reader', current_database());
END
$$;
ALTER ROLE bike_reader PASSWORD :'reader_password';
ALTER ROLE bike_reader SET default_transaction_read_only = on;
ALTER ROLE bike_reader SET search_path = bike, public;

REVOKE ALL ON SCHEMA bike FROM PUBLIC;
GRANT USAGE ON SCHEMA bike TO bike_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA bike TO bike_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

COMMIT;
