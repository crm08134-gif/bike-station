package com.example.bikestation.controller;

import com.example.bikestation.config.BikestationProperties;
import com.example.bikestation.dto.StationCoverageResponse;
import com.example.bikestation.dto.StationFeatureCollection;
import com.example.bikestation.service.StationService;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/stations")
public class StationController {

	private final StationService stationService;

	private final BikestationProperties properties;

	public StationController(StationService stationService, BikestationProperties properties) {
		this.stationService = stationService;
		this.properties = properties;
	}

	/**
	 * GET /api/stations?scenario={scenario_id}&bbox=minLon,minLat,maxLon,maxLat
	 * 시나리오의 선택 대여소 GeoJSON. bbox(EPSG:4326)를 주면 화면 범위 안만, 없으면 전부.
	 */
	@GetMapping
	public StationFeatureCollection stations(@RequestParam @NotBlank String scenario,
			@RequestParam(required = false) String bbox) {
		return stationService.stations(scenario, bbox == null ? null : BboxParser.parse(bbox));
	}

	/**
	 * GET /api/stations/{id}/coverage?radius=300
	 * 대여소(후보지 id) 도보 반경 안 통행과 통행량. radius(m)를 안 주면 bikestation.coverage.default-radius-m.
	 */
	@GetMapping("/{id}/coverage")
	public StationCoverageResponse coverage(@PathVariable @Positive long id,
			@RequestParam(required = false) @Positive @DecimalMax("2000") Double radius) {
		double radiusM = (radius == null) ? properties.coverage().defaultRadiusM() : radius;
		return stationService.coverage(id, radiusM);
	}

}
