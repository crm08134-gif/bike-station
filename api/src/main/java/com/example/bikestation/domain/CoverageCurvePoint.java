package com.example.bikestation.domain;

import java.io.Serializable;
import java.util.Objects;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/** bike.coverage_curve. 조건(cond_id)별 p -> 목적값. 기본키 (cond_id, p). */
@Entity
@Immutable
@IdClass(CoverageCurvePoint.Key.class)
@Table(schema = "bike", name = "coverage_curve")
public class CoverageCurvePoint {

	@Id
	@Column(name = "cond_id")
	private String condId;

	@Id
	@Column(name = "p")
	private Long p;

	@Column(name = "mode")
	private String mode;

	@Column(name = "build_type")
	private String buildType;

	@Column(name = "walk_m")
	private Long walkM;

	@Column(name = "trip_unit")
	private String tripUnit;

	@Column(name = "objective")
	private Double objective;

	@Column(name = "upper_bound")
	private Double upperBound;

	protected CoverageCurvePoint() {
	}

	public String getCondId() {
		return condId;
	}

	public Long getP() {
		return p;
	}

	public String getMode() {
		return mode;
	}

	public String getBuildType() {
		return buildType;
	}

	public Long getWalkM() {
		return walkM;
	}

	public String getTripUnit() {
		return tripUnit;
	}

	public Double getObjective() {
		return objective;
	}

	public Double getUpperBound() {
		return upperBound;
	}

	/** 복합 기본키 (cond_id, p). */
	public static class Key implements Serializable {

		private String condId;

		private Long p;

		protected Key() {
		}

		public Key(String condId, Long p) {
			this.condId = condId;
			this.p = p;
		}

		@Override
		public boolean equals(Object o) {
			return o instanceof Key k && Objects.equals(condId, k.condId) && Objects.equals(p, k.p);
		}

		@Override
		public int hashCode() {
			return Objects.hash(condId, p);
		}

	}

}
