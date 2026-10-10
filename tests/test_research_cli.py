from __future__ import annotations

import json

from typer.testing import CliRunner

from tal.cli import app


def test_research_bootstrap_writes_non_secret_prespecified_plan(tmp_path) -> None:
    output = tmp_path / "study-plan.json"
    result = CliRunner().invoke(app, ["research", "bootstrap", "--output", str(output)])
    assert result.exit_code == 0
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["random_seed"] == 42
    assert plan["main_tasks"] == {
        "finance_agent": 50,
        "plancraft": 50,
        "plancraft_selection": "recipe-depth quartiles, seed 42",
    }
