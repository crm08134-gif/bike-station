package com.example.bikestation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * bike.existing_station_info (기존 따릉이 대여소, 지도 표시 전용). sql/existing_station_info.sql이 만들고
 * src/load_existing_stations.py가 엑셀에서 채운다. 후보지(candidates)와 연결하지 않는다.
 * geom은 매핑하지 않는다 (공간 조회는 ExistingStationInfoRepository 네이티브 쿼리).
 */
@Entity
@Immutable
@Table(schema = "bike", name = "existing_station_info")
public class ExistingStationInfo {

	@Id
	@Column(name = "station_id")
	private String stationId;

	@Column(name = "station_name")
	private String stationName;

	@Column(name = "gu_name")
	private String guName;

	protected ExistingStationInfo() {
	}

	public String getStationId() {
		return stationId;
	}

	public String getStationName() {
		return stationName;
	}

	public String getGuName() {
		return guName;
	}

}
