package com.example.bikestation.repository;

import java.util.List;

import com.example.bikestation.domain.ExistingStationInfo;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface ExistingStationInfoRepository extends JpaRepository<ExistingStationInfo, String> {

	/** 기존 대여소 전부 (번호순). 좌표는 4326, 소수점 6자리 (약 0.1m). */
	@Query(nativeQuery = true, value = """
			SELECT e.station_id AS "stationId", e.station_name AS "stationName", e.gu_name AS "guName",
			       ST_AsGeoJSON(ST_Transform(e.geom, 4326), 6) AS "geometry"
			FROM bike.existing_station_info e
			ORDER BY e.station_id
			""")
	List<ExistingStationRow> findFeatures();

}
