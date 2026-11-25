package com.example.bikestation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * bike.candidates (후보지). geom 칼럼은 매핑하지 않는다: 공간 조회는 CandidateRepository의 네이티브 쿼리가
 * ST_AsGeoJSON(ST_Transform(geom, 4326))으로 GeoJSON 문자열을 받아 온다 (Hibernate Spatial 없이).
 */
@Entity
@Immutable
@Table(schema = "bike", name = "candidates")
public class Candidate {

	@Id
	@Column(name = "cand_id")
	private Long candId;

	@Column(name = "sgg_code")
	private String sggCode;

	@Column(name = "n_routes")
	private Long nRoutes;

	@Column(name = "n_trips")
	private Long nTrips;

	@Column(name = "is_existing")
	private Boolean existing;

	@Column(name = "station_id")
	private String stationId;

	@Column(name = "boot_freq")
	private Double bootFreq;

	protected Candidate() {
	}

	public Long getCandId() {
		return candId;
	}

	public String getSggCode() {
		return sggCode;
	}

	public Long getNRoutes() {
		return nRoutes;
	}

	public Long getNTrips() {
		return nTrips;
	}

	public Boolean getExisting() {
		return existing;
	}

	public String getStationId() {
		return stationId;
	}

	public Double getBootFreq() {
		return bootFreq;
	}

}
