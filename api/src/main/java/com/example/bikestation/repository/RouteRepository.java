package com.example.bikestation.repository;

import java.util.List;

import com.example.bikestation.domain.Route;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface RouteRepository extends JpaRepository<Route, Long> {

	/** 통행 선 전부와 그 시나리오의 혜택 (route_benefit에 행이 없으면 혜택 0 -> null로 돌려준다). */
	@Query(nativeQuery = true, value = """
			SELECT r.route_id AS "routeId", r.weight AS "weight", r.route_len_m AS "routeLenM",
			       r.obs_ratio AS "obsRatio", r.repeat_ratio AS "repeatRatio", r.straight_ratio AS "straightRatio",
			       r.n_breaks AS "nBreaks", b.benefit_m AS "benefitM", b.benefit AS "benefit",
			       b.start_cand AS "startCand", b.end_cand AS "endCand",
			       ST_AsGeoJSON(ST_Transform(r.geom, 4326), 6) AS "geometry"
			FROM bike.routes r
			LEFT JOIN bike.route_benefit b ON b.route_id = r.route_id AND b.scenario_id = :scenarioId
			ORDER BY r.route_id
			""")
	List<RouteFeatureRow> findFeatures(@Param("scenarioId") String scenarioId);

}
