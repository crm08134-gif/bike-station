package com.example.bikestation.repository;

import java.util.List;

import com.example.bikestation.domain.CoverageCurvePoint;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CoverageCurveRepository extends JpaRepository<CoverageCurvePoint, CoverageCurvePoint.Key> {

	List<CoverageCurvePoint> findByCondIdOrderByPAsc(String condId);

}
