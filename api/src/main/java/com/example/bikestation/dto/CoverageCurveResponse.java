package com.example.bikestation.dto;

import java.util.List;

/**
 * 조건 하나의 커버리지 곡선. scenarioP는 요청한 시나리오의 p (곡선 위에 표시할 점).
 * points[].objectiveRatio = objective / upperBound (상한이 없으면 null).
 */
public record CoverageCurveResponse(
		String condId,
		String mode,
		String buildType,
		long walkM,
		String tripUnit,
		Double upperBound,
		long scenarioP,
		List<Point> points) {

	public record Point(long p, double objective, Double objectiveRatio) {
	}

}
