package com.example.bikestation.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.bikestation.dto.Bbox;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class BboxParserTest {

	@Test
	void 정상_bbox() {
		assertThat(BboxParser.parse("126.8, 37.4,127.2,37.7")).isEqualTo(new Bbox(126.8, 37.4, 127.2, 37.7));
		assertThat(BboxParser.parse("-180,-90,180,90")).isEqualTo(new Bbox(-180, -90, 180, 90));
	}

	@ParameterizedTest
	@ValueSource(strings = { "", "126.8,37.4,127.2", "126.8,37.4,127.2,37.7,1", "126.8,37.4,127.2,", "a,37.4,127.2,37.7",
			"NaN,37.4,127.2,37.7", "126.8,37.4,Infinity,37.7", "-181,37.4,127.2,37.7", "126.8,-91,127.2,37.7",
			"126.8,37.4,181,37.7", "127.2,37.4,126.8,37.7", "126.8,37.7,127.2,37.4", "126.8,37.4,126.8,37.7" })
	void 잘못된_bbox는_거부(String raw) {
		assertThatThrownBy(() -> BboxParser.parse(raw)).isInstanceOf(InvalidParameterException.class);
	}

}
