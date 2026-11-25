-- =============================================================================
-- 기존 따릉이 대여소 위치 (지도 표시 전용)
--
-- 9단계 결과 테이블(sql/schema.sql)과 따로 둔다.
--   - bike.existing_stations는 8단계 계약(s8_export.SCHEMA)에 묶인 "후보지로 쓰인 기존 대여소"이고 cand_id가 필수다.
--     이 테이블은 엑셀 원본을 지도에 띄우기만 하는 참고 자료라 후보지와 연결하지 않는다 (외래키 없음).
--   - schema.sql의 DROP과 s9_load.py의 TRUNCATE는 자기 테이블 8개만 대상으로 하므로 이 테이블은 지워지지 않는다.
--     반대로 이 파일을 다시 실행하면 이 테이블만 지우고 새로 만든다 (데이터는 load_existing_stations.py로 다시 넣는다).
--
-- schema.sql을 먼저 실행해 bike 스키마와 bike_reader 계정이 있어야 한다.
-- 실행
--   psql -U postgres -d bike_station -f sql/existing_station_info.sql
-- =============================================================================
\set ON_ERROR_STOP on

BEGIN;

DROP TABLE IF EXISTS bike.existing_station_info;

CREATE TABLE bike.existing_station_info (
    station_id    text PRIMARY KEY,                -- 대여소 번호 (엑셀 A열)
    station_name  text,                            -- 보관소(대여소)명 (앞뒤 공백 제거)
    gu_name       text,                            -- 자치구 이름 (엑셀 값 그대로, 구 코드 아님)
    geom          geometry(Point, 5179) NOT NULL
);

CREATE INDEX existing_station_info_geom_gist ON bike.existing_station_info USING gist (geom);

-- Spring 읽기 전용 계정. schema.sql의 GRANT SELECT ON ALL TABLES는 그 시점에 있던 테이블에만 적용되므로 따로 준다.
GRANT SELECT ON bike.existing_station_info TO bike_reader;

COMMIT;
