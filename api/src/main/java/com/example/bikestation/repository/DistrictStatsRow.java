package com.example.bikestation.repository;

/** 구 하나의 시나리오별 집계 (네이티브 쿼리 결과). */
public interface DistrictStatsRow {

	String getSggCode();

	String getSggName();

	Double getAreaKm2();

	Long getNCandidates();

	/** 구 안(ST_Within)의 선택 대여소 수 */
	Long getNSelected();

	/** 구를 지나는(ST_Intersects) 통행 행 수와 가중치(묶인 통행 수) 합 */
	Long getNRoutes();

	Long getNTrips();

	/** 그중 이 시나리오에서 혜택(> 0)을 받는 통행 행 수와 가중치 합 */
	Long getNRoutesBenefit();

	Long getNTripsBenefit();

}
