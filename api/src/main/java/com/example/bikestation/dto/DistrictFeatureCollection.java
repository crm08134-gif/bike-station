package com.example.bikestation.dto;

import java.util.List;

import com.example.bikestation.repository.DistrictFeatureRow;
import com.fasterxml.jackson.annotation.JsonRawValue;

/**
 * 구 경계 GeoJSON FeatureCollection (EPSG:4326). simplifyToleranceM = 경계 단순화 허용오차 (m, EPSG:5179에서 적용).
 * nRoutes = 구를 지나는 통행 수, routeKm = 구 안을 지나는 통행 선 길이 합.
 */
public record DistrictFeatureCollection(String type, double simplifyToleranceM, List<Feature> features) {

	public static DistrictFeatureCollection of(double toleranceM, List<DistrictFeatureRow> rows) {
		return new DistrictFeatureCollection("FeatureCollection", toleranceM, rows.stream().map(Feature::from).toList());
	}

	public record Feature(String type, String id, @JsonRawValue String geometry, Properties properties) {

		static Feature from(DistrictFeatureRow r) {
			return new Feature("Feature", r.getSggCode(), r.getGeometry(), new Properties(r.getSggCode(),
					r.getSggName(), r.getAreaKm2(), r.getNCandidates(), r.getNRoutes(), r.getRouteKm()));
		}

	}

	public record Properties(String sggCode, String sggName, double areaKm2, long nCandidates, long nRoutes,
			double routeKm) {
	}

}
