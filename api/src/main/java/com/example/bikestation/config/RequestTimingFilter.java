package com.example.bikestation.config;

import java.io.IOException;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * /api 요청마다 응답 시간을 로그로 남긴다 (로거 api.timing).
 * 예) GET /api/stations?scenario=... 200 12.3ms
 */
@Component
public class RequestTimingFilter extends OncePerRequestFilter {

	private static final Logger log = LoggerFactory.getLogger("api.timing");

	@Override
	protected boolean shouldNotFilter(HttpServletRequest request) {
		return !request.getRequestURI().startsWith("/api/");
	}

	@Override
	protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
			throws ServletException, IOException {
		long start = System.nanoTime();
		try {
			chain.doFilter(request, response);
		}
		finally {
			double ms = (System.nanoTime() - start) / 1_000_000.0;
			String query = request.getQueryString();
			log.info("{} {}{} {} {}ms", request.getMethod(), request.getRequestURI(), query == null ? "" : "?" + query,
					response.getStatus(), String.format("%.1f", ms));
		}
	}

}
