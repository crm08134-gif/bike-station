package com.example.bikestation.repository;

import java.util.List;
import java.util.Optional;

import com.example.bikestation.domain.District;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface DistrictRepository extends JpaRepository<District, String> {

	/**
	 * 구 경계 전부. 단순화는 EPSG:5179(미터)에서 toleranceM으로 한 뒤 4326으로 바꾼다.
	 * 구마다 따로 단순화하므로 인접 구 경계에 폭 수 m 이하의 틈·겹침이 생길 수 있다 (표시용).
	 * 단순화하면 조각이 하나인 MultiPolygon이 Polygon이 되므로 ST_Multi로 타입을 MultiPolygon에 맞춘다.
	 */
	@Query(nativeQuery = true, value = """
			SELECT d.sgg_code AS "sggCode", d.sgg_name AS "sggName", d.area_km2 AS "areaKm2",
			       d.n_candidates AS "nCandidates", d.n_routes AS "nRoutes", d.route_km AS "routeKm",
			       ST_AsGeoJSON(ST_Transform(ST_Multi(ST_SimplifyPreserveTopology(d.geom, :toleranceM)), 4326), 6) AS "geometry"
			FROM bike.districts d
			ORDER BY d.sgg_code
			""")
	List<DistrictFeatureRow> findFeatures(@Param("toleranceM") double toleranceM);

	/**
	 * 구(코드 또는 이름) 하나의 시나리오별 집계. 모든 도형은 EPSG:5179 그대로 비교한다.
	 * - 선택 대여소: ST_Within(대여소, 구 경계)
	 * - 경로: 구 경계와 만나는(ST_Intersects) 통행, 혜택은 route_benefit에 행이 있는 경로 (행이 없으면 혜택 0)
	 */
	@Query(nativeQuery = true, value = """
			SELECT d.sgg_code AS "sggCode", d.sgg_name AS "sggName", d.area_km2 AS "areaKm2",
			       d.n_candidates AS "nCandidates",
			       (SELECT count(*)
			          FROM bike.scenario_selection s
			          JOIN bike.candidates c ON c.cand_id = s.cand_id
			         WHERE s.scenario_id = :scenarioId AND ST_Within(c.geom, d.geom)) AS "nSelected",
			       rt.n_routes AS "nRoutes", rt.n_trips AS "nTrips",
			       rt.n_routes_benefit AS "nRoutesBenefit", rt.n_trips_benefit AS "nTripsBenefit"
			FROM bike.districts d
			CROSS JOIN LATERAL (
			    SELECT count(*) AS n_routes,
			           coalesce(sum(r.weight), 0)::bigint AS n_trips,
			           count(b.route_id) AS n_routes_benefit,
			           coalesce(sum(r.weight) FILTER (WHERE b.route_id IS NOT NULL), 0)::bigint AS n_trips_benefit
			      FROM bike.routes r
			      LEFT JOIN bike.route_benefit b ON b.route_id = r.route_id AND b.scenario_id = :scenarioId
			     WHERE ST_Intersects(r.geom, d.geom)
			) rt
			WHERE d.sgg_code = :gu OR d.sgg_name = :gu
			""")
	Optional<DistrictStatsRow> findStats(@Param("gu") String gu, @Param("scenarioId") String scenarioId);

}
