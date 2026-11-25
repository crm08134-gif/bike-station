package com.example.bikestation.support;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import org.springframework.test.context.DynamicPropertyRegistry;
import org.testcontainers.containers.Container.ExecResult;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;

/**
 * 모든 통합 테스트가 함께 쓰는 PostGIS 컨테이너 (JVM당 한 번 뜬다).
 *
 * 1) 9단계 sql/schema.sql을 psql로 그대로 실행한다 (테이블, GIST 인덱스, 읽기 전용 계정 bike_reader)
 *    이어서 sql/existing_station_info.sql (기존 대여소 표시용 테이블, 9단계와 별도 파일)
 * 2) fixture.sql로 합성 데이터를 넣는다 (관리자 계정)
 * 3) Spring은 운영과 같이 bike_reader로 접속한다 -> ddl-auto=validate와 읽기 전용 권한이 테스트에서도 검증된다
 */
public final class PostgisTestContainer {

	public static final String READER_USER = "bike_reader";

	public static final String READER_PASSWORD = "reader-test-password";

	// 결과 DB(docker/postgis/docker-compose.yml)와 같은 이미지
	private static final DockerImageName IMAGE = DockerImageName.parse("postgis/postgis:16-3.4")
		.asCompatibleSubstituteFor("postgres");

	private static final PostgreSQLContainer CONTAINER = start();

	private PostgisTestContainer() {
	}

	private static PostgreSQLContainer start() {
		Path schemaSql = Path.of(System.getProperty("bikestation.schema-sql", "../sql/schema.sql"));
		if (!Files.exists(schemaSql)) {
			throw new IllegalStateException("9단계 DDL을 찾을 수 없습니다: " + schemaSql.toAbsolutePath());
		}
		Path existingSql = Path.of(System.getProperty("bikestation.existing-station-sql",
				"../sql/existing_station_info.sql"));
		if (!Files.exists(existingSql)) {
			throw new IllegalStateException("기존 대여소 DDL을 찾을 수 없습니다: " + existingSql.toAbsolutePath());
		}
		PostgreSQLContainer c = new PostgreSQLContainer(IMAGE).withDatabaseName("bike_station")
			.withCopyFileToContainer(MountableFile.forHostPath(schemaSql), "/tmp/schema.sql")
			.withCopyFileToContainer(MountableFile.forHostPath(existingSql), "/tmp/existing_station_info.sql")
			.withCopyFileToContainer(MountableFile.forClasspathResource("fixture.sql"), "/tmp/fixture.sql");
		c.start();
		psql(c, "-v", "reader_password=" + READER_PASSWORD, "-f", "/tmp/schema.sql");
		psql(c, "-f", "/tmp/existing_station_info.sql");
		psql(c, "-f", "/tmp/fixture.sql");
		return c;
	}

	private static void psql(PostgreSQLContainer c, String... args) {
		List<String> cmd = new ArrayList<>(List.of("psql", "-v", "ON_ERROR_STOP=1", "-q", "-U", c.getUsername(), "-d",
				c.getDatabaseName()));
		cmd.addAll(List.of(args));
		try {
			ExecResult r = c.execInContainer(cmd.toArray(String[]::new));
			if (r.getExitCode() != 0) {
				throw new IllegalStateException("psql 실패 " + cmd + "\n" + r.getStderr());
			}
		}
		catch (Exception e) {
			throw new IllegalStateException(e);
		}
	}

	/** Spring 접속 정보: 운영과 같은 읽기 전용 계정. */
	public static void register(DynamicPropertyRegistry registry) {
		registry.add("spring.datasource.url", CONTAINER::getJdbcUrl);
		registry.add("spring.datasource.username", () -> READER_USER);
		registry.add("spring.datasource.password", () -> READER_PASSWORD);
	}

}
