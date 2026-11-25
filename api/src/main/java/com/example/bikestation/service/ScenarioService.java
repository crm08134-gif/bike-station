package com.example.bikestation.service;

import java.util.List;
import java.util.Objects;

import com.example.bikestation.domain.CoverageCurvePoint;
import com.example.bikestation.domain.Scenario;
import com.example.bikestation.dto.CoverageCurveResponse;
import com.example.bikestation.dto.ScenarioResponse;
import com.example.bikestation.repository.CoverageCurveRepository;
import com.example.bikestation.repository.ScenarioRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class ScenarioService {

	private final ScenarioRepository scenarios;

	private final CoverageCurveRepository coverageCurves;

	public ScenarioService(ScenarioRepository scenarios, CoverageCurveRepository coverageCurves) {
		this.scenarios = scenarios;
		this.coverageCurves = coverageCurves;
	}

	public List<ScenarioResponse> list() {
		return scenarios.findAllByOrderByCondIdAscPAsc().stream().map(ScenarioResponse::from).toList();
	}

	/** 시나리오가 속한 조건(cond_id)의 곡선 전체. 곡선 점이 없으면 points가 빈 목록이다. */
	public CoverageCurveResponse coverageCurve(String scenarioId) {
		Scenario s = get(scenarioId);
		List<CoverageCurvePoint> rows = coverageCurves.findByCondIdOrderByPAsc(s.getCondId());
		Double upperBound = rows.stream()
			.map(CoverageCurvePoint::getUpperBound)
			.filter(Objects::nonNull)
			.findFirst()
			.orElse(s.getUpperBound());
		List<CoverageCurveResponse.Point> points = rows.stream()
			.map(r -> new CoverageCurveResponse.Point(r.getP(), r.getObjective(),
					(upperBound == null || upperBound == 0) ? null : r.getObjective() / upperBound))
			.toList();
		return new CoverageCurveResponse(s.getCondId(), s.getMode(), s.getBuildType(), s.getWalkM(), s.getTripUnit(),
				upperBound, s.getP(), points);
	}

	/** 다른 서비스가 시나리오 존재 여부와 조건(cond_id)을 확인할 때 쓴다. */
	public Scenario get(String scenarioId) {
		return scenarios.findById(scenarioId)
			.orElseThrow(() -> new NotFoundException("시나리오가 없습니다: " + scenarioId));
	}

}
