package com.example.bikestation.service;

import java.util.List;

import com.example.bikestation.dto.Bbox;
import com.example.bikestation.dto.StationCoverageResponse;
import com.example.bikestation.dto.StationFeatureCollection;
import com.example.bikestation.repository.CandidateRepository;
import com.example.bikestation.repository.StationRow;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class StationService {

	private final ScenarioService scenarioService;

	private final CandidateRepository candidates;

	public StationService(ScenarioService scenarioService, CandidateRepository candidates) {
		this.scenarioService = scenarioService;
		this.candidates = candidates;
	}

	/** 시나리오의 선택 대여소. bbox가 null이면 전부. 없는 시나리오면 404. */
	public StationFeatureCollection stations(String scenarioId, Bbox bbox) {
		scenarioService.get(scenarioId);
		List<StationRow> rows = (bbox == null) ? candidates.findStations(scenarioId)
				: candidates.findStationsInBbox(scenarioId, bbox.minLon(), bbox.minLat(), bbox.maxLon(), bbox.maxLat());
		return StationFeatureCollection.of(scenarioId, rows);
	}

	/** 후보지 반경 안 통행과 통행량. 없는 후보지면 404. */
	public StationCoverageResponse coverage(long candId, double radiusM) {
		if (!candidates.existsById(candId)) {
			throw new NotFoundException("대여소(후보지)가 없습니다: " + candId);
		}
		List<StationCoverageResponse.Route> routes = candidates.findRoutesWithin(candId, radiusM)
			.stream()
			.map(r -> new StationCoverageResponse.Route(r.getRouteId(), r.getWeight(), r.getDistanceM()))
			.toList();
		long nTrips = routes.stream().mapToLong(StationCoverageResponse.Route::weight).sum();
		return new StationCoverageResponse(candId, radiusM, routes.size(), nTrips, routes);
	}

}
