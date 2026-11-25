package com.example.bikestation.config;

import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** application.yml의 bikestation.* 설정. */
@ConfigurationProperties("bikestation")
public record BikestationProperties(Cors cors, Coverage coverage, Districts districts) {

	public record Cors(List<String> allowedOrigins) {
	}

	public record Coverage(double defaultRadiusM) {
	}

	public record Districts(double simplifyToleranceM) {
	}

}
