package com.example.bikestation.repository;

/**
 * 선택 대여소 한 행 (네이티브 쿼리 결과). geometry는 PostGIS가 만든 GeoJSON 문자열 (EPSG:4326)이다.
 * 네이티브 쿼리 별칭은 따옴표로 감싸 대소문자를 유지한다 (PostgreSQL은 따옴표 없는 별칭을 소문자로 바꾼다).
 */
public interface StationRow {

	Long getCandId();

	Boolean getUsed();

	String getSggCode();

	Long getNRoutes();

	Long getNTrips();

	Boolean getExisting();

	String getStationId();

	Double getBootFreq();

	String getGeometry();

}
