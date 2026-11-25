package com.example.bikestation.repository;

import java.util.List;

import com.example.bikestation.domain.Candidate;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 후보지 공간 조회. geom은 EPSG:5179로 저장되어 있고, 4326 변환은 응답에 싣는 이 SELECT에서 한 번만 한다.
 * 화면 범위(bbox, 4326)는 반대로 5179로 바꿔 비교한다. geom 쪽을 바꾸면 GIST 인덱스를 못 쓰기 때문이다.
 */
public interface CandidateRepository extends JpaRepository<Candidate, Long> {

	String STATION_COLUMNS = """
			c.cand_id AS "candId", s.used AS "used", c.sgg_code AS "sggCode", c.n_routes AS "nRoutes",
			c.n_trips AS "nTrips", c.is_existing AS "existing", c.station_id AS "stationId", c.boot_freq AS "bootFreq",
			ST_AsGeoJSON(ST_Transform(c.geom, 4326), 6) AS "geometry"
			""";

	/** 시나리오의 선택 대여소 전부. */
	@Query(nativeQuery = true, value = "SELECT " + STATION_COLUMNS + """
			FROM bike.scenario_selection s
			JOIN bike.candidates c ON c.cand_id = s.cand_id
			WHERE s.scenario_id = :scenarioId
			ORDER BY c.cand_id
			""")
	List<StationRow> findStations(@Param("scenarioId") String scenarioId);

	/** 시나리오의 선택 대여소 중 화면 범위(EPSG:4326 경위도) 안에 있는 것. */
	@Query(nativeQuery = true, value = "SELECT " + STATION_COLUMNS + """
			FROM bike.scenario_selection s
			JOIN bike.candidates c ON c.cand_id = s.cand_id
			WHERE s.scenario_id = :scenarioId
			  AND ST_Intersects(c.geom,
			        ST_Transform(ST_MakeEnvelope(:minLon, :minLat, :maxLon, :maxLat, 4326), 5179))
			ORDER BY c.cand_id
			""")
	List<StationRow> findStationsInBbox(@Param("scenarioId") String scenarioId, @Param("minLon") double minLon,
			@Param("minLat") double minLat, @Param("maxLon") double maxLon, @Param("maxLat") double maxLat);

	/** 후보지에서 radiusM(m) 안을 지나는 통행. 거리는 5179 미터 좌표로 잰다 (4단계 접근점 표와 같은 직선거리). */
	@Query(nativeQuery = true, value = """
			SELECT r.route_id AS "routeId", r.weight AS "weight", ST_Distance(r.geom, c.geom) AS "distanceM"
			FROM bike.candidates c
			JOIN bike.routes r ON ST_DWithin(r.geom, c.geom, :radiusM)
			WHERE c.cand_id = :candId
			ORDER BY "distanceM", r.route_id
			""")
	List<NearbyRouteRow> findRoutesWithin(@Param("candId") long candId, @Param("radiusM") double radiusM);

}
