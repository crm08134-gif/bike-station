package com.example.bikestation.dto;

import java.util.List;

/**
 * 대여소 도보 반경 안 통행량. nRoutes = 반경 안 통행 행 수, nTrips = 그 가중치(묶인 통행 수) 합.
 * routes는 가까운 순서.
 */
public record StationCoverageResponse(long candId, double radiusM, long nRoutes, long nTrips, List<Route> routes) {

	public record Route(long routeId, long weight, double distanceM) {
	}

}
