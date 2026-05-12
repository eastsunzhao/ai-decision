from pathlib import Path
from typing import Dict, Optional

import yaml


class ScenarioManager:
    def __init__(self, scenario_folder: Optional[str] = None):
        self.scenario_folder = Path(scenario_folder) if scenario_folder else Path(__file__).resolve().parent.parent / "scenarios"
        self._scenarios = self._load_scenarios()

    def _load_scenarios(self) -> Dict[str, Dict]:
        scenarios: Dict[str, Dict] = {}
        for path in self.scenario_folder.glob("*.yaml"):
            with path.open("r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            scenario_id = config.get("scenario_id")
            if scenario_id:
                scenarios[scenario_id] = config
        return scenarios

    def get_scenario(self, scenario_id: str) -> Optional[Dict]:
        return self._scenarios.get(scenario_id)

    def has_scenario(self, scenario_id: str) -> bool:
        return scenario_id in self._scenarios

    def list_scenarios(self) -> list[Dict]:
        items = []
        for scenario_id, scenario in self._scenarios.items():
            directions = scenario.get("analysis_directions", [])
            visible_skills = [item.get("skill_id") for item in directions if item.get("skill_id")]
            items.append(
                {
                    "scenario_id": scenario_id,
                    "name": scenario.get("name", scenario_id),
                    "description": scenario.get("description", ""),
                    "analysis_directions": directions,
                    "skills": visible_skills or list((scenario.get("skills") or {}).keys()),
                }
            )
        return items
