package com.example.bikestation.controller;

import com.example.bikestation.dto.RouteFeatureCollection;
import com.example.bikestation.service.RouteService;
import jakarta.validation.constraints.NotBlank;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/routes")
public class RouteController {

	private final RouteService routeService;

	public RouteController(RouteService routeService) {
		this.routeService = routeService;
	}

	/** GET /api/routes?scenario={scenario_id}: 통행 선 GeoJSON과 그 시나리오의 혜택. */
	@GetMapping
	public RouteFeatureCollection routes(@RequestParam @NotBlank String scenario) {
		return routeService.features(scenario);
	}

}
