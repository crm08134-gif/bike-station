package com.example.bikestation.service;

import com.example.bikestation.dto.RouteFeatureCollection;
import com.example.bikestation.repository.RouteRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class RouteService {

	private final ScenarioService scenarioService;

	private final RouteRepository routes;

	public RouteService(ScenarioService scenarioService, RouteRepository routes) {
		this.scenarioService = scenarioService;
		this.routes = routes;
	}

	/** 통행 선 전부 + 시나리오별 혜택. 없는 시나리오면 404. */
	public RouteFeatureCollection features(String scenarioId) {
		scenarioService.get(scenarioId);
		return RouteFeatureCollection.of(scenarioId, routes.findFeatures(scenarioId));
	}

}
