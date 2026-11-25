package com.example.bikestation;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class BikestationApplication {

	public static void main(String[] args) {
		SpringApplication.run(BikestationApplication.class, args);
	}

}
