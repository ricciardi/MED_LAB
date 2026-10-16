"""Post-editing records actual actors without manufacturing clinical approval."""
from copy import deepcopy
import hashlib
import json

import pytest

from medlab.case_schema import CaseValidationError, validate_case
from medlab.draft_bundle import bundle_shards, write_bundle
from medlab.local_case_integration import assemble_local_case
from test_local_case_integration import MODEL, REFERENCE, package_inputs


def post_edited_case():
    package, translations = package_inputs()
    unit = package["translationUnits"][0]
    editing = {"method": "assistant_post_edit", "actor": "Codex", "units": [{
        "sourceSha256": hashlib.sha256(unit["text"].encode()).hexdigest(),
        "translationSha256": hashlib.sha256(translations[unit["id"]].encode()).hexdigest()}]}
    metadata = {**MODEL, "postEditing": editing}
    result = assemble_local_case(package, translations, metadata, accepted_for_use=True,
                                 authorization_reference=REFERENCE)
    return result, metadata


def test_post_editing_survives_json_and_release_round_trip_without_claiming_review(tmp_path):
    case, metadata = post_edited_case()
    editing = deepcopy(metadata["postEditing"])
    restored = validate_case(json.loads(json.dumps(case)))
    assert restored["provenance"]["localPreparation"]["postEditing"] == editing
    assert restored["provenance"]["localPreparation"]["model"] == MODEL["model"]
    assert restored["provenance"]["localPreparation"]["modelRevision"] == MODEL["revision"]
    assert restored["provenance"]["localPreparation"]["clinicalReviewDeferred"] is True
    assert restored["reviewStatus"] == "review_required"
    assert restored["provenance"]["audit"]["method"] == "not_performed"
    assert any("pós-edição linguística assistida por Codex" in text for text in restored["provenance"]["changes"])
    metadata["postEditing"]["units"].clear()
    assert case["provenance"]["localPreparation"]["postEditing"] == editing
    bundle = tmp_path / "bundle"
    write_bundle([restored], bundle)
    exported = json.loads(bundle_shards(bundle)[0].read_text())[0]
    assert exported == restored


@pytest.mark.parametrize("mutation", [
    lambda value: value["units"][0].update(sourceSha256="invalid"),
    lambda value: value["units"][0].update(translationSha256="z" * 64),
    lambda value: value["units"].append(deepcopy(value["units"][0])),
    lambda value: value.update(units=[]),
    lambda value: value.update(units=[value["units"][0]] * 10001),
    lambda value: value.update(actor="Marian"),
    lambda value: value.update(method="clinical_review"),
    lambda value: value.update(clinicalApproval=True),
])
def test_post_editing_rejects_invalid_hashes_duplicates_or_approval_claims(mutation):
    case, _ = post_edited_case()
    mutation(case["provenance"]["localPreparation"]["postEditing"])
    with pytest.raises(CaseValidationError, match="postEditing"):
        validate_case(case)
