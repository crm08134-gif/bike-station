package com.example.bikestation.controller;

import static org.hamcrest.Matchers.closeTo;
import static org.hamcrest.Matchers.contains;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasKey;
import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.bikestation.support.PostgisTestContainer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

/** 전체 스택 (컨트롤러 -> 서비스 -> 리포지토리 -> PostGIS). 데이터는 fixture.sql. */
@SpringBootTest
@AutoConfigureMockMvc
class ApiIntegrationTest {

	static final String ABS_P2 = "absolute-new-w300-journey_gap15m-p2";

	static final String ABS_P4 = "absolute-new-w300-journey_gap15m-p4";

	@DynamicPropertySource
	static void db(DynamicPropertyRegistry registry) {
		PostgisTestContainer.register(registry);
	}

	@Autowired
	MockMvc mvc;

	// ---- /api/scenarios

	@Test
	void 시나리오_목록() throws Exception {
		mvc.perform(get("/api/scenarios"))
			.andExpect(status().isOk())
			.andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
			.andExpect(jsonPath("$.length()").value(3))
			.andExpect(jsonPath("$[*].scenarioId").value(contains(ABS_P2, ABS_P4, "ratio-new-w300-journey_gap15m-p2")))
			.andExpect(jsonPath("$[1].p").value(4))
			.andExpect(jsonPath("$[1].objective").value(4300.0))
			.andExpect(jsonPath("$[1].objectiveRatio").value(1.0))
			.andExpect(jsonPath("$[0].objectiveRatio").value(closeTo(3600.0 / 4300, 1e-12)))
			.andExpect(jsonPath("$[0].nSelected").value(2))
			.andExpect(jsonPath("$[0].walkM").value(300));
	}

	// ---- /api/coverage-curve

	@Test
	void 커버리지_곡선() throws Exception {
		mvc.perform(get("/api/coverage-curve").param("scenario", ABS_P2))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.condId").value("absolute-new-w300-journey_gap15m"))
			.andExpect(jsonPath("$.scenarioP").value(2))
			.andExpect(jsonPath("$.upperBound").value(4300.0))
			.andExpect(jsonPath("$.points[*].p").value(contains(1, 2, 3, 4)))
			.andExpect(jsonPath("$.points[3].objectiveRatio").value(1.0));
	}

