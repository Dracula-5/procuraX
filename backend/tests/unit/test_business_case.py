import pytest
from pydantic import ValidationError

from app.domain.business_case import BusinessCaseScenario, calculate_scenario


def _value(value: str, kind: str = "assumption") -> dict[str, str]:
    return {"value": value, "kind": kind, "source": "documented scenario assumption"}


def test_business_case_keeps_assumptions_visible_and_calculates_savings() -> None:
    scenario = BusinessCaseScenario.model_validate(
        {
            "name": "Expected",
            "inputs": {
                "annual_requests": _value("1000", "measured"),
                "annual_invoices": _value("1000", "measured"),
                "current_request_minutes": _value("10"),
                "digitized_request_minutes": _value("5"),
                "current_invoice_minutes": _value("10"),
                "digitized_invoice_minutes": _value("4"),
                "current_invoice_exception_rate": _value("0.1"),
                "digitized_invoice_exception_rate": _value("0.05"),
                "extra_minutes_per_exception": _value("20"),
                "loaded_labor_cost_per_hour": _value("60"),
                "annual_platform_cost": _value("1000"),
                "implementation_cost": _value("5000"),
                "verified_duplicate_loss_avoided": _value("200"),
            },
        }
    )

    result = calculate_scenario(scenario)

    assert result["assumption_count"] == 11
    assert result["annual_net_benefit"] == "11200.00"
    assert result["first_year_net_benefit"] == "6200.00"
    assert result["capacity_hours_released"] == "200.00"


def test_business_case_rejects_fractional_counts_and_rates_over_one() -> None:
    inputs = {
        name: _value("0")
        for name in (
            "annual_requests",
            "annual_invoices",
            "current_request_minutes",
            "digitized_request_minutes",
            "current_invoice_minutes",
            "digitized_invoice_minutes",
            "current_invoice_exception_rate",
            "digitized_invoice_exception_rate",
            "extra_minutes_per_exception",
            "loaded_labor_cost_per_hour",
            "annual_platform_cost",
            "implementation_cost",
            "verified_duplicate_loss_avoided",
        )
    }
    inputs["annual_requests"] = _value("1.5")
    with pytest.raises(ValidationError, match="whole-number count"):
        BusinessCaseScenario.model_validate({"name": "bad-count", "inputs": inputs})

    inputs["annual_requests"] = _value("1")
    inputs["current_invoice_exception_rate"] = _value("1.1")
    with pytest.raises(ValidationError, match="between 0 and 1"):
        BusinessCaseScenario.model_validate({"name": "bad-rate", "inputs": inputs})
