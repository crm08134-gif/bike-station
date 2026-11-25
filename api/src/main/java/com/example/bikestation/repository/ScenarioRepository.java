package com.example.bikestation.repository;

import java.util.List;

import com.example.bikestation.domain.Scenario;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ScenarioRepository extends JpaRepository<Scenario, String> {

	List<Scenario> findAllByOrderByCondIdAscPAsc();

}
