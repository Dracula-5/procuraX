"""Calculate measured or assumption-labeled business-case scenarios from user-supplied JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.domain.business_case import BusinessCaseScenario, calculate_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", type=Path, help="JSON object with a scenarios array and sourced input values"
    )
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    scenarios = [BusinessCaseScenario.model_validate(row) for row in raw["scenarios"]]
    if len({scenario.name for scenario in scenarios}) != len(scenarios):
        parser.error("scenario names must be unique")
    print(json.dumps([calculate_scenario(scenario) for scenario in scenarios], indent=2))


if __name__ == "__main__":
    main()
