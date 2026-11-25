package com.example.bikestation.repository;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.example.bikestation.support.PostgisTestContainer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.boot.jdbc.test.autoconfigure.AutoConfigureTestDatabase;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

/** 네이티브 공간 쿼리 (fixture.sql 그림 참고). */
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
class SpatialRepositoryTest {

	static final String ABS_P2 = "absolute-new-w300-journey_gap15m-p2";

	static final String ABS_P4 = "absolute-new-w300-journey_gap15m-p4";

	static final Pattern POINT = Pattern.compile("\\{\"type\":\"Point\",\"coordinates\":\\[([-0-9.]+),([-0-9.]+)\\]\\}");

	@DynamicPropertySource
	static void db(DynamicPropertyRegistry registry) {
		PostgisTestContainer.register(registry);
	}

	@Autowired
	CandidateRepository candidates;

	@Autowired
	DistrictRepository districts;

	static double[] lonLat(String geojson) {
		Matcher m = POINT.matcher(geojson);
		assertThat(m.matches()).as("GeoJSON Point: %s", geojson).isTrue();
		return new double[] { Double.parseDouble(m.group(1)), Double.parseDouble(m.group(2)) };
	}

	@Test
	void 선택_대여소를_4326_GeoJSON으로_받는다() {
		List<StationRow> rows = candidates.findStations(ABS_P4);
		assertThat(rows).extracting(StationRow::getCandId).containsExactly(1L, 2L, 3L, 4L);
		StationRow c1 = rows.get(0);
		assertThat(c1.getUsed()).isTrue();
		assertThat(c1.getSggCode()).isEqualTo("11110");
		assertThat(c1.getBootFreq()).isEqualTo(0.9);
		assertThat(rows.get(2).getBootFreq()).isNull();
		assertThat(rows.get(3).getExisting()).isTrue();
		assertThat(rows.get(3).getStationId()).isEqualTo("ST1");
		double[] ll = lonLat(c1.getGeometry());
		assertThat(ll[0]).as("경도").isBetween(126.0, 128.0);
		assertThat(ll[1]).as("위도").isBetween(37.0, 38.0);
		assertThat(candidates.findStations("없는-시나리오")).isEmpty();
	}

	@Test
	void bbox_안의_대여소만_받는다() {
		double[] ll = lonLat(candidates.findStations(ABS_P4).get(0).getGeometry()); // c1
		double d = 0.001; // 약 90~110m. c2는 800m 떨어져 있다
		List<StationRow> rows = candidates.findStationsInBbox(ABS_P4, ll[0] - d, ll[1] - d, ll[0] + d, ll[1] + d);
		assertThat(rows).extracting(StationRow::getCandId).containsExactly(1L);
		// 선택되지 않은 후보지(c5)는 bbox 안이어도 나오지 않는다
		assertThat(candidates.findStationsInBbox(ABS_P2, 120, 30, 135, 45)).extracting(StationRow::getCandId)
			.containsExactly(1L, 4L);
		assertThat(candidates.findStationsInBbox(ABS_P4, 0, 0, 1, 1)).isEmpty();
	}

	@Test
	void 대여소_반경_안_경로를_가까운_순서로_받는다() {
		List<NearbyRouteRow> c1 = candidates.findRoutesWithin(1L, 300);
		assertThat(c1).extracting(NearbyRouteRow::getRouteId).containsExactly(10L, 11L);
		assertThat(c1.get(0).getDistanceM()).isCloseTo(0.0, within(1e-9));
		assertThat(c1.get(1).getDistanceM()).isCloseTo(200.0, within(1e-9));
		assertThat(c1).extracting(NearbyRouteRow::getWeight).containsExactly(2L, 1L);
		assertThat(candidates.findRoutesWithin(2L, 300)).extracting(NearbyRouteRow::getDistanceM)
			.satisfiesExactly(v -> assertThat(v).isCloseTo(0.0, within(1e-9)),
					v -> assertThat(v).isCloseTo(Math.hypot(100, 200), within(1e-9)));
		assertThat(candidates.findRoutesWithin(1L, 100)).extracting(NearbyRouteRow::getRouteId).containsExactly(10L);
		assertThat(candidates.findRoutesWithin(5L, 300)).as("c5는 R10에서 400m").isEmpty();
		assertThat(candidates.findRoutesWithin(5L, 400)).extracting(NearbyRouteRow::getRouteId).containsExactly(10L);
	}

	@Test
	void 구별_집계() {
		DistrictStatsRow a = districts.findStats("11110", ABS_P2).orElseThrow();
		assertThat(a.getSggName()).isEqualTo("가구");
		assertThat(a.getNCandidates()).isEqualTo(3L);
		assertThat(a.getNSelected()).as("p2 = {c1, c4} 중 가구 안은 c1").isEqualTo(1L);
		assertThat(a.getNRoutes()).as("R10, R11").isEqualTo(2L);
		assertThat(a.getNTrips()).isEqualTo(3L);
		assertThat(a.getNRoutesBenefit()).as("R10만 혜택").isEqualTo(1L);
		assertThat(a.getNTripsBenefit()).isEqualTo(2L);

		DistrictStatsRow a4 = districts.findStats("가구", ABS_P4).orElseThrow();
		assertThat(a4.getSggCode()).as("이름으로도 찾는다").isEqualTo("11110");
		assertThat(a4.getNSelected()).isEqualTo(2L);
		assertThat(a4.getNRoutesBenefit()).isEqualTo(2L);
		assertThat(a4.getNTripsBenefit()).isEqualTo(3L);

		DistrictStatsRow b = districts.findStats("11140", ABS_P2).orElseThrow();
		assertThat(b.getNSelected()).isEqualTo(1L);
		assertThat(b.getNRoutes()).as("R10(경계를 넘음), R12").isEqualTo(2L);
		assertThat(b.getNTrips()).isEqualTo(5L);
		assertThat(b.getNRoutesBenefit()).isEqualTo(1L);
		assertThat(b.getNTripsBenefit()).isEqualTo(2L);

		assertThat(districts.findStats("99999", ABS_P2)).isEmpty();
	}

}
