package com.example.bikestation.repository;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.tuple;

import com.example.bikestation.domain.CoverageCurvePoint;
import com.example.bikestation.support.PostgisTestContainer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.boot.jdbc.test.autoconfigure.AutoConfigureTestDatabase;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
class CoverageCurveRepositoryTest {

	@DynamicPropertySource
	static void db(DynamicPropertyRegistry registry) {
		PostgisTestContainer.register(registry);
	}

	@Autowired
	CoverageCurveRepository curves;

	@Test
	void 조건별_곡선을_p_순서로_읽는다() {
		assertThat(curves.findByCondIdOrderByPAsc("absolute-new-w300-journey_gap15m"))
			.extracting(CoverageCurvePoint::getP, CoverageCurvePoint::getObjective)
			.containsExactly(tuple(1L, 0.0), tuple(2L, 3600.0),
					tuple(3L, 3600.0), tuple(4L, 4300.0));
		assertThat(curves.findByCondIdOrderByPAsc("ratio-new-w300-journey_gap15m")).hasSize(1);
		assertThat(curves.findByCondIdOrderByPAsc("없는-조건")).isEmpty();
	}

	@Test
	void 복합키로_찾는다() {
		assertThat(curves.findById(new CoverageCurvePoint.Key("absolute-new-w300-journey_gap15m", 4L))).get()
			.extracting(CoverageCurvePoint::getUpperBound)
			.isEqualTo(4300.0);
	}

}
