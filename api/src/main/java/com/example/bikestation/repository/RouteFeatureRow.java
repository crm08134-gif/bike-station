package com.example.bikestation.repository;

/**
 * 통행 선 한 행 + 시나리오별 혜택 (네이티브 쿼리 결과). 혜택이 없는 통행은 benefit* 와 start/endCand가 null.
 * geometry는 PostGIS가 만든 GeoJSON 문자열 (EPSG:4326, 소수점 6자리).
 */
public interface RouteFeatureRow {

	Long getRouteId();

	Long getWeight();

	Double getRouteLenM();

	Double getObsRatio();

	Double getRepeatRatio();

	Double getStraightRatio();

	Long getNBreaks();

	Double getBenefitM();

	Double getBenefit();

	Long getStartCand();

	Long getEndCand();

	String getGeometry();

}
