package com.example.bikestation.controller;

import com.example.bikestation.dto.ExistingStationFeatureCollection;
import com.example.bikestation.service.ExistingStationService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/existing-stations")
public class ExistingStationController {

	private final ExistingStationService existingStationService;

	public ExistingStationController(ExistingStationService existingStationService) {
		this.existingStationService = existingStationService;
	}

	/** GET /api/existing-stations: 기존 따릉이 대여소 위치 GeoJSON (번호, 이름, 자치구). */
	@GetMapping
	public ExistingStationFeatureCollection existingStations() {
		return existingStationService.features();
	}

}
