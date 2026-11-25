package com.example.bikestation.dto;

/**
 * 구별 커버율 (시나리오 하나).
 * routeCoverageRate = nRoutesBenefit / nRoutes : 구를 지나는 통행 중 혜택을 받는 비율
 * tripCoverageRate  = nTripsBenefit / nTrips   : 같은 비율을 통행 수로 가중
 * 구를 지나는 경로가 없으면 두 비율은 null.
 */
public record DistrictStatsResponse(
		String sggCode,
		String sggName,
		String scenarioId,
		double areaKm2,
		long nCandidates,
		long nSelected,
		long nRoutes,
		long nTrips,
		long nRoutesBenefit,
		long nTripsBenefit,
		Double routeCoverageRate,
		Double tripCoverageRate) {
}
