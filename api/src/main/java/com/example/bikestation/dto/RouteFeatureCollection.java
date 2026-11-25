package com.example.bikestation.dto;

import java.util.List;

import com.example.bikestation.repository.RouteFeatureRow;
import com.fasterxml.jackson.annotation.JsonRawValue;

/**
 * 통행 선 GeoJSON FeatureCollection (EPSG:4326) + 시나리오별 혜택.
 * geometry는 PostGIS가 만든 GeoJSON 문자열을 그대로 싣는다 (@JsonRawValue).
 */
public record RouteFeatureCollection(String type, String scenarioId, List<Feature> features) {

	public static RouteFeatureCollection of(String scenarioId, List<RouteFeatureRow> rows) {
		return new RouteFeatureCollection("FeatureCollection", scenarioId, rows.stream().map(Feature::from).toList());
	}

	public record Feature(String type, long id, @JsonRawValue String geometry, Properties properties) {

		static Feature from(RouteFeatureRow r) {
			return new Feature("Feature", r.getRouteId(), r.getGeometry(),
					new Properties(r.getRouteId(), r.getWeight(), r.getRouteLenM(), r.getObsRatio(),
							r.getRepeatRatio(), r.getStraightRatio(), r.getNBreaks(), r.getBenefitM(), r.getBenefit(),
							r.getStartCand(), r.getEndCand()));
		}

	}

	/**
	 * weight = 묶인 통행 수. obsRatio = 관측 시간 비율, repeatRatio = 링크 반복 비율, straightRatio = 직선으로 이은 길이 비율.
	 * benefitM·benefit = 이 시나리오에서 대여소 사이를 탈 수 있는 구간 (m, 변형 적용). 혜택이 없으면 null.
	 * startCand·endCand = 그 구간의 양 끝 대여소(후보지 id).
	 */
	public record Properties(long routeId, long weight, double routeLenM, Double obsRatio, Double repeatRatio,
			Double straightRatio, long nBreaks, Double benefitM, Double benefit, Long startCand, Long endCand) {
	}

}
