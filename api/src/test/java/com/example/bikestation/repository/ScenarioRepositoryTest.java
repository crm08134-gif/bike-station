package com.example.bikestation.repository;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import com.example.bikestation.domain.Scenario;
import com.example.bikestation.support.PostgisTestContainer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.boot.jdbc.test.autoconfigure.AutoConfigureTestDatabase;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
class ScenarioRepositoryTest {

	@DynamicPropertySource
	static void db(DynamicPropertyRegistry registry) {
		PostgisTestContainer.register(registry);
	}

	@Autowired
	ScenarioRepository scenarios;

	@Test
	void 조건_p_순서로_전부_읽는다() {
		List<Scenario> all = scenarios.findAllByOrderByCondIdAscPAsc();
		assertThat(all).extracting(Scenario::getScenarioId)
			.containsExactly("absolute-new-w300-journey_gap15m-p2", "absolute-new-w300-journey_gap15m-p4",
					"ratio-new-w300-journey_gap15m-p2");
		Scenario p4 = all.get(1);
		assertThat(p4.getCondId()).isEqualTo("absolute-new-w300-journey_gap15m");
		assertThat(p4.getP()).isEqualTo(4L);
		assertThat(p4.getWalkM()).isEqualTo(300L);
		assertThat(p4.getObjective()).isEqualTo(4300.0);
		assertThat(p4.getNSelected()).isEqualTo(4L);
		assertThat(p4.getNRoutesBenefit()).isEqualTo(2L);
	}

	@Test
	void id로_찾는다() {
		assertThat(scenarios.findById("ratio-new-w300-journey_gap15m-p2")).get()
			.extracting(Scenario::getObjective)
			.isEqualTo(1.8);
		assertThat(scenarios.findById("없는-시나리오")).isEmpty();
	}

}
