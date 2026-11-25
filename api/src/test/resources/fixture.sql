-- 통합 테스트용 합성 데이터 (EPSG:5179, 서울 근처 좌표). 테이블은 9단계 sql/schema.sql이 만든다.
--
--   y
--   1946000 +-----------------+-----------------+
--           |  가구 11110     |  나구 11140     |      c5 (950500,1945900) 선택 안 됨
--   1945500 c1-----R10-----c2 | c3------------c4|      R10 (w=2) y=1945500, x 950000~952000
--   1945300 R11 (w=1) --      |       R12 (w=3) |      R11 (w=1) y=1945300, x 950000~950800 (가구 안)
--           |                 |        |        |      R12 (w=3) x=951500, y 1945100~1945900 (나구 안)
--   1945000 +-----------------+-----------------+
--         950000            951000            952000
--   c1 (950100,1945500)  c2 (950900,1945500)  c3 (951100,1945500)  c4 (951900,1945500)
--
-- 도보 300m 안 경로: c1 -> R10(0m), R11(200m) / c2 -> R10(0m), R11(224m) / c3 -> R10 / c4 -> R10 / c5 -> 없음 (R10 400m)
-- 시나리오 absolute p2 = {c1, c4}: R10 혜택 1800 -> 목적값 2 x 1800 = 3600
--          absolute p4 = {c1..c4}: R10 1800, R11 700 (c1 a=100 ~ c2 a=800) -> 2 x 1800 + 700 = 4300
--          ratio p2    = {c1, c4}: R10 0.9 -> 1.8

INSERT INTO bike.districts VALUES
  ('11110', '가구', 1.0, 3, 2, 1.8, ST_GeomFromText('MULTIPOLYGON(((950000 1945000, 951000 1945000, 951000 1946000, 950000 1946000, 950000 1945000)))', 5179)),
  ('11140', '나구', 1.0, 2, 2, 1.8, ST_GeomFromText('MULTIPOLYGON(((951000 1945000, 952000 1945000, 952000 1946000, 951000 1946000, 951000 1945000)))', 5179));

INSERT INTO bike.candidates VALUES
  (1, '11110', 2, 3, false, NULL, 0.9,  ST_GeomFromText('POINT(950100 1945500)', 5179)),
  (2, '11110', 2, 3, false, NULL, 0.5,  ST_GeomFromText('POINT(950900 1945500)', 5179)),
  (3, '11140', 1, 2, false, NULL, NULL, ST_GeomFromText('POINT(951100 1945500)', 5179)),
  (4, '11140', 1, 2, true,  'ST1', NULL, ST_GeomFromText('POINT(951900 1945500)', 5179)),
  (5, '11110', 0, 0, false, NULL, NULL, ST_GeomFromText('POINT(950500 1945900)', 5179));

INSERT INTO bike.existing_stations VALUES
  ('ST1', 4, '11140', ST_GeomFromText('POINT(951900 1945500)', 5179));

-- 기존 대여소 표시용 (sql/existing_station_info.sql). 번호는 글자라 '102' < '2001' 순, 이름이 없는 행 하나
INSERT INTO bike.existing_station_info VALUES
  ('2001', NULL,               '나구', ST_GeomFromText('POINT(951900 1945500)', 5179)),
  ('102',  '망원역 1번출구 앞', '가구', ST_GeomFromText('POINT(950123.456 1945500.789)', 5179));

INSERT INTO bike.routes VALUES
  (10, 2, 2000, 1.0, 0.0, 0.0, 0, ST_GeomFromText('LINESTRING(950000 1945500, 952000 1945500)', 5179)),
  (11, 1,  800, 0.8, 0.0, 0.0, 0, ST_GeomFromText('LINESTRING(950000 1945300, 950800 1945300)', 5179)),
  (12, 3,  800, 1.0, 0.1, 0.2, 1, ST_GeomFromText('LINESTRING(951500 1945100, 951500 1945900)', 5179));

INSERT INTO bike.scenarios VALUES
  ('absolute-new-w300-journey_gap15m-p2', 'absolute-new-w300-journey_gap15m', 'absolute', 'new', 300, 'journey_gap15m', 2, 3600, 4300,  2, 1, 0, 'optimal'),
  ('absolute-new-w300-journey_gap15m-p4', 'absolute-new-w300-journey_gap15m', 'absolute', 'new', 300, 'journey_gap15m', 4, 4300, 4300,  4, 2, 0, 'optimal'),
  ('ratio-new-w300-journey_gap15m-p2',    'ratio-new-w300-journey_gap15m',    'ratio',    'new', 300, 'journey_gap15m', 2, 1.8,  2.675, 2, 1, 0, 'optimal');

INSERT INTO bike.scenario_selection VALUES
  ('absolute-new-w300-journey_gap15m-p2', 1, true), ('absolute-new-w300-journey_gap15m-p2', 4, true),
  ('absolute-new-w300-journey_gap15m-p4', 1, true), ('absolute-new-w300-journey_gap15m-p4', 2, true),
  ('absolute-new-w300-journey_gap15m-p4', 3, true), ('absolute-new-w300-journey_gap15m-p4', 4, true),
  ('ratio-new-w300-journey_gap15m-p2',    1, true), ('ratio-new-w300-journey_gap15m-p2',    4, true);

INSERT INTO bike.route_benefit VALUES
  ('absolute-new-w300-journey_gap15m-p2', 10, 1800, 1800, 1, 4),
  ('absolute-new-w300-journey_gap15m-p4', 10, 1800, 1800, 1, 4),
  ('absolute-new-w300-journey_gap15m-p4', 11,  700,  700, 1, 2),
  ('ratio-new-w300-journey_gap15m-p2',    10, 1800,  0.9, 1, 4);

INSERT INTO bike.coverage_curve VALUES
  ('absolute-new-w300-journey_gap15m', 'absolute', 'new', 300, 'journey_gap15m', 1, 0,    4300),
  ('absolute-new-w300-journey_gap15m', 'absolute', 'new', 300, 'journey_gap15m', 2, 3600, 4300),
  ('absolute-new-w300-journey_gap15m', 'absolute', 'new', 300, 'journey_gap15m', 3, 3600, 4300),
  ('absolute-new-w300-journey_gap15m', 'absolute', 'new', 300, 'journey_gap15m', 4, 4300, 4300),
  ('ratio-new-w300-journey_gap15m',    'ratio',    'new', 300, 'journey_gap15m', 2, 1.8,  2.675);
