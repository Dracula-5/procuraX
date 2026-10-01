"""Assumption-explicit procurement transformation business case calculations."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourcedNumber(BaseModel):
    value: Decimal = Field(ge=0)
    kind: Literal["measured", "assumption"]
    source: str = Field(min_length=3, max_length=300)


class ScenarioInputs(BaseModel):
    annual_requests: SourcedNumber
    annual_invoices: SourcedNumber
    current_request_minutes: SourcedNumber
    digitized_request_minutes: SourcedNumber
    current_invoice_minutes: SourcedNumber
    digitized_invoice_minutes: SourcedNumber
    current_invoice_exception_rate: SourcedNumber
    digitized_invoice_exception_rate: SourcedNumber
    extra_minutes_per_exception: SourcedNumber
    loaded_labor_cost_per_hour: SourcedNumber
    annual_platform_cost: SourcedNumber
    implementation_cost: SourcedNumber
    verified_duplicate_loss_avoided: SourcedNumber

    @model_validator(mode="after")
    def _validate_counts_and_rates(self) -> ScenarioInputs:
        for name in ("annual_requests", "annual_invoices"):
            value = getattr(self, name).value
            if value != value.to_integral_value():
                raise ValueError(f"{name} must be a whole-number count")
        for name in ("current_invoice_exception_rate", "digitized_invoice_exception_rate"):
            if getattr(self, name).value > 1:
                raise ValueError(f"{name} must be between 0 and 1")
        return self


class BusinessCaseScenario(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    inputs: ScenarioInputs


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_scenario(scenario: BusinessCaseScenario) -> dict[str, str | None | int]:
    values = {name: getattr(scenario.inputs, name).value for name in ScenarioInputs.model_fields}
    requests = values["annual_requests"]
    invoices = values["annual_invoices"]
    current_request_hours = requests * values["current_request_minutes"] / 60
    digitized_request_hours = requests * values["digitized_request_minutes"] / 60
    current_invoice_hours = (
        invoices
        * (
            values["current_invoice_minutes"]
            + values["current_invoice_exception_rate"] * values["extra_minutes_per_exception"]
        )
        / 60
    )
    digitized_invoice_hours = (
        invoices
        * (
            values["digitized_invoice_minutes"]
            + values["digitized_invoice_exception_rate"] * values["extra_minutes_per_exception"]
        )
        / 60
    )
    labor_rate = values["loaded_labor_cost_per_hour"]
    current_labor_cost = (current_request_hours + current_invoice_hours) * labor_rate
    digitized_labor_cost = (digitized_request_hours + digitized_invoice_hours) * labor_rate
    annual_net = (
        current_labor_cost
        - digitized_labor_cost
        + values["verified_duplicate_loss_avoided"]
        - values["annual_platform_cost"]
    )
    first_year_net = annual_net - values["implementation_cost"]
    payback_months = values["implementation_cost"] / (annual_net / 12) if annual_net > 0 else None
    released_hours = (
        current_request_hours + current_invoice_hours - digitized_request_hours - digitized_invoice_hours
    )
    assumption_count = sum(
        getattr(scenario.inputs, name).kind == "assumption" for name in ScenarioInputs.model_fields
    )
    return {
        "scenario": scenario.name,
        "input_count": len(ScenarioInputs.model_fields),
        "assumption_count": assumption_count,
        "capacity_hours_released": str(_money(released_hours)),
        "current_processing_labor_cost": str(_money(current_labor_cost)),
        "digitized_processing_labor_cost": str(_money(digitized_labor_cost)),
        "annual_net_benefit": str(_money(annual_net)),
        "first_year_net_benefit": str(_money(first_year_net)),
        "payback_months": str(_money(payback_months)) if payback_months is not None else None,
    }
