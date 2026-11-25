package com.example.bikestation.repository;

/** 대여소 도보 반경 안 통행 한 행 (네이티브 쿼리 결과). distanceM = 대여소에서 경로 선까지 직선거리 (m, EPSG:5179). */
public interface NearbyRouteRow {

	Long getRouteId();

	Long getWeight();

	Double getDistanceM();

}
