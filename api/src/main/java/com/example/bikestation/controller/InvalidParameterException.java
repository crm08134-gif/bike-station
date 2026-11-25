package com.example.bikestation.controller;

/** 요청 파라미터가 형식·범위에 맞지 않을 때 (400). */
public class InvalidParameterException extends RuntimeException {

	public InvalidParameterException(String message) {
		super(message);
	}

}
