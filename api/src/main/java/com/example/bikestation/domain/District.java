package com.example.bikestation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/** bike.districts (구 경계와 구별 통계). geom은 매핑하지 않는다 (공간 집계는 DistrictRepository 네이티브 쿼리). */
@Entity
@Immutable
@Table(schema = "bike", name = "districts")
public class District {

	@Id
	@Column(name = "sgg_code")
	private String sggCode;

	@Column(name = "sgg_name")
	private String sggName;

	@Column(name = "area_km2")
	private Double areaKm2;

	@Column(name = "n_candidates")
	private Long nCandidates;

	@Column(name = "n_routes")
	private Long nRoutes;

	@Column(name = "route_km")
	private Double routeKm;

	protected District() {
	}

	public String getSggCode() {
		return sggCode;
	}

	public String getSggName() {
		return sggName;
	}

	public Double getAreaKm2() {
		return areaKm2;
	}

	public Long getNCandidates() {
		return nCandidates;
	}

	public Long getNRoutes() {
		return nRoutes;
	}

	public Double getRouteKm() {
		return routeKm;
	}

}
