"""Protection levels, modes, and review of phase-2 processing."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
from presidio_sidecar.processing import (
    FindingKind,
    ProcessingMode,
    ProcessingRequest,
    ProcessingValidationError,
    ProtectionLevel,
    ProtectionOverrides,
    Reversibility,
    ReviewAction,
    ReviewDecision,
    TextProcessor,
    UnknownEntityTypeError,
    finding_id,
)
from presidio_sidecar.residual_risk import ResidualRiskAnalyzer
from presidio_sidecar.vault import TokenVault


def _finding(text: str, value: str, entity_type: str, *, score: float = 0.9) -> Finding:
    start = text.index(value)
    return Finding(
        entity_type,
        start,
        start + len(value),
        score,
        FindingSource.NER,
        text=value,
    )


@pytest.fixture
def processor(tmp_path: Path) -> TextProcessor:
    vault = TokenVault(tmp_path / "state")
    instance = TextProcessor(vault)
    try:
        yield instance
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (
            ProtectionLevel.CRITICAL,
            "[IBAN] gehört Hans Meier in Wien.",
        ),
        (
            ProtectionLevel.DIRECT,
            "[IBAN] gehört [Name] in Wien.",
        ),
        (
            ProtectionLevel.INDIRECT,
            "[IBAN] gehört [Name] in [Ort].",
        ),
    ],
)
def test_protection_levels_are_cumulative(
    processor: TextProcessor,
    level: ProtectionLevel,
    expected: str,
) -> None:
    text = "DE02120300000000202051 gehört Hans Meier in Wien."
    detection = DetectionResult(
        confirmed=(
            _finding(text, "DE02120300000000202051", "IBAN_CODE"),
            _finding(text, "Hans Meier", "PERSON"),
            _finding(text, "Wien", "LOCATION"),
        )
    )

    result = processor.apply(
        text,
        detection,
        ProcessingRequest(
            mode=ProcessingMode.PLACEHOLDER,
            protection_level=level,
        ),
    )

    assert result.text == expected
    assert result.facts.protection_level is level
    assert result.facts.has_plaintext_overrides is (level is not ProtectionLevel.INDIRECT)


def test_overrides_work_both_ways_and_must_be_disjoint(
    processor: TextProcessor,
) -> None:
    text = "Hans Meier aus Wien"
    detection = DetectionResult(
        confirmed=(
            _finding(text, "Hans Meier", "PERSON"),
            _finding(text, "Wien", "LOCATION"),
        )
    )
    request = ProcessingRequest(
        mode=ProcessingMode.REMOVE,
        protection_level=ProtectionLevel.CRITICAL,
        overrides=ProtectionOverrides(
            keep_plain=frozenset({"PERSON"}),
            force_handle=frozenset({"LOCATION"}),
        ),
    )

    result = processor.apply(text, detection, request)

    assert result.text == "Hans Meier aus "
    assert result.facts.has_plaintext_overrides is True
    assert result.facts.plaintext_override_entities == ("PERSON",)
    assert result.facts.force_handle_entities == ("LOCATION",)
    with pytest.raises(ProcessingValidationError, match="disjunkt"):
        ProtectionOverrides(
            keep_plain=frozenset({"PERSON"}),
            force_handle=frozenset({"PERSON"}),
        )


def test_level_three_captures_location_and_date(
    processor: TextProcessor,
) -> None:
    text = "Hans Meier war am 03.04.2026 in Wien."
    detection = DetectionResult(
        confirmed=(
            _finding(text, "Hans Meier", "PERSON"),
            _finding(text, "03.04.2026", "DATE_TIME"),
            _finding(text, "Wien", "LOCATION"),
        )
    )

    level_one = processor.apply(
        text,
        detection,
        ProcessingRequest(
            mode=ProcessingMode.PLACEHOLDER,
            protection_level=ProtectionLevel.CRITICAL,
        ),
    )
    level_three = processor.apply(
        text,
        detection,
        ProcessingRequest(
            mode=ProcessingMode.PLACEHOLDER,
            protection_level=ProtectionLevel.INDIRECT,
        ),
    )

    assert level_one.text == text
    assert level_three.text == "[Name] war am [Datum] in [Ort]."


def test_unknown_finding_type_fails_closed_before_any_output(
    processor: TextProcessor,
) -> None:
    text = "Geheim"
    detection = DetectionResult(confirmed=(_finding(text, text, "FUTURE_UNCLASSIFIED_PII"),))

    with pytest.raises(UnknownEntityTypeError, match="keine Schutzstufen"):
        processor.apply(
            text,
            detection,
            ProcessingRequest(
                mode=ProcessingMode.REMOVE,
                protection_level=ProtectionLevel.INDIRECT,
            ),
        )

    assert not processor.vault.mapping_path.exists()


@pytest.mark.parametrize(
    ("mode", "expected_reversibility", "eligible"),
    [
        (ProcessingMode.TOKEN_DETERMINISTIC, Reversibility.VAULT, False),
        (ProcessingMode.SURROGATE, Reversibility.VAULT, False),
        (ProcessingMode.ENCRYPT, Reversibility.KEY, False),
        (ProcessingMode.HASH, Reversibility.NONE, False),
        (ProcessingMode.PLACEHOLDER, Reversibility.NONE, True),
        (ProcessingMode.MASK, Reversibility.NONE, True),
        (ProcessingMode.REMOVE, Reversibility.NONE, True),
    ],
)
def test_all_seven_modes_provide_operator_and_reversibility_facts(
    processor: TextProcessor,
    mode: ProcessingMode,
    expected_reversibility: Reversibility,
    eligible: bool,
) -> None:
    text = "Hans Meier"
    result = processor.apply(
        text,
        DetectionResult(confirmed=(_finding(text, text, "PERSON"),)),
        ProcessingRequest(
            mode=mode,
            protection_level=ProtectionLevel.DIRECT,
        ),
    )

    replacement = result.text
    if mode is ProcessingMode.TOKEN_DETERMINISTIC:
        assert re.fullmatch(r"<<PERSON_[0-9a-f]{8}>>", replacement)
        assert processor.vault.deanonymize_tokens(replacement).text == text
    elif mode is ProcessingMode.SURROGATE:
        assert replacement != text
        assert processor.vault.deanonymize_surrogates(replacement).text == text
    elif mode is ProcessingMode.ENCRYPT:
        assert re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", replacement)
        assert processor.decrypt_encrypted_value(replacement) == text
    elif mode is ProcessingMode.HASH:
        assert re.fullmatch(r"[0-9a-f]{64}", replacement)
    elif mode is ProcessingMode.PLACEHOLDER:
        assert replacement == "[Name]"
    elif mode is ProcessingMode.MASK:
        assert replacement == "•" * len(text)
    else:
        assert replacement == ""

    assert result.facts.effective_modes == (mode,)
    assert result.facts.reversibility_by_mode[mode] is expected_reversibility
    assert result.facts.only_anonymization_eligible_modes is eligible
    assert result.facts.contains_surrogates is (mode is ProcessingMode.SURROGATE)
    assert result.facts.has_reversible_changes is (expected_reversibility is not Reversibility.NONE)
    assert result.facts.has_irreversible_changes is (expected_reversibility is Reversibility.NONE)
    notices = result.report.as_dict()["notices"]
    if mode is ProcessingMode.SURROGATE:
        assert "enthält Decknamen" in notices
    if mode is ProcessingMode.HASH:
        assert any("Pseudonymisierung" in notice for notice in notices)


def test_hash_is_stable_but_remains_pseudonymization(
    processor: TextProcessor,
) -> None:
    first_text = "Hans  Meier"
    second_text = "HANS MEIER"
    first = processor.apply(
        first_text,
        DetectionResult(confirmed=(_finding(first_text, first_text, "PERSON"),)),
        ProcessingRequest(mode=ProcessingMode.HASH),
    )
    second = processor.apply(
        second_text,
        DetectionResult(confirmed=(_finding(second_text, second_text, "PERSON"),)),
        ProcessingRequest(mode=ProcessingMode.HASH),
    )

    assert first.text == second.text
    assert first.facts.has_nonreturning_pseudonyms is True
    assert first.facts.only_anonymization_eligible_modes is False


def test_token_without_reversible_vault_stores_no_mapping(
    tmp_path: Path,
) -> None:
    vault = TokenVault(tmp_path / "state")
    processor = TextProcessor(vault)
    text = "Hans Meier"
    try:
        result = processor.apply(
            text,
            DetectionResult(confirmed=(_finding(text, text, "PERSON"),)),
            ProcessingRequest(
                mode=ProcessingMode.TOKEN_DETERMINISTIC,
                vault_reversible=False,
            ),
        )

        assert result.facts.has_reversible_changes is False
        assert result.facts.has_nonreturning_pseudonyms is True
        assert not vault.mapping_path.exists()
    finally:
        vault.close()


def test_pseudonym_without_reversible_vault_is_rejected(
    processor: TextProcessor,
) -> None:
    text = "Hans Meier"

    with pytest.raises(ProcessingValidationError, match="reversiblen"):
        processor.apply(
            text,
            DetectionResult(confirmed=(_finding(text, text, "PERSON"),)),
            ProcessingRequest(
                mode=ProcessingMode.SURROGATE,
                vault_reversible=False,
            ),
        )


def test_entity_policy_creates_mixed_effective_modes_and_facts(
    processor: TextProcessor,
) -> None:
    text = "Hans Meier: DE02120300000000202051"
    detection = DetectionResult(
        confirmed=(
            _finding(text, "Hans Meier", "PERSON"),
            _finding(text, "DE02120300000000202051", "IBAN_CODE"),
        )
    )
    result = processor.apply(
        text,
        detection,
        ProcessingRequest(
            mode=ProcessingMode.REMOVE,
            protection_level=ProtectionLevel.DIRECT,
            policies={"PERSON": ProcessingMode.TOKEN_DETERMINISTIC},
        ),
    )

    assert re.fullmatch(r"<<PERSON_[0-9a-f]{8}>>: ", result.text)
    assert result.facts.effective_modes == (
        ProcessingMode.TOKEN_DETERMINISTIC,
        ProcessingMode.REMOVE,
    )
    assert result.facts.has_reversible_changes is True
    assert result.facts.has_irreversible_changes is True
    assert result.facts.only_anonymization_eligible_modes is False
    assert result.facts.irreversible_count == 1
    assert result.facts.irreversible_counts_by_entity == {"IBAN_CODE": 1}

    payload = result.as_dict()
    assert payload["effective_modes"] == [
        ProcessingMode.TOKEN_DETERMINISTIC,
        ProcessingMode.REMOVE,
    ]
    assert payload["irreversible_counts"] == {
        "total": 1,
        "by_entity": {"IBAN_CODE": 1},
    }
    assert payload["has_reversible_changes"] is True


def test_confirmed_is_default_suspicious_needs_accept_decision(
    processor: TextProcessor,
) -> None:
    text = "Hans Meier und Petra Novak"
    confirmed = _finding(text, "Hans Meier", "PERSON")
    suspicious = _finding(text, "Petra Novak", "PERSON", score=0.2)
    detection = DetectionResult(
        confirmed=(confirmed,),
        suspicious=(suspicious,),
    )

    without_review = processor.apply(
        text,
        detection,
        ProcessingRequest(mode=ProcessingMode.PLACEHOLDER),
    )
    suspicion_id = finding_id(suspicious, kind=FindingKind.SUSPICIOUS)
    with_review = processor.apply(
        text,
        detection,
        ProcessingRequest(
            mode=ProcessingMode.PLACEHOLDER,
            decisions=(
                ReviewDecision(
                    finding_id=suspicion_id,
                    action=ReviewAction.ACCEPT,
                    mode=ProcessingMode.MASK,
                ),
            ),
        ),
    )

    assert without_review.text == "[Name] und Petra Novak"
    assert without_review.facts.has_unresolved_suspicions is True
    assert without_review.facts.has_plaintext_overrides is True
    assert with_review.text == "[Name] und " + "•" * len("Petra Novak")
    assert with_review.facts.has_unresolved_suspicions is False
    assert with_review.report.accepted_suspicion_count == 1


def test_reject_and_unknown_review_id_are_handled_unambiguously(
    processor: TextProcessor,
) -> None:
    text = "Hans Meier"
    finding = _finding(text, text, "PERSON")
    identifier = finding_id(finding, kind=FindingKind.CONFIRMED)
    detection = DetectionResult(confirmed=(finding,))

    rejected = processor.apply(
        text,
        detection,
        ProcessingRequest(
            mode=ProcessingMode.REMOVE,
            decisions=(ReviewDecision(identifier, ReviewAction.REJECT),),
        ),
    )
    assert rejected.text == text
    assert rejected.report.rejected_counts_by_entity == {"PERSON": 1}

    with pytest.raises(ProcessingValidationError, match="kein Finding"):
        processor.apply(
            text,
            detection,
            ProcessingRequest(
                mode=ProcessingMode.REMOVE,
                decisions=(ReviewDecision("f_unbekannt", ReviewAction.ACCEPT),),
            ),
        )


def test_finding_id_is_stable_and_contains_no_plain_value() -> None:
    left = Finding("PERSON", 4, 14, 0.9, FindingSource.NER, text="Hans Meier")
    right = Finding("PERSON", 4, 14, 0.9, FindingSource.NER, text="Petra Novak")

    left_id = finding_id(left, kind=FindingKind.CONFIRMED)
    right_id = finding_id(right, kind=FindingKind.CONFIRMED)

    assert left_id == right_id
    assert "Hans" not in left_id
    assert re.fullmatch(r"f_[0-9a-f]{20}", left_id)


def test_back_to_front_and_utf16_metadata_remain_correct(
    processor: TextProcessor,
) -> None:
    text = "😀 Hans Meier traf Petra Novak."
    detection = DetectionResult(
        confirmed=(
            _finding(text, "Hans Meier", "PERSON"),
            _finding(text, "Petra Novak", "PERSON"),
        )
    )
    result = processor.apply(
        text,
        detection,
        ProcessingRequest(mode=ProcessingMode.PLACEHOLDER),
    )

    assert result.text == "😀 [Name] traf [Name]."
    assert result.offset_encoding == "utf-16"
    first, second = result.applied_findings
    # Emoji belegt in WebView/JavaScript zwei UTF-16-Codeeinheiten.
    assert (first.original_start, first.original_end) == (3, 13)
    assert (first.output_start, first.output_end) == (3, 9)
    assert second.output_start > first.output_end


def test_residual_risk_gets_exact_python_output_spans(tmp_path: Path) -> None:
    class RecordingRisk(ResidualRiskAnalyzer):
        spans: tuple[tuple[int, int], ...] = ()

        def analyze(
            self,
            cleaned_text: str,
            *,
            remaining_entity_types=(),
            replacement_spans=(),
        ):
            self.spans = tuple(replacement_spans)
            return super().analyze(
                cleaned_text,
                remaining_entity_types=remaining_entity_types,
                replacement_spans=self.spans,
            )

    vault = TokenVault(tmp_path / "state")
    risk = RecordingRisk()
    processor = TextProcessor(vault, residual_risk=risk)
    text = "😀 Hans Meier"
    try:
        result = processor.apply(
            text,
            DetectionResult(confirmed=(_finding(text, "Hans Meier", "PERSON"),)),
            ProcessingRequest(mode=ProcessingMode.PLACEHOLDER),
        )
    finally:
        vault.close()

    # Python: Emoji=1; HTTP/WebView: Emoji=2 UTF-16-Codeeinheiten.
    assert risk.spans == ((2, 8),)
    assert result.applied_findings[0].output_start == 3


def test_review_report_contains_neither_plaintext_nor_replacement(
    processor: TextProcessor,
) -> None:
    text = "Hans Meier"
    result = processor.apply(
        text,
        DetectionResult(confirmed=(_finding(text, text, "PERSON"),)),
        ProcessingRequest(mode=ProcessingMode.TOKEN_DETERMINISTIC),
    )

    report = json.dumps(result.report.as_dict(), ensure_ascii=False)
    assert "Hans Meier" not in report
    assert result.text not in report
    assert report.count("PERSON") >= 1


def test_api_level_parser_validates_alias_policies_and_payload() -> None:
    request = ProcessingRequest.from_dict(
        {
            "mode": "remove",
            "protection_level": 3,
            "overrides": {"keep_plain": ["ORG"], "force_handle": ["PERSON"]},
            "policies": {"EMAIL_ADDRESS": "placeholder", "LOCATION": "inherit"},
            "vault_reversible": False,
            "decisions": [],
        }
    )

    assert request.protection_level is ProtectionLevel.INDIRECT
    assert request.overrides.keep_plain == {"ORGANIZATION"}
    assert request.policies == {"EMAIL_ADDRESS": ProcessingMode.PLACEHOLDER}
    with pytest.raises(ProcessingValidationError):
        ProcessingRequest.from_dict({"mode": "unbekannt"})
    with pytest.raises(ProcessingValidationError):
        ProcessingRequest.from_dict({"mode": "remove", "decisions": "keine-liste"})
