package com.example.bikestation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * bike.scenarios. 시나리오 = 조건(cond_id) + 대여소 수 p.
 * 테이블은 Python 쪽(sql/schema.sql)이 만들고 채운다. 읽기 전용이라 setter가 없다.
 */
@Entity
@Immutable
@Table(schema = "bike", name = "scenarios")
public class Scenario {

	@Id
	@Column(name = "scenario_id")
	private String scenarioId;

	@Column(name = "cond_id")
	private String condId;

	@Column(name = "mode")
	private String mode;

	@Column(name = "build_type")
	private String buildType;

	@Column(name = "walk_m")
	private Long walkM;

	@Column(name = "trip_unit")
	private String tripUnit;

	@Column(name = "p")
	private Long p;

	@Column(name = "objective")
	private Double objective;

	@Column(name = "upper_bound")
	private Double upperBound;

	@Column(name = "n_selected")
	private Long nSelected;

	@Column(name = "n_routes_benefit")
	private Long nRoutesBenefit;

	@Column(name = "mip_gap")
	private Double mipGap;

	@Column(name = "status")
	private String status;

	protected Scenario() {
	}

	public String getScenarioId() {
		return scenarioId;
	}

	public String getCondId() {
		return condId;
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

	public Long getP() {
		return p;
	}

	public Double getObjective() {
		return objective;
	}

	public Double getUpperBound() {
		return upperBound;
	}

	public Long getNSelected() {
		return nSelected;
	}

	public Long getNRoutesBenefit() {
		return nRoutesBenefit;
	}

	public Double getMipGap() {
		return mipGap;
	}

	public String getStatus() {
		return status;
	}

}
