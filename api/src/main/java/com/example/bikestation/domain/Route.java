package com.example.bikestation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * bike.routes (통행). 같은 길을 간 통행을 묶은 단위이고 weight = 묶인 통행 수다 (현재 데이터는 전부 1).
 * geom은 매핑하지 않는다 (공간 조회는 RouteRepository 네이티브 쿼리).
 */
@Entity
@Immutable
@Table(schema = "bike", name = "routes")
public class Route {

	@Id
	@Column(name = "route_id")
	private Long routeId;

	@Column(name = "weight")
	private Long weight;

	@Column(name = "route_len_m")
	private Double routeLenM;

	@Column(name = "obs_ratio")
	private Double obsRatio;

	@Column(name = "repeat_ratio")
	private Double repeatRatio;

	@Column(name = "straight_ratio")
	private Double straightRatio;

	@Column(name = "n_breaks")
	private Long nBreaks;

	protected Route() {
	}

	public Long getRouteId() {
		return routeId;
	}

	public Long getWeight() {
		return weight;
	}

	public Double getRouteLenM() {
		return routeLenM;
	}

	public Double getObsRatio() {
		return obsRatio;
	}

	public Double getRepeatRatio() {
		return repeatRatio;
	}

	public Double getStraightRatio() {
		return straightRatio;
	}

	public Long getNBreaks() {
		return nBreaks;
	}

}
