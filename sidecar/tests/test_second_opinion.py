"""second opinion (local classifier) after suspicion review.

Policy and input as pure functions, the stage with a simulated
model via the API (findings, open questions, text mode, settings),
the error case – and, when in development, the real ONNX model.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from presidio_sidecar import resources
from presidio_sidecar.detection.second_opinion import (
    LABELS,
    PROTECT_MINIMUM,
    SUGGEST_MINIMUM,
    V1_MODEL_LABELS,
    SecondOpinionModel,
    SecondOpinionUnavailableError,
    assess,
    classifier_input,
    softmax,
)
from presidio_sidecar.settings_store import AppSettings, SettingsStore, migrate
from test_api_v2 import (  # noqa: F401
    AUTH,
    DOCUMENT,
    REVERSIBLE,
    Harness,
    _entry_with_files,
    _wait_ready,
    api,
)


def _distribution(**values: float) -> list[float]:
    return [values.get(label, 0.0) for label in LABELS]


# -- Policy -------------------------------------------------------------------


def test_policy_protect_decides_only_toward_protection() -> None:
    confident = assess(
        _distribution(person=0.9, term=0.1), policy="protect", keep_threshold=0.95, model="m"
    )
    assert (confident.label, confident.suggestion, confident.decided) == ("person", "accept", True)
    # Company, location and person count together for protection.
    split_result = assess(
        _distribution(person=0.3, organization_or_product=0.3, location=0.25, term=0.15),
        policy="protect",
        keep_threshold=0.95,
        model="m",
    )
    assert split_result.protect == pytest.approx(0.85)
    assert split_result.decided and split_result.suggestion == "accept"
    # A safe term is only suggested – „Behalten" is never decided by the AI here.
    term_case = assess(
        _distribution(term=0.99, person=0.01), policy="protect", keep_threshold=0.95, model="m"
    )
    assert term_case.suggestion == "reject" and not term_case.decided


def test_pure_suspicion_kept_as_term_above_80_percent() -> None:
    # Only the uppercase heuristic has fired: Then the
    # AI also „Behalten", with the same confidence as for protecting.
    term_case = _distribution(term=0.85, person=0.15)
    heuristic_result = assess(
        term_case, policy="protect", keep_threshold=0.95, model="m", heuristic=True
    )
    assert (heuristic_result.suggestion, heuristic_result.decided) == ("reject", True)
    assert not assess(term_case, policy="protect", keep_threshold=0.95, model="m").decided
    borderline = _distribution(term=PROTECT_MINIMUM - 0.01, person=1 - PROTECT_MINIMUM + 0.01)
    assert not assess(
        borderline, policy="protect", keep_threshold=0.95, model="m", heuristic=True
    ).decided
    assert not assess(
        term_case, policy="suggest", keep_threshold=0.95, model="m", heuristic=True
    ).decided


def test_policy_protect_and_keep_above_threshold_and_suggest_only() -> None:
    term_case = _distribution(term=0.96, person=0.04)
    assert assess(term_case, policy="protect_and_keep", keep_threshold=0.95, model="m").decided
    assert not assess(term_case, policy="protect_and_keep", keep_threshold=0.97, model="m").decided
    person = _distribution(person=0.99, term=0.01)
    suggest_result = assess(person, policy="suggest", keep_threshold=0.95, model="m")
    assert suggest_result.suggestion == "accept" and not suggest_result.decided


def test_uncertain_without_suggestion_between_thresholds() -> None:
    uncertain = assess(
        _distribution(person=0.4, term=0.6), policy="protect", keep_threshold=0.95, model="m"
    )
    assert uncertain.suggestion is None and not uncertain.decided
    # Between suggestion and self-decision: suggestion yes, decision no.
    middle = (SUGGEST_MINIMUM + PROTECT_MINIMUM) / 2
    between = assess(
        _distribution(person=middle, term=1 - middle),
        policy="protect",
        keep_threshold=0.95,
        model="m",
    )
    assert between.suggestion == "accept" and not between.decided
    with pytest.raises(ValueError):
        assess([1.0], policy="protect", keep_threshold=0.95, model="m")


def test_softmax_with_temperature() -> None:
    assert sum(softmax([1.0, 2.0, 3.0, 4.0])) == pytest.approx(1.0)
    assert max(softmax([0.0, 4.0, 0.0, 0.0], 2.0)) < max(softmax([0.0, 4.0, 0.0, 0.0], 1.0))


# -- Input -------------------------------------------------------------------


def test_classifier_input_marks_value_and_keeps_lines() -> None:
    text = "BETREFF: ANGEBOT\nSehr geehrter Herr Wachtelhuber,\nanbei das Angebot."
    start = text.index("Herr Wachtelhuber")
    classifier_text = classifier_input(
        text, start, start + len("Herr Wachtelhuber"), entity_type="PERSON", suspicion=True
    )
    assert classifier_text.startswith("verdacht: Herr Wachtelhuber | ")
    assert "/ Sehr geehrter « Herr Wachtelhuber » , / anbei" in classifier_text
    uncertain = classifier_input(text, 0, 7, entity_type="ORGANIZATION", suspicion=False)
    assert uncertain.startswith("organization: BETREFF | ")


def test_classifier_input_truncates_context_at_word_boundaries() -> None:
    text = "wort " * 80 + "Gebrüder Kriechbaum" + " ende" * 80
    start = text.index("Gebrüder")
    classifier_text = classifier_input(
        text, start, start + 19, entity_type="PERSON", suspicion=True
    )
    before, after = classifier_text.split(" | ", 1)[1].split("«")
    assert before.split()[0] == "wort" and after.split()[-1] == "ende"
    assert len(classifier_text) < 2 * 120 + 80


# -- Stage with simulated model -----------------------------------------


class FakeModel:
    """Petra Novak is definitely a name; Musterbau is definitely a term (for the test)."""

    folder = Path("/nirgends")
    version = "fake-1"

    def __init__(self, *, error: bool = False) -> None:
        self.error = error
        self.classifier_texts: list[str] = []

    def probabilities(self, inputs: Sequence[str]) -> list[list[float]]:
        if self.error:
            raise SecondOpinionUnavailableError("kaputt")
        self.classifier_texts.extend(inputs)
        return [
            _distribution(person=0.97, term=0.03)
            if "Petra Novak" in item.split("|")[0]
            else _distribution(term=0.98, organization_or_product=0.02)
            for item in inputs
        ]


@pytest.fixture
def classifier_model(api: Harness, monkeypatch: pytest.MonkeyPatch) -> FakeModel:  # noqa: F811
    fake = FakeModel()
    monkeypatch.setattr(api.services, "second_opinion_model", lambda: fake)
    return fake


def _findings(api: Harness) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any]]:  # noqa: F811
    entry_id, file_ids = _entry_with_files(api, {"brief.txt": DOCUMENT})
    progress = _wait_ready(api, entry_id)
    body = api.client.get(f"/jobs/{file_ids[0]}/findings", headers=AUTH).json()
    return entry_id, {item["text"]: item for item in body["findings"]}, progress


def test_findings_carry_assessment_only_for_questions(
    api: Harness,  # noqa: F811
    classifier_model: FakeModel,
) -> None:
    _entry, by_text, progress = _findings(api)
    novak = by_text["Petra Novak"]["assessment"]
    assert novak == {
        "label": "person",
        "confidence": 0.97,
        "protect": 0.97,
        "suggestion": "accept",
        "decided": True,
        "model": "fake-1",
    }
    musterbau = by_text["Musterbau GmbH"]["assessment"]
    assert musterbau["label"] == "term" and musterbau["suggestion"] == "reject"
    assert not musterbau["decided"], "Behalten entscheidet die KI nur mit protect_and_keep"
    # Secure matches and numbers are not questions for the classifier.
    assert by_text["Hans Meier"]["assessment"] is None
    assert by_text["DE02120300000000202051"]["assessment"] is None
    assert len(classifier_model.classifier_texts) == 2
    # What the AI has resolved is no longer an open question. Without it,
    # this entry counts as a (companies remain in clear text per the preset).
    assert progress["files"][0]["questions"] == 0


def test_also_keep_above_95_percent_via_settings(
    api: Harness,  # noqa: F811
    classifier_model: FakeModel,
) -> None:
    response = api.client.put(
        "/settings", headers=AUTH, json={"second_opinion": {"policy": "protect_and_keep"}}
    )
    assert response.status_code == 200, response.text
    _entry, by_text, progress = _findings(api)
    assert by_text["Musterbau GmbH"]["assessment"]["decided"]
    assert progress["files"][0]["questions"] == 0


def test_disabled_or_without_model_no_assessment(
    api: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api.services, "second_opinion_model", lambda: None)
    _entry, by_text, _progress = _findings(api)
    assert by_text["Petra Novak"]["assessment"] is None

    fake = FakeModel()
    monkeypatch.setattr(api.services, "second_opinion_model", lambda: fake)
    api.client.put("/settings", headers=AUTH, json={"second_opinion": {"enabled": False}})
    _entry, by_text, _progress = _findings(api)
    assert by_text["Petra Novak"]["assessment"] is None
    assert fake.classifier_texts == []


def test_broken_model_does_not_stop_job(
    api: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api.services, "second_opinion_model", lambda: FakeModel(error=True))
    _entry, by_text, progress = _findings(api)
    assert by_text["Petra Novak"]["assessment"] is None
    assert progress["files"][0]["status"] == "review"
    events = api.services.diagnostics.as_dicts()
    assert any(event.get("details", {}).get("component") == "classifier" for event in events)


def test_text_mode_applies_ai_decisions_and_users_take_precedence(
    api: Harness,  # noqa: F811
    classifier_model: FakeModel,
) -> None:
    body = {"text": DOCUMENT, "masking": REVERSIBLE, "review": {"unresolved": "keep"}}
    masked = api.client.post("/text/mask", headers=AUTH, json=body).json()
    # “keep” leaves unresolved questions – the AI decided on Petra Novak.
    assert "Petra Novak" not in masked["result"]["text"]
    novak = next(item for item in masked["findings"] if item["text"] == "Petra Novak")
    keep_request = {
        **body,
        "review": {
            "unresolved": "keep",
            "decisions": [{"finding_id": novak["id"], "action": "reject"}],
        },
    }
    kept = api.client.post("/text/mask", headers=AUTH, json=keep_request).json()
    assert "Petra Novak" in kept["result"]["text"]


def test_apply_counts_ai_decisions_in_review_report(
    api: Harness,  # noqa: F811
    classifier_model: FakeModel,
) -> None:
    entry_id, file_ids = _entry_with_files(api, {"brief.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    findings = api.client.get(f"/jobs/{file_ids[0]}/findings", headers=AUTH).json()
    novak = next(item for item in findings["findings"] if item["text"] == "Petra Novak")
    response = api.client.post(
        f"/jobs/{file_ids[0]}/apply",
        headers=AUTH,
        json={
            "masking": REVERSIBLE,
            "review": {
                "decisions": [
                    {"finding_id": novak["id"], "action": "accept", "by": "ai-suggestion"}
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    entry = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    assert entry["review"][file_ids[0]]["decisions"] == [
        {"finding_id": novak["id"], "action": "accept", "by": "ai-suggestion"}
    ]


# -- Settings ---------------------------------------------------------------


def test_settings_version_three_starts_with_second_opinion(tmp_path: Path) -> None:
    alt = {"schema_version": 2, "second_opinion": {"enabled": False, "policy": "protect"}}
    migrated = migrate(alt)
    assert migrated["schema_version"] == 3 and "second_opinion" not in migrated
    standard = AppSettings.model_validate(migrated).second_opinion
    assert (standard.enabled, standard.policy, standard.keep_threshold) == (True, "protect", 0.95)
    store = SettingsStore(tmp_path / "settings.json")
    assert (
        store.update({"second_opinion": {"policy": "suggest"}}).second_opinion.policy == "suggest"
    )
    with pytest.raises(ValueError):
        store.update({"second_opinion": {"policy": "alles"}})
    with pytest.raises(ValueError):
        store.update({"second_opinion": {"keep_threshold": 0.3}})


# -- Model and Inventory ------------------------------------------------------------


def test_model_folder_without_configuration_not_available(tmp_path: Path) -> None:
    classifier_model = SecondOpinionModel(tmp_path)
    with pytest.raises(SecondOpinionUnavailableError):
        classifier_model.probabilities(["verdacht: x | « x »"])
    (tmp_path / "zweitmeinung.json").write_text('{"labels": ["a", "b"]}')
    with pytest.raises(SecondOpinionUnavailableError):
        SecondOpinionModel(tmp_path).probabilities(["verdacht: x | « x »"])
    assert SecondOpinionModel(tmp_path).probabilities([]) == []


def test_label_order_matches_the_trained_model() -> None:
    # The ONNX model outputs label indices in this order. Reordering LABELS
    # would silently swap labels without retraining.
    assert LABELS == ("person", "organization_or_product", "location", "term")
    assert len(V1_MODEL_LABELS) == len(LABELS)
    assert V1_MODEL_LABELS[0] == LABELS[0]


@pytest.mark.parametrize("labels", [LABELS, V1_MODEL_LABELS])
def test_model_config_accepts_current_and_v1_label_names(
    tmp_path: Path, labels: tuple[str, ...]
) -> None:
    (tmp_path / "zweitmeinung.json").write_text(json.dumps({"labels": list(labels)}))
    assert SecondOpinionModel(tmp_path).config["labels"] == list(labels)


def test_model_config_rejects_reordered_labels(tmp_path: Path) -> None:
    reordered = [LABELS[1], LABELS[0], *LABELS[2:]]
    (tmp_path / "zweitmeinung.json").write_text(json.dumps({"labels": reordered}))
    with pytest.raises(SecondOpinionUnavailableError):
        _ = SecondOpinionModel(tmp_path).config


def test_loaded_classifier_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(resources.MODELS_STRICT_ENV, "1")
    resources.use_models_directory(tmp_path)
    try:
        assert resources.classifier_location().path is None
        model_folder = tmp_path / "classifier"
        model_folder.mkdir()
        (model_folder / resources.INSTALL_MARKER).write_text('{"component": "classifier"}')
        assert resources.classifier_location() == resources.ComponentLocation(
            model_folder, "download"
        )
        assert resources.model_components()["classifier"]["state"] == "ready"
        # Optional: without a classifier no setup is pending.
        assert "classifier" not in {"ner", "de", "en"}
    finally:
        resources.use_models_directory(None)


DEV_MODEL_FOLDER = resources.repository_root() / "sidecar" / "models" / "second-opinion"


@pytest.mark.skipif(
    not (DEV_MODEL_FOLDER / "model.int8.onnx").is_file(), reason="Modell nicht trainiert"
)
def test_real_model_separates_names_from_terms() -> None:
    classifier_model = SecondOpinionModel(DEV_MODEL_FOLDER)
    cases = {
        "Sehr geehrter Herr Wachtelhuber, vielen Dank für Ihr Schreiben.": (
            "Herr Wachtelhuber",
            "person",
        ),
        "Die Lieferung erfolgt gemäß den Allgemeinen Geschäftsbedingungen.": (
            "Allgemeinen Geschäftsbedingungen",
            "term",
        ),
        "TERMS AND CONDITIONS\nThe Supplier shall deliver the goods.": (
            "TERMS AND CONDITIONS",
            "term",
        ),
        "We met the team of Ravenscroft Timber last week in Leeds.": (
            "Ravenscroft Timber",
            "organization_or_product",
        ),
    }
    classifier_texts = []
    for text, (finding_value, _label) in cases.items():
        start = text.index(finding_value)
        classifier_texts.append(
            classifier_input(
                text, start, start + len(finding_value), entity_type="PERSON", suspicion=True
            )
        )
    results = classifier_model.probabilities(classifier_texts)
    for (_text, (_value, label)), values in zip(cases.items(), results, strict=True):
        assert LABELS[max(range(4), key=values.__getitem__)] == label
    assert classifier_model.version.startswith("zweitmeinung-")


# -- Stage: reorder suspicion and keep it -------------------------------------


class LabelModel:
    """Return a fixed distribution for each value (value appears before the ``|``)."""

    folder = Path("/nirgends")
    version = "label-1"

    def __init__(self, distributions: dict[str, list[float]]) -> None:
        self.distributions = distributions

    def probabilities(self, inputs: Sequence[str]) -> list[list[float]]:
        return [self.distributions[item.split("|")[0].split(": ", 1)[1].strip()] for item in inputs]


def test_suspicion_becomes_company_location_or_term_after_confident_verdict() -> None:
    from presidio_sidecar.assessments import SecondOpinionOptions, with_assessments
    from presidio_sidecar.base_profile import base_profile
    from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
    from presidio_sidecar.detection.pipeline import EXACT_DUPLICATE_DETECTOR
    from presidio_sidecar.detection.suspicion import NAME_SUSPICION_DETECTOR
    from presidio_sidecar.processing import FindingKind, finding_id

    text = "Claude Code. Temporärer Speicher. Neue Welt. Hans Meier. Musterbau Nord."

    def suspicion_finding(finding_value: str, detector: str = NAME_SUSPICION_DETECTOR) -> Finding:
        start = text.index(finding_value)
        return Finding(
            entity_type="PERSON",
            start=start,
            end=start + len(finding_value),
            score=0.25,
            source=FindingSource.SUSPICION,
            text=finding_value,
            detector=detector,
        )

    model = LabelModel(
        {
            "Claude Code": _distribution(organization_or_product=0.9, term=0.1),
            "Temporärer Speicher": _distribution(term=0.9, person=0.1),
            "Neue Welt": _distribution(location=0.7, term=0.3),
            "Hans Meier": _distribution(person=0.95, term=0.05),
            "Musterbau Nord": _distribution(term=0.9, person=0.1),
        }
    )
    detection = DetectionResult(
        suspicious=(
            suspicion_finding("Claude Code"),
            suspicion_finding("Temporärer Speicher"),
            suspicion_finding("Neue Welt"),
            suspicion_finding("Hans Meier"),
            # Repeated recognized value: not a pure suspicion.
            suspicion_finding("Musterbau Nord", EXACT_DUPLICATE_DETECTOR),
        )
    )

    result = with_assessments(
        text,
        detection,
        base_profile(),
        model,  # type: ignore[arg-type]
        SecondOpinionOptions(policy="protect", keep_threshold=0.95),
    )

    by_text = {finding.text: finding for finding in result.suspicious}
    assessment = {
        finding.text: result.assessments[finding_id(finding, kind=FindingKind.SUSPICIOUS)]
        for finding in result.suspicious
    }
    # Definitely a company or product: the spot belongs to "organizations".
    assert by_text["Claude Code"].entity_type == "ORGANIZATION"
    assert assessment["Claude Code"].label == "organization_or_product"
    # Below the threshold it stays a name suspicion.
    assert by_text["Neue Welt"].entity_type == "PERSON"
    assert by_text["Hans Meier"].entity_type == "PERSON"
    assert assessment["Hans Meier"].decided and assessment["Hans Meier"].suggestion == "accept"
    # Pure suspicion, definitely a term: the model keeps it.
    assert assessment["Temporärer Speicher"].decided
    assert assessment["Temporärer Speicher"].suggestion == "reject"
    assert not assessment["Musterbau Nord"].decided


def test_reclassified_partial_value_reinherits_category_of_its_source() -> None:
    from presidio_sidecar.assessments import SecondOpinionOptions, with_assessments
    from presidio_sidecar.base_profile import base_profile
    from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
    from presidio_sidecar.detection.pipeline import _with_exact_duplicate_suspicions
    from presidio_sidecar.detection.suspicion import NAME_SUSPICION_DETECTOR
    from presidio_sidecar.processing import FindingKind, finding_id

    value = "ALPHA HOTEL GE KOS"
    text = f"{value} | {value}"
    confirmed = (Finding("ORGANIZATION", 0, 11, 0.95, FindingSource.NER, text="ALPHA HOTEL"),)
    start = text.rindex(value)
    original = Finding(
        "PERSON",
        start,
        len(text),
        0.25,
        FindingSource.SUSPICION,
        text=value,
        detector=NAME_SUSPICION_DETECTOR,
    )
    detection = DetectionResult(
        confirmed=confirmed,
        suspicious=_with_exact_duplicate_suspicions(
            text, confirmed=confirmed, suspicious=(original,)
        ),
    )
    model = LabelModel(
        {
            value: _distribution(person=0.95, term=0.05),
            "GE KOS": _distribution(organization_or_product=0.95, term=0.05),
        }
    )

    result = with_assessments(
        text,
        detection,
        base_profile(),
        model,  # type: ignore[arg-type]
        SecondOpinionOptions(policy="protect", keep_threshold=0.95),
    )

    by_text = {finding.text: finding for finding in result.suspicious}
    assert by_text[value].entity_type == "PERSON"
    assert by_text["GE KOS"].entity_type == "PERSON"
    # A finding must not silently revert to its old category based on a previous ID.
    valid_ids = {finding_id(item, kind=FindingKind.SUSPICIOUS) for item in result.suspicious}
    assert set(result.assessments) <= valid_ids
