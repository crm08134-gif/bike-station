package com.example.bikestation.controller;

import java.util.List;

import com.example.bikestation.dto.ScenarioResponse;
import com.example.bikestation.service.ScenarioService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/scenarios")
public class ScenarioController {

	private final ScenarioService scenarioService;

	public ScenarioController(ScenarioService scenarioService) {
		this.scenarioService = scenarioService;
	}

	@GetMapping
	public List<ScenarioResponse> list() {
		return scenarioService.list();
	}

}
