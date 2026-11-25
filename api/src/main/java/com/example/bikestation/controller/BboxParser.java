package com.example.bikestation.controller;

import com.example.bikestation.dto.Bbox;

/**
 * 화면 범위 파라미터 bbox = "minLon,minLat,maxLon,maxLat" (EPSG:4326 경위도)를 검증해 Bbox로 바꾼다.
 * 숫자 4개인지, 유한한 값인지, 경위도 범위 안인지, 최소 < 최대인지 확인한다. 틀리면 400.
 */
public final class BboxParser {

	private BboxParser() {
	}

	public static Bbox parse(String raw) {
		String[] parts = raw.split(",", -1);
		if (parts.length != 4) {
			throw new InvalidParameterException("bbox는 minLon,minLat,maxLon,maxLat 숫자 4개여야 합니다: " + raw);
		}
		double[] v = new double[4];
		for (int i = 0; i < 4; i++) {
			try {
				v[i] = Double.parseDouble(parts[i].trim());
			}
			catch (NumberFormatException e) {
				throw new InvalidParameterException("bbox에 숫자가 아닌 값이 있습니다: " + parts[i]);
			}
			if (!Double.isFinite(v[i])) {
				throw new InvalidParameterException("bbox에 유한하지 않은 값이 있습니다: " + parts[i]);
			}
		}
		Bbox b = new Bbox(v[0], v[1], v[2], v[3]);
		if (b.minLon() < -180 || b.maxLon() > 180 || b.minLat() < -90 || b.maxLat() > 90) {
			throw new InvalidParameterException("bbox가 경위도 범위(경도 -180~180, 위도 -90~90)를 벗어났습니다: " + raw);
		}
		if (b.minLon() >= b.maxLon() || b.minLat() >= b.maxLat()) {
			throw new InvalidParameterException("bbox의 최솟값이 최댓값보다 작아야 합니다: " + raw);
		}
		return b;
	}

}
