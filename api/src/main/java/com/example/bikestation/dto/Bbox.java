package com.example.bikestation.dto;

/** 화면 범위 (EPSG:4326 경위도). 검증은 controller.BboxParser가 한다. */
public record Bbox(double minLon, double minLat, double maxLon, double maxLat) {
}
