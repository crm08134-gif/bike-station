package com.example.bikestation.dto;

import java.util.List;

import com.example.bikestation.repository.ExistingStationRow;
import com.fasterxml.jackson.annotation.JsonRawValue;

/**
 * 기존 따릉이 대여소 GeoJSON FeatureCollection (EPSG:4326, 지도 표시 전용).
 * geometry는 PostGIS가 만든 GeoJSON 문자열을 그대로 싣는다 (@JsonRawValue).
 */
public record ExistingStationFeatureCollection(String type, List<Feature> features) {

	public static ExistingStationFeatureCollection of(List<ExistingStationRow> rows) {
		return new ExistingStationFeatureCollection("FeatureCollection", rows.stream().map(Feature::from).toList());
	}

	public record Feature(String type, String id, @JsonRawValue String geometry, Properties properties) {

		static Feature from(ExistingStationRow r) {
			return new Feature("Feature", r.getStationId(), r.getGeometry(),
					new Properties(r.getStationId(), r.getStationName(), r.getGuName()));
		}

	}

	/** stationId = 대여소 번호, stationName = 대여소 이름, guName = 자치구 이름 (엑셀 값 그대로). */
	public record Properties(String stationId, String stationName, String guName) {
	}

}
