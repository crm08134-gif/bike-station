package com.example.bikestation.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** 프론트엔드 주소에만 조회(GET)를 허용한다. */
@Configuration
public class WebConfig implements WebMvcConfigurer {

	private final BikestationProperties properties;

	public WebConfig(BikestationProperties properties) {
		this.properties = properties;
	}

	@Override
	public void addCorsMappings(CorsRegistry registry) {
		registry.addMapping("/api/**")
			.allowedOrigins(properties.cors().allowedOrigins().toArray(String[]::new))
			.allowedMethods("GET");
	}

}
