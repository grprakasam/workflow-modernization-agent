import copy
import json
from pathlib import Path

import pytest

from wma.solution import SolutionPreparationError, pack_solution, prepare_solution

SCHEMA = "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#"


@pytest.fixture
def template(tmp_path):
    root = tmp_path / "template"
    (root / "Other").mkdir(parents=True)
    (root / "Other" / "Solution.xml").write_text("<ImportExportXml/>", encoding="utf-8")
    (root / "Workflows").mkdir()
    payload = {
        "schemaVersion": "1.0.0.0",
        "properties": {
            "definition": {},
            "connectionReferences": {
                "mail_ref": {
                    "connection": {"connectionReferenceLogicalName": "new_mail"},
                    "api": {"name": "shared_office365"},
                }
            },
        },
    }
    (root / "Workflows" / "flow.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


@pytest.fixture
def definition():
    return {
        "$schema": SCHEMA, "contentVersion": "1.0.0.0",
        "triggers": {"manual": {"type": "Request", "kind": "Button", "inputs": {"schema": {}}}},
        "actions": {
            "mail": {
                "type": "OpenApiConnection", "runAfter": {},
                "inputs": {
                    "host": {
                        "connectionName": "shared_office365", "operationId": "SendEmailV2",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_office365",
                    },
                    "parameters": {"emailMessage/To": "reviewer@example.invalid"},
                },
            },
        },
    }


def test_prepares_copy_and_retains_real_reference(template, definition, tmp_path):
    original = (template / "Workflows" / "flow.json").read_bytes()
    out = tmp_path / "prepared"
    result = prepare_solution(template, "Workflows/flow.json", definition, out, {"shared_office365": "mail_ref"})
    payload = json.loads((out / "Workflows" / "flow.json").read_text())
    assert payload["properties"]["definition"]["actions"]["mail"]["inputs"]["host"]["connectionName"] == "mail_ref"
    assert payload["properties"]["connectionReferences"]["mail_ref"]["connection"]["connectionReferenceLogicalName"]
    assert result["live_import_tested"] is False
    assert (template / "Workflows" / "flow.json").read_bytes() == original


def test_refuses_incomplete_mapping_and_todos(template, definition, tmp_path):
    with pytest.raises(SolutionPreparationError, match="connection reference"):
        prepare_solution(template, "Workflows/flow.json", definition, tmp_path / "out")
    bad = copy.deepcopy(definition)
    bad["actions"]["TODO_manual"] = {"type": "Compose", "inputs": "Rebuild manually", "runAfter": {}}
    with pytest.raises(SolutionPreparationError, match="TODO"):
        prepare_solution(template, "Workflows/flow.json", bad, tmp_path / "out")


def test_refuses_escaping_path_and_template_mutation(template, definition, tmp_path):
    with pytest.raises(SolutionPreparationError, match="Workflows"):
        prepare_solution(template, "../escape.json", definition, tmp_path / "out")
    with pytest.raises(SolutionPreparationError, match="outside"):
        prepare_solution(template, "Workflows/flow.json", definition, template / "out")


def test_pack_uses_argument_list_and_checks_result(template, definition, tmp_path, monkeypatch):
    out, archive = tmp_path / "prepared", tmp_path / "result.zip"
    prepare_solution(template, "Workflows/flow.json", definition, out, {"shared_office365": "mail_ref"})
    calls = []
    def fake_run(args, check):
        assert check
        calls.append(args)
        Path(args[args.index("--zipfile") + 1]).write_bytes(b"mock ZIP")
    monkeypatch.setattr("wma.solution.subprocess.run", fake_run)
    assert pack_solution(out, archive) == archive
    assert calls[0][:3] == ["pac", "solution", "pack"]
