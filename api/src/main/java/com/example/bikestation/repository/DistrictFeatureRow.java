package com.example.bikestation.repository;

/** 구 경계 한 행 (네이티브 쿼리 결과). geometry는 단순화한 GeoJSON 문자열 (EPSG:4326, 소수점 6자리). */
public interface DistrictFeatureRow {

	String getSggCode();

	String getSggName();

	Double getAreaKm2();

	Long getNCandidates();

	/** 구를 지나는 통행 수 (8단계 계산값) */
	Long getNRoutes();

	/** 구 안을 지나는 통행 선 길이 합 (km) */
	Double getRouteKm();

	String getGeometry();

}
