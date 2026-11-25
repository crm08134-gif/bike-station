package com.example.bikestation.service;

/** 요청한 시나리오·대여소·구가 없을 때. 컨트롤러 예외 처리에서 404로 바꾼다. */
public class NotFoundException extends RuntimeException {

	public NotFoundException(String message) {
		super(message);
	}

}
