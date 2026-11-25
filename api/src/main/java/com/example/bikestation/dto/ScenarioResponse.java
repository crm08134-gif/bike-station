package com.example.bikestation.dto;

import com.example.bikestation.domain.Scenario;

/**
 * 시나리오 한 개.
 * objectiveRatio = objective / upperBound (후보지를 전부 골랐을 때 대비). 상한이 없으면 null.
 */
public record ScenarioResponse(
		String scenarioId,
		String condId,
		String mode,
		String buildType,
		long walkM,
		String tripUnit,
		long p,
		double objective,
		Double upperBound,
		Double objectiveRatio,
		long nSelected,
		long nRoutesBenefit,
		Double mipGap,
		String status) {

	public static ScenarioResponse from(Scenario s) {
		Double ratio = (s.getUpperBound() == null || s.getUpperBound() == 0) ? null
				: s.getObjective() / s.getUpperBound();
		return new ScenarioResponse(s.getScenarioId(), s.getCondId(), s.getMode(), s.getBuildType(), s.getWalkM(),
				s.getTripUnit(), s.getP(), s.getObjective(), s.getUpperBound(), ratio, s.getNSelected(),
				s.getNRoutesBenefit(), s.getMipGap(), s.getStatus());
	}

}
