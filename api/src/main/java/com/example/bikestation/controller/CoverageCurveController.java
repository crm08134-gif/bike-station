package com.example.bikestation.controller;

import com.example.bikestation.dto.CoverageCurveResponse;
import com.example.bikestation.service.ScenarioService;
import jakarta.validation.constraints.NotBlank;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/coverage-curve")
public class CoverageCurveController {

	private final ScenarioService scenarioService;

	public CoverageCurveController(ScenarioService scenarioService) {
		this.scenarioService = scenarioService;
	}

	/** GET /api/coverage-curve?scenario={scenario_id}: 그 시나리오가 속한 조건의 곡선. */
	@GetMapping
	public CoverageCurveResponse curve(@RequestParam @NotBlank String scenario) {
		return scenarioService.coverageCurve(scenario);
	}

}
