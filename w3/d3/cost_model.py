#!/usr/bin/env python3
"""Break-even cost model for the W3 AIOps mini-platform."""


def is_worth_it(
    num_services: int,
    incidents_per_month: int,
    avg_incident_duration_hours: float,
    downtime_cost_per_hour: float,
    expected_mttr_reduction_pct: float = 0.4,
    aiops_monthly_cost: float = 15_000,
) -> dict:
    """
    Returns:
      {
        "monthly_value": float,
        "monthly_cost": float,
        "roi": float,
        "payback_months": float,
        "verdict": "worth_it" | "marginal" | "not_worth_it"
      }
    """
    monthly_downtime_hours = incidents_per_month * avg_incident_duration_hours
    monthly_value = (
        monthly_downtime_hours
        * expected_mttr_reduction_pct
        * downtime_cost_per_hour
    )
    roi = monthly_value / aiops_monthly_cost if aiops_monthly_cost > 0 else float("inf")
    payback_months = (
        aiops_monthly_cost / monthly_value
        if monthly_value > 0
        else float("inf")
    )
    verdict = (
        "worth_it"
        if roi > 1.5
        else "marginal"
        if roi > 1.0
        else "not_worth_it"
    )
    return {
        "num_services": num_services,
        "monthly_value": round(monthly_value, 2),
        "monthly_cost": round(aiops_monthly_cost, 2),
        "roi": round(roi, 2),
        "payback_months": round(payback_months, 2) if payback_months != float("inf") else float("inf"),
        "verdict": verdict,
    }


if __name__ == "__main__":
    scenarios = {
        "small_internal_saas": is_worth_it(
            num_services=20,
            incidents_per_month=2,
            avg_incident_duration_hours=1,
            downtime_cost_per_hour=10_000,
            aiops_monthly_cost=15_000,
        ),
        "mid_market_ecommerce": is_worth_it(
            num_services=100,
            incidents_per_month=5,
            avg_incident_duration_hours=2,
            downtime_cost_per_hour=20_000,
            aiops_monthly_cost=25_000,
        ),
        # Current W3 mini-platform assumption: 10-service demo stack plus
        # frontend/api/db SLOs. A mid-tier ecommerce checkout outage is modeled
        # at $50k/hour, matching the order-of-magnitude table in the material.
        "current_aiops_stack": is_worth_it(
            num_services=12,
            incidents_per_month=3,
            avg_incident_duration_hours=1.25,
            downtime_cost_per_hour=50_000,
            expected_mttr_reduction_pct=0.4,
            aiops_monthly_cost=18_000,
        ),
    }
    for name, result in scenarios.items():
        print(name)
        for key, value in result.items():
            print(f"  {key}: {value}")