	@Test
	void 커버리지_곡선_오류() throws Exception {
		mvc.perform(get("/api/coverage-curve")).andExpect(status().isBadRequest());
		mvc.perform(get("/api/coverage-curve").param("scenario", " ")).andExpect(status().isBadRequest());
		mvc.perform(get("/api/coverage-curve").param("scenario", "없는-시나리오"))
			.andExpect(status().isNotFound())
			.andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON));
	}

	// ---- /api/stations

	@Test
	void 선택_대여소_GeoJSON() throws Exception {
		mvc.perform(get("/api/stations").param("scenario", ABS_P4))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.type").value("FeatureCollection"))
			.andExpect(jsonPath("$.features.length()").value(4))
			.andExpect(jsonPath("$.features[0].type").value("Feature"))
			.andExpect(jsonPath("$.features[0].id").value(1))
			.andExpect(jsonPath("$.features[0].geometry.type").value("Point"))
			.andExpect(jsonPath("$.features[0].geometry.coordinates[0]").value(closeTo(127.0, 1.0)))
			.andExpect(jsonPath("$.features[0].geometry.coordinates[1]").value(closeTo(37.5, 0.5)))
			.andExpect(jsonPath("$.features[0].properties.used").value(true))
			.andExpect(jsonPath("$.features[0].properties.bootFreq").value(0.9))
			.andExpect(jsonPath("$.features[2].properties.bootFreq").value(nullValue()))
			.andExpect(jsonPath("$.features[3].properties.stationId").value("ST1"))
			// 엔티티를 그대로 내보내지 않는다 (DTO에 없는 칼럼 없음)
			.andExpect(jsonPath("$.features[0].properties", not(hasKey("geom"))));
	}

	@Test
	void 화면_범위_안_대여소() throws Exception {
		mvc.perform(get("/api/stations").param("scenario", ABS_P2).param("bbox", "120,30,135,45"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.features[*].id").value(contains(1, 4)));
		mvc.perform(get("/api/stations").param("scenario", ABS_P2).param("bbox", "0,0,1,1"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.features.length()").value(0));
	}

	@ParameterizedTest
	@ValueSource(strings = { "1,2,3", "a,b,c,d", "127.2,37.4,126.8,37.7", "200,37,201,38", "NaN,37,127,38", "1,2,3,4,5" })
	void 잘못된_bbox는_400(String bbox) throws Exception {
		mvc.perform(get("/api/stations").param("scenario", ABS_P2).param("bbox", bbox))
			.andExpect(status().isBadRequest())
			.andExpect(jsonPath("$.detail").value(containsString("bbox")));
	}

	@Test
	void 없는_시나리오는_404() throws Exception {
		mvc.perform(get("/api/stations").param("scenario", "없는-시나리오")).andExpect(status().isNotFound());
	}

	// ---- /api/stations/{id}/coverage

	@Test
	void 대여소_반경_통행량() throws Exception {
		mvc.perform(get("/api/stations/1/coverage"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.radiusM").value(300.0))
			.andExpect(jsonPath("$.nRoutes").value(2))
			.andExpect(jsonPath("$.nTrips").value(3))
			.andExpect(jsonPath("$.routes[*].routeId").value(contains(10, 11)))
			.andExpect(jsonPath("$.routes[1].distanceM").value(closeTo(200.0, 1e-9)));
		mvc.perform(get("/api/stations/1/coverage").param("radius", "100"))
			.andExpect(jsonPath("$.nRoutes").value(1))
			.andExpect(jsonPath("$.nTrips").value(2));
		mvc.perform(get("/api/stations/5/coverage")).andExpect(jsonPath("$.nRoutes").value(0));
	}

	@Test
	void 대여소_반경_통행량_오류() throws Exception {
		mvc.perform(get("/api/stations/999/coverage")).andExpect(status().isNotFound());
		mvc.perform(get("/api/stations/abc/coverage")).andExpect(status().isBadRequest());
		mvc.perform(get("/api/stations/0/coverage")).andExpect(status().isBadRequest());
		mvc.perform(get("/api/stations/1/coverage").param("radius", "0")).andExpect(status().isBadRequest());
		mvc.perform(get("/api/stations/1/coverage").param("radius", "5000")).andExpect(status().isBadRequest());
	}

	// ---- /api/districts/{gu}/stats

	@Test
	void 구별_커버율() throws Exception {
		mvc.perform(get("/api/districts/11110/stats").param("scenario", ABS_P2))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.sggName").value("가구"))
			.andExpect(jsonPath("$.nSelected").value(1))
			.andExpect(jsonPath("$.nRoutes").value(2))
			.andExpect(jsonPath("$.nRoutesBenefit").value(1))
			.andExpect(jsonPath("$.routeCoverageRate").value(0.5))
			.andExpect(jsonPath("$.tripCoverageRate").value(closeTo(2.0 / 3, 1e-12)));
		mvc.perform(get("/api/districts/가구/stats").param("scenario", ABS_P4))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.sggCode").value("11110"))
			.andExpect(jsonPath("$.routeCoverageRate").value(1.0));
	}

	@Test
	void 구별_커버율_오류() throws Exception {
		mvc.perform(get("/api/districts/99999/stats").param("scenario", ABS_P2)).andExpect(status().isNotFound());
		mvc.perform(get("/api/districts/11110/stats").param("scenario", "없는-시나리오"))
			.andExpect(status().isNotFound());
		mvc.perform(get("/api/districts/11110/stats")).andExpect(status().isBadRequest());
	}

	// ---- /api/routes

	@Test
	void 통행_선과_시나리오별_혜택() throws Exception {
		mvc.perform(get("/api/routes").param("scenario", ABS_P2))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.type").value("FeatureCollection"))
			.andExpect(jsonPath("$.features[*].id").value(contains(10, 11, 12)))
			.andExpect(jsonPath("$.features[0].geometry.type").value("LineString"))
			.andExpect(jsonPath("$.features[0].geometry.coordinates.length()").value(2))
			.andExpect(jsonPath("$.features[0].properties.weight").value(2))
			.andExpect(jsonPath("$.features[0].properties.benefitM").value(1800.0))
			.andExpect(jsonPath("$.features[0].properties.startCand").value(1))
			.andExpect(jsonPath("$.features[0].properties.endCand").value(4))
			.andExpect(jsonPath("$.features[1].properties.benefitM").value(nullValue()))
			.andExpect(jsonPath("$.features[2].properties.straightRatio").value(0.2));
		mvc.perform(get("/api/routes").param("scenario", "ratio-new-w300-journey_gap15m-p2"))
			.andExpect(jsonPath("$.features[0].properties.benefit").value(0.9));
		mvc.perform(get("/api/routes").param("scenario", ABS_P4))
			.andExpect(jsonPath("$.features[1].properties.benefitM").value(700.0));
	}

	@Test
	void 통행_선_오류() throws Exception {
		mvc.perform(get("/api/routes")).andExpect(status().isBadRequest());
		mvc.perform(get("/api/routes").param("scenario", "없는-시나리오")).andExpect(status().isNotFound());
	}

	@Test
	void 좌표는_소수점_6자리까지() throws Exception {
		for (String url : new String[] { "/api/routes?scenario=" + ABS_P2, "/api/stations?scenario=" + ABS_P2,
				"/api/districts", "/api/existing-stations" }) {
			String body = mvc.perform(get(url)).andReturn().getResponse().getContentAsString();
			java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"coordinates\":\\[(.*?)\\]\\}").matcher(body);
			org.assertj.core.api.Assertions.assertThat(m.find()).as(url).isTrue();
			java.util.regex.Matcher num = java.util.regex.Pattern.compile("-?\\d+\\.(\\d+)").matcher(body);
			while (num.find()) {
				if (body.lastIndexOf("\"coordinates\"", num.start()) > body.lastIndexOf("\"properties\"", num.start())) {
					org.assertj.core.api.Assertions.assertThat(num.group(1).length()).as(url).isLessThanOrEqualTo(6);
				}
			}
		}
	}

	// ---- /api/districts

	@Test
	void 구_경계() throws Exception {
		mvc.perform(get("/api/districts"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.type").value("FeatureCollection"))
			.andExpect(jsonPath("$.simplifyToleranceM").value(5.0))
			.andExpect(jsonPath("$.features[*].id").value(contains("11110", "11140")))
			.andExpect(jsonPath("$.features[0].geometry.type").value("MultiPolygon"))
			.andExpect(jsonPath("$.features[0].geometry.coordinates[0][0][0][0]").value(closeTo(127.0, 1.0)))
			.andExpect(jsonPath("$.features[0].properties.sggName").value("가구"))
			.andExpect(jsonPath("$.features[0].properties.nCandidates").value(3))
			.andExpect(jsonPath("$.features[0].properties.nRoutes").value(2));
	}

	// ---- /api/existing-stations

	@Test
	void 기존_대여소() throws Exception {
		mvc.perform(get("/api/existing-stations"))
			.andExpect(status().isOk())
			.andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
			.andExpect(jsonPath("$.type").value("FeatureCollection"))
			.andExpect(jsonPath("$.features[*].id").value(contains("102", "2001")))
			.andExpect(jsonPath("$.features[0].geometry.type").value("Point"))
			.andExpect(jsonPath("$.features[0].geometry.coordinates[0]").value(closeTo(127.0, 1.0)))
			.andExpect(jsonPath("$.features[0].geometry.coordinates[1]").value(closeTo(37.5, 0.5)))
			.andExpect(jsonPath("$.features[0].properties.stationId").value("102"))
			.andExpect(jsonPath("$.features[0].properties.stationName").value("망원역 1번출구 앞"))
			.andExpect(jsonPath("$.features[0].properties.guName").value("가구"))
			.andExpect(jsonPath("$.features[1].properties.stationName").value(nullValue()));
	}

	// ---- CORS

	@Test
	void CORS는_프론트엔드_주소만_GET_허용() throws Exception {
		mvc.perform(options("/api/scenarios").header("Origin", "http://localhost:5173")
			.header("Access-Control-Request-Method", "GET"))
			.andExpect(status().isOk())
			.andExpect(header().string("Access-Control-Allow-Origin", "http://localhost:5173"));
		mvc.perform(options("/api/scenarios").header("Origin", "http://evil.example")
			.header("Access-Control-Request-Method", "GET")).andExpect(status().isForbidden());
		mvc.perform(options("/api/scenarios").header("Origin", "http://localhost:5173")
			.header("Access-Control-Request-Method", "DELETE")).andExpect(status().isForbidden());
	}

}
