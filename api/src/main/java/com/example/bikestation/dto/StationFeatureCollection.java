package com.example.bikestation.dto;

import java.util.List;

import com.example.bikestation.repository.StationRow;
import com.fasterxml.jackson.annotation.JsonRawValue;

/**
 * 선택 대여소 GeoJSON FeatureCollection (EPSG:4326).
 * geometry는 PostGIS가 만든 GeoJSON 문자열을 파싱하지 않고 그대로 싣는다 (@JsonRawValue).
 */
public record StationFeatureCollection(String type, String scenarioId, List<Feature> features) {

	public static StationFeatureCollection of(String scenarioId, List<StationRow> rows) {
		return new StationFeatureCollection("FeatureCollection", scenarioId, rows.stream().map(Feature::from).toList());
	}

	public record Feature(String type, long id, @JsonRawValue String geometry, Properties properties) {

		static Feature from(StationRow r) {
			return new Feature("Feature", r.getCandId(), r.getGeometry(),
					new Properties(r.getCandId(), r.getUsed(), r.getSggCode(), r.getNRoutes(), r.getNTrips(),
							r.getExisting(), r.getStationId(), r.getBootFreq()));
		}

	}

	/**
	 * used: 혜택 경로의 앞·뒤 끝으로 실제로 쓰였나. nRoutes·nTrips: 도보 반경 안 통행 행 수·가중치 합 (3단계).
	 * bootFreq: 부트스트랩 선택 빈도 (아직 계산 전이면 null).
	 */
	public record Properties(long candId, boolean used, String sggCode, long nRoutes, long nTrips, boolean existing,
			String stationId, Double bootFreq) {
	}

}
