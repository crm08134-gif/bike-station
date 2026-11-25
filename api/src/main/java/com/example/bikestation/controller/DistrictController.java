package com.example.bikestation.controller;

import com.example.bikestation.dto.DistrictFeatureCollection;
import com.example.bikestation.dto.DistrictStatsResponse;
import com.example.bikestation.service.DistrictService;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/districts")
public class DistrictController {

	private final DistrictService districtService;

	public DistrictController(DistrictService districtService) {
		this.districtService = districtService;
	}

	/** GET /api/districts: 구 경계 GeoJSON (단순화) 과 구별 기본 통계. */
	@GetMapping
	public DistrictFeatureCollection districts() {
		return districtService.features();
	}

	/** GET /api/districts/{gu}/stats?scenario={scenario_id}. gu = 구 코드(11710) 또는 이름(송파구). */
	@GetMapping("/{gu}/stats")
	public DistrictStatsResponse stats(@PathVariable @NotBlank @Size(max = 20) String gu,
			@RequestParam @NotBlank String scenario) {
		return districtService.stats(gu, scenario);
	}

}
