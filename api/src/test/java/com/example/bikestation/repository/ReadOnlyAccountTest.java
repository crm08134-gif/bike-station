package com.example.bikestation.repository;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

import javax.sql.DataSource;

import com.example.bikestation.support.PostgisTestContainer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.boot.jdbc.test.autoconfigure.AutoConfigureTestDatabase;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/** Spring이 쓰는 접속이 9단계의 읽기 전용 계정이고, 쓰기가 막혀 있는지 확인한다. */
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Transactional(propagation = Propagation.NOT_SUPPORTED)
class ReadOnlyAccountTest {

	@DynamicPropertySource
	static void db(DynamicPropertyRegistry registry) {
		PostgisTestContainer.register(registry);
	}

	@Autowired
	DataSource dataSource;

	@Test
	void 접속_계정은_bike_reader() throws SQLException {
		try (Connection c = dataSource.getConnection(); Statement st = c.createStatement();
				ResultSet rs = st.executeQuery("SELECT current_user")) {
			rs.next();
			assertThat(rs.getString(1)).isEqualTo(PostgisTestContainer.READER_USER);
		}
	}

	@Test
	void 읽기_전용_트랜잭션을_풀어도_권한으로_막힌다() throws SQLException {
		try (Connection c = dataSource.getConnection(); Statement st = c.createStatement()) {
			c.setReadOnly(false);
			c.setAutoCommit(true);
			st.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE");
			for (String sql : new String[] { "INSERT INTO bike.scenarios (scenario_id) VALUES ('x')",
					"UPDATE bike.routes SET weight = 1", "DELETE FROM bike.candidates", "TRUNCATE bike.routes",
					"CREATE TABLE bike.t (x int)" }) {
				assertThatThrownBy(() -> st.execute(sql)).as(sql)
					.isInstanceOf(SQLException.class)
					.extracting(e -> ((SQLException) e).getSQLState())
					.as("insufficient_privilege: %s", sql)
					.isEqualTo("42501");
			}
		}
	}

}
