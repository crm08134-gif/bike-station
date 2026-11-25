package com.example.bikestation.repository;

/**
 * 기존 대여소 한 행 (네이티브 쿼리 결과). geometry는 PostGIS가 만든 GeoJSON 문자열 (EPSG:4326, 소수점 6자리).
 */
public interface ExistingStationRow {

	String getStationId();

	String getStationName();

	String getGuName();

	String getGeometry();

}
