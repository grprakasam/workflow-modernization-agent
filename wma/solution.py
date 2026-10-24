"""Prepare a reviewed definition inside a COPY of an existing unpacked solution.

This supports the Workflows/*.json client-data format, not arbitrary solution formats.
PAC remains responsible for packing and Dataverse remains the final schema validator.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

from .validate import validate_definition


class SolutionPreparationError(ValueError):
    pass


def _hosts(value):
    if isinstance(value, dict):
        if value.get("type") == "OpenApiConnection":
            yield value.get("inputs", {}).get("host", {})
        for child in value.values():
            yield from _hosts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _hosts(child)


def _unresolved(value):
    if isinstance(value, dict):
        if value.get("metadata", {}).get("wmaMethod") == "unsupported":
            return True
        if any(str(key).startswith("TODO_") for key in value):
            return True
        return any(_unresolved(child) for child in value.values())
    if isinstance(value, list):
        return any(_unresolved(child) for child in value)
    return False


def prepare_solution(template, workflow_file, definition, output, bindings=None):
    """Preserve solution identity/references; replace only the selected flow definition.

    bindings maps generated connector names to EXISTING template connection-reference keys.
    Refuses unresolved mappings, escaping paths, unknown references and existing outputs.
    This is preparation for sandbox review, never proof of live equivalence.
    """
    template, output = Path(template).resolve(), Path(output).resolve()
    if not template.is_dir() or not (template / "Other" / "Solution.xml").is_file():
        raise SolutionPreparationError("Template must be a PAC-unpacked XML solution (Other/Solution.xml)")
    if output == template or template in output.parents or output in template.parents or output.exists():
        raise SolutionPreparationError("Output must be a new folder outside the template")
    relative = Path(workflow_file)
    target = (template / relative).resolve()
    if target.parent != template / "Workflows" or target.suffix.lower() != ".json":
        raise SolutionPreparationError("Select one direct Workflows/*.json file inside the template")
    if not target.is_file():
        raise SolutionPreparationError("Selected workflow file does not exist")
    issues = validate_definition(definition)
    if issues or _unresolved(definition):
        raise SolutionPreparationError(
            "Definition contains static issues or unresolved TODO actions: " + "; ".join(issues)
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    properties = payload.get("properties", {})
    if not isinstance(properties.get("definition"), dict):
        raise SolutionPreparationError("Template must contain properties.definition")
    references = properties.get("connectionReferences", {})
    prepared = copy.deepcopy(definition)
    bindings = bindings or {}
    used = set()
    for host in _hosts(prepared):
        source = host.get("connectionName")
        key = bindings.get(source, source)
        ref = references.get(key, {})
        logical = ref.get("connection", {}).get("connectionReferenceLogicalName")
        api = ref.get("api", {}).get("name")
        expected_api = host.get("apiId", "").rsplit("/", 1)[-1]
        if not logical or api != expected_api:
            raise SolutionPreparationError(f"Missing or incompatible connection reference for {source}: {key}")
        host["connectionName"] = key
        used.add(key)
    # Triggers may require site/list/environment-specific parameters: the maker must review these.
    payload["properties"]["definition"] = prepared
    shutil.copytree(template, output)
    (output / target.relative_to(template)).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    manifest = {
        "status": "prepared_for_sandbox_review",
        "workflow_file": target.relative_to(template).as_posix(),
        "connection_references": sorted(used),
        "live_import_tested": False,
        "live_behavioral_equivalence_tested": False,
    }
    (output / "wma-preparation.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def pack_solution(folder, zipfile, pac="pac"):
    """Ask the installed Microsoft PAC CLI to pack; no authentication or import is performed."""
    folder, zipfile = Path(folder).resolve(), Path(zipfile).resolve()
    if not (folder / "wma-preparation.json").is_file():
        raise SolutionPreparationError("Prepare and review the solution before packing")
    if zipfile.exists() or folder == zipfile or folder in zipfile.parents:
        raise SolutionPreparationError("Use a new ZIP path outside the prepared folder")
    subprocess.run(
        [pac, "solution", "pack", "--folder", str(folder), "--zipfile", str(zipfile), "--packagetype", "Unmanaged"],
        check=True,
    )
    if not zipfile.is_file():
        raise SolutionPreparationError("PAC returned without creating the requested ZIP")
    return zipfile
