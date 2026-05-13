from pathlib import Path
from typing import Dict, Optional

import yaml


class ScenarioManager:
    def __init__(self, scenario_folder: Optional[str] = None):
        app_root = Path(__file__).resolve().parent.parent
        self.scenario_folder = Path(scenario_folder) if scenario_folder else app_root / "scenarios"
        self.analyst_folder = app_root / "analysts"
        self._analysts = self._load_analysts()
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

    def _load_analysts(self) -> Dict[str, Dict]:
        analysts: Dict[str, Dict] = {}
        if not self.analyst_folder.exists():
            return analysts
        for path in self.analyst_folder.glob("*.yaml"):
            with path.open("r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            analyst_id = config.get("analyst_id")
            if analyst_id:
                analysts[analyst_id] = self._as_workflow_config(config)
        return analysts

    def _as_workflow_config(self, analyst: Dict) -> Dict:
        skill_id = analyst.get("skill_id") or analyst.get("default_route")
        config = dict(analyst)
        config.setdefault("scenario_id", analyst.get("analyst_id"))
        config.setdefault("default_route", skill_id)
        config.setdefault("analysis_directions", [])
        return config

    def get_scenario(self, scenario_id: str) -> Optional[Dict]:
        return self.get_analyst(scenario_id) or self._scenarios.get(scenario_id)

    def has_scenario(self, scenario_id: str) -> bool:
        return self.has_analyst(scenario_id) or scenario_id in self._scenarios

    def get_analyst(self, analyst_id: str) -> Optional[Dict]:
        return self._analysts.get(analyst_id)

    def has_analyst(self, analyst_id: str) -> bool:
        return analyst_id in self._analysts

    def list_analysts(self) -> list[Dict]:
        return [
            {
                "analyst_id": analyst_id,
                "name": analyst.get("name", analyst_id),
                "description": analyst.get("description", ""),
                "default_query": analyst.get("default_query", ""),
                "skill_id": analyst.get("skill_id") or analyst.get("default_route"),
                "methodology_config": analyst.get("methodology_config", ""),
                "data_sources": analyst.get("data_sources", []),
            }
            for analyst_id, analyst in self._analysts.items()
        ]

    def list_scenarios(self) -> list[Dict]:
        if self._analysts:
            return [
                {
                    "scenario_id": item["analyst_id"],
                    "analyst_id": item["analyst_id"],
                    "name": item["name"],
                    "description": item["description"],
                    "analysis_directions": [],
                    "skills": [item["skill_id"]] if item.get("skill_id") else [],
                }
                for item in self.list_analysts()
            ]
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
