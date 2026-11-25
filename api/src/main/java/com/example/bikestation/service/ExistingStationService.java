package com.example.bikestation.service;

import com.example.bikestation.dto.ExistingStationFeatureCollection;
import com.example.bikestation.repository.ExistingStationInfoRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class ExistingStationService {

	private final ExistingStationInfoRepository stations;

	public ExistingStationService(ExistingStationInfoRepository stations) {
		this.stations = stations;
	}

	/** 기존 대여소 전부. 적재 전이면 빈 FeatureCollection. */
	public ExistingStationFeatureCollection features() {
		return ExistingStationFeatureCollection.of(stations.findFeatures());
	}

}
