"""Structure and reference check of the machine-readable acceptance evidence (spec 11).

``acceptance/spec-11.json`` follows specification 4.0. The old manifest
(``phase-7.2.json``) pointed at the removed UI and only lives in the git history.
"""

import json
import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
MANIFEST = REPOSITORY / "acceptance" / "spec-11.json"
SPEC = REPOSITORY / "docs" / "spec.md"
#: A-28 and A-30 are reserved; they would follow with the network capture and
#: with highlighting.
EXPECTED_CRITERIA = (*(f"A-{index:02d}" for index in range(1, 28)), "A-29")
EXTERNAL_CRITERIA = {"A-12", "A-14", "A-22"}


def _load_manifest() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_acceptance_manifest_contains_each_criterion_exactly_once() -> None:
    document = _load_manifest()
    assert document["schema_version"] == 2
    assert document["specification"] == "4.0"
    criteria = document["criteria"]
    assert isinstance(criteria, list)
    assert tuple(item["id"] for item in criteria) == EXPECTED_CRITERIA


def test_only_network_capture_low_end_device_and_real_corpus_remain_external() -> None:
    criteria = _load_manifest()["criteria"]
    external = {item["id"] for item in criteria if item["status"] == "external_evidence_required"}
    assert external == EXTERNAL_CRITERIA

    for item in criteria:
        if item["id"] in EXTERNAL_CRITERIA:
            check = item.get("external_check")
            assert isinstance(check, dict)
            assert all(
                check.get(field) for field in ("why", "procedure", "pass_condition", "artifact")
            )
        else:
            assert item["status"] == "automated"
            assert "external_check" not in item


def test_each_evidence_reference_points_to_existing_locator() -> None:
    criteria = _load_manifest()["criteria"]
    for item in criteria:
        evidence = item.get("evidence")
        assert isinstance(evidence, list) and evidence, item["id"]
        for reference in evidence:
            path = REPOSITORY / reference["file"]
            assert path.is_file(), f"{item['id']}: {path} is missing"
            locator = reference["test"]
            assert locator in path.read_text(encoding="utf-8"), (
                f"{item['id']}: locator {locator!r} is missing in {reference['file']}"
            )
            assert reference.get("asserts"), f"{item['id']}: the evidence has no statement"


def test_spec_lists_each_criterion_in_section_eleven() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert re.search(r"^\*\*Version:\*\* 4\.0", text, re.M)
    start = text.index("\n## 11.")
    end = text.index("\n## 12.", start)
    section = text[start:end]
    for criterion in EXPECTED_CRITERIA:
        assert f"**{criterion}**" in section, f"{criterion} is missing from spec section 11"
