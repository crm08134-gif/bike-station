package com.example.bikestation.service;

import com.example.bikestation.config.BikestationProperties;
import com.example.bikestation.dto.DistrictFeatureCollection;
import com.example.bikestation.dto.DistrictStatsResponse;
import com.example.bikestation.repository.DistrictRepository;
import com.example.bikestation.repository.DistrictStatsRow;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class DistrictService {

	private final ScenarioService scenarioService;

	private final DistrictRepository districts;

	private final BikestationProperties properties;

	public DistrictService(ScenarioService scenarioService, DistrictRepository districts,
			BikestationProperties properties) {
		this.scenarioService = scenarioService;
		this.districts = districts;
		this.properties = properties;
	}

	/** 구 경계 전부 (bikestation.districts.simplify-tolerance-m으로 단순화). */
	public DistrictFeatureCollection features() {
		double tol = properties.districts().simplifyToleranceM();
		return DistrictFeatureCollection.of(tol, districts.findFeatures(tol));
	}

	/** gu = 구 코드(예: 11710) 또는 이름(예: 송파구). 없는 시나리오·구면 404. */
	public DistrictStatsResponse stats(String gu, String scenarioId) {
		scenarioService.get(scenarioId);
		DistrictStatsRow r = districts.findStats(gu, scenarioId)
			.orElseThrow(() -> new NotFoundException("구가 없습니다: " + gu));
		return new DistrictStatsResponse(r.getSggCode(), r.getSggName(), scenarioId, r.getAreaKm2(),
				r.getNCandidates(), r.getNSelected(), r.getNRoutes(), r.getNTrips(), r.getNRoutesBenefit(),
				r.getNTripsBenefit(), rate(r.getNRoutesBenefit(), r.getNRoutes()),
				rate(r.getNTripsBenefit(), r.getNTrips()));
	}

	private static Double rate(long part, long whole) {
		return whole == 0 ? null : (double) part / whole;
	}

}
