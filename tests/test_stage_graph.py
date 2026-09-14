from __future__ import annotations

import json

from pixelasset.cli import main
from pixelasset.paths import ProjectPaths
from pixelasset.stages import (
    PATH_C_NOT_APPLICABLE,
    STAGE_DEFINITIONS,
    STATUS_NOT_APPLICABLE,
    STATUS_PASSED,
    graph_ok,
    run_stage_graph,
)


def test_stage_graph_has_seventeen_named_stages() -> None:
    assert len(STAGE_DEFINITIONS) == 17
    ids = [stage_id for stage_id, _ in STAGE_DEFINITIONS]
    assert ids[0] == "asset_specification"
    assert ids[-1] == "approved_production_asset"
    assert "reference_concept_generation" in ids
    assert "animation_construction" in ids
    assert "frame_consistency_validation" in ids


def test_hay_bale_marks_path_c_not_applicable(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    result = run_stage_graph(ProjectPaths(project_copy), "hay_bale")
    graph = result["graph"]
    assert len(graph["stages"]) == 17
    by_id = {s["id"]: s for s in graph["stages"]}
    for stage_id, reason in PATH_C_NOT_APPLICABLE.items():
        assert by_id[stage_id]["status"] == STATUS_NOT_APPLICABLE
        assert by_id[stage_id]["reason"] == reason
    pending = [s["id"] for s in graph["stages"] if s["status"] == "PENDING"]
    assert pending == []
    assert graph_ok(graph)
    for stage in graph["stages"]:
        assert stage["status"] in {STATUS_PASSED, STATUS_NOT_APPLICABLE}


def test_horse_bay_runs_idle_stages(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    result = run_stage_graph(ProjectPaths(project_copy), "horse_bay")
    graph = result["graph"]
    by_id = {s["id"]: s for s in graph["stages"]}
    assert by_id["reference_concept_generation"]["status"] == STATUS_NOT_APPLICABLE
    assert by_id["animation_construction"]["status"] == STATUS_PASSED
    assert by_id["frame_consistency_validation"]["status"] == STATUS_PASSED
    pending = [s["id"] for s in graph["stages"] if s["status"] == "PENDING"]
    assert pending == []
    assert graph_ok(graph)


def test_cli_concept_is_not_applicable_for_path_c(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    rc = main(["--root", str(project_copy), "concept", "hay_bale"])
    assert rc == 0
    graph = project_copy / "assets" / "working" / "hay_bale" / "stage_graph.json"
    data = json.loads(graph.read_text())
    concept = next(s for s in data["stages"] if s["id"] == "reference_concept_generation")
    assert concept["status"] == STATUS_NOT_APPLICABLE
