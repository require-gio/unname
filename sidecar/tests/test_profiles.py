"""Schema, catalog and rule tests for spec format."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from presidio_analyzer.input_validation import ConfigurationValidator
from pydantic import ValidationError

from presidio_sidecar.processing import ProcessingRequest
from presidio_sidecar.profiles import (
    DocumentFormat,
    DuplicateProfileError,
    Profile,
    ProfileLoadError,
    ProfileRuleViolation,
    load_profile,
    load_profiles,
    profile_by_id,
)

PROFILES_DIRECTORY = Path(__file__).resolve().parents[2] / "profiles"


@pytest.fixture(scope="module")
def profiles() -> tuple[Profile, ...]:
    return load_profiles(PROFILES_DIRECTORY)


@pytest.fixture(scope="module")
def invoice() -> Profile:
    return load_profile(PROFILES_DIRECTORY / "de-at-rechnung-v3.yaml")


@pytest.fixture(scope="module")
def llm_upload() -> Profile:
    return load_profile(PROFILES_DIRECTORY / "llm-upload-v1.yaml")


def _payload(profile: Profile) -> dict[str, object]:
    return profile.model_dump(mode="json")


def test_load_three_start_profiles_atomically(profiles: tuple[Profile, ...]) -> None:
    assert {profile.id for profile in profiles} == {
        "de-at-rechnung-v3",
        "kanzlei-beratung-v1",
        "llm-upload-v1",
    }
    assert profile_by_id("KANZLEI-BERATUNG-V1", profiles).vault.reversible is True
    assert profile_by_id("llm-upload-v1", profiles).vault.persist_mapping is True
    assert profile_by_id("de-at-rechnung-v3", profiles).vault.reversible is False


def test_invoice_profile_covers_spec_format(invoice: Profile) -> None:
    assert invoice.version == "3.0.0"
    assert invoice.languages == ("de", "en")
    assert invoice.input.allow == (
        DocumentFormat.PDF,
        DocumentFormat.DOCX,
        DocumentFormat.XLSX,
        DocumentFormat.CSV,
        DocumentFormat.PNG,
        DocumentFormat.JPG,
    )
    assert invoice.max_file_bytes == 50 * 1024 * 1024
    assert invoice.review.required_entities == ("IBAN_CODE", "DE_TAX_ID", "PERSON")
    assert invoice.review.block_export_on_unreviewed is True
    assert invoice.export.preserve_original is True


def test_presidio_part_is_native_analyzer_configuration(invoice: Profile) -> None:
    configuration = invoice.to_presidio_analyzer_config()

    assert ConfigurationValidator.validate_analyzer_configuration(configuration) == configuration
    assert configuration == {
        "supported_languages": ["de", "en"],
        # PERSON overrides the global review threshold.
        "default_score_threshold": 0.35,
    }
    assert "IBAN_CODE" in invoice.presidio_entities
    assert "ORG" not in invoice.presidio_entities
    assert "ORGANIZATION" in invoice.presidio_entities


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Beleg.PDF", DocumentFormat.PDF),
        ("foto.jpeg", DocumentFormat.JPG),
        ("tabelle.CsV", DocumentFormat.CSV),
    ],
)
def test_allowed_formats_and_aliases_are_accepted(
    invoice: Profile,
    filename: str,
    expected: DocumentFormat,
) -> None:
    assert invoice.validate_upload(filename, invoice.max_file_bytes) is expected


def test_markdown_alias_for_llm_upload(llm_upload: Profile) -> None:
    assert (
        llm_upload.validate_upload("prompt.markdown", llm_upload.max_file_bytes)
        is DocumentFormat.MARKDOWN
    )


@pytest.mark.parametrize(
    ("filename", "code"),
    [
        ("programm.exe", "unsupported_file_format"),
        ("README", "missing_file_extension"),
        ("daten.json", "format_not_allowed"),
    ],
)
def test_disallowed_formats_are_rejected_fail_closed(
    invoice: Profile,
    filename: str,
    code: str,
) -> None:
    with pytest.raises(ProfileRuleViolation) as captured:
        invoice.validate_upload(filename, 1)
    assert captured.value.code == code


@pytest.mark.parametrize("size", [-1, 1.5, True])
def test_invalid_file_size_is_rejected(invoice: Profile, size: object) -> None:
    with pytest.raises(ProfileRuleViolation) as captured:
        invoice.validate_upload("beleg.pdf", size)  # type: ignore[arg-type]
    assert captured.value.code == "invalid_file_size"


def test_file_size_over_profile_limit_is_rejected(invoice: Profile) -> None:
    with pytest.raises(ProfileRuleViolation) as captured:
        invoice.validate_upload("beleg.pdf", invoice.max_file_bytes + 1)
    assert captured.value.code == "file_too_large"


def test_export_blocked_only_by_open_mandatory_entities(invoice: Profile) -> None:
    assert invoice.export_blockers(["org", "PERSON", "iban_code"]) == (
        "IBAN_CODE",
        "PERSON",
    )
    with pytest.raises(ProfileRuleViolation) as captured:
        invoice.assert_export_allowed(["PERSON"])
    assert captured.value.code == "required_review_pending"

    invoice.assert_export_allowed(["ORG", "EMAIL_ADDRESS"])
    invoice.assert_export_allowed([])


def test_apply_defaults_are_processing_request_compatible(invoice: Profile) -> None:
    defaults = invoice.apply_defaults()

    assert defaults == {
        "mode": "token_deterministic",
        "protection_level": 2,
        "overrides": {
            "keep_plain": ["LOCATION"],
            "force_handle": ["CUSTOMER_NUMBER"],
        },
        "policies": {
            "IBAN_CODE": "remove",
            "DE_TAX_ID": "remove",
            "EMAIL_ADDRESS": "placeholder",
        },
        "vault_reversible": False,
    }
    with pytest.raises(ProfileRuleViolation, match="nicht erlaubt"):
        invoice.validate_mode("surrogate")


def test_all_start_profiles_provide_valid_apply_defaults(
    profiles: tuple[Profile, ...],
) -> None:
    for profile in profiles:
        request = ProcessingRequest.from_dict(profile.apply_defaults())
        assert request.mode.value == profile.mode.default.value
        assert request.vault_reversible is profile.vault.reversible


def test_disallowed_apply_mode_is_rejected(llm_upload: Profile) -> None:
    with pytest.raises(ProfileRuleViolation) as captured:
        llm_upload.validate_mode("remove")
    assert captured.value.code == "processing_mode_not_allowed"


def test_locked_profile_allows_no_deviation(invoice: Profile) -> None:
    payload = _payload(invoice)
    mode = payload["mode"]
    assert isinstance(mode, dict)
    mode["user_selectable"] = False
    mode["allowed"] = ["token_deterministic"]
    locked = Profile.model_validate(payload)

    with pytest.raises(ProfileRuleViolation) as captured:
        locked.validate_mode("surrogate")
    assert captured.value.code == "processing_mode_locked"


def test_mode_default_must_be_allowed(invoice: Profile) -> None:
    payload = _payload(invoice)
    mode = payload["mode"]
    assert isinstance(mode, dict)
    mode["allowed"] = ["remove"]

    with pytest.raises(ValidationError, match="mode.default"):
        Profile.model_validate(payload)


def test_runtime_alias_duplicates_rejected_in_rule_layers(
    invoice: Profile,
) -> None:
    payload = _payload(invoice)
    thresholds = payload["thresholds"]
    assert isinstance(thresholds, dict)
    thresholds["ORG"] = {"needs_review": 0.4}
    thresholds["ORGANIZATION"] = {"needs_review": 0.5}

    with pytest.raises(ValidationError, match="ORG-Normalisierung"):
        Profile.model_validate(payload)


def test_threshold_zero_rejected_before_runtime(
    invoice: Profile,
) -> None:
    payload = _payload(invoice)
    thresholds = payload["thresholds"]
    assert isinstance(thresholds, dict)
    thresholds["PERSON"] = {"needs_review": 0}

    with pytest.raises(ValidationError):
        Profile.model_validate(payload)


def test_user_selectable_false_has_only_standard_mode(invoice: Profile) -> None:
    payload = _payload(invoice)
    mode = payload["mode"]
    assert isinstance(mode, dict)
    mode["user_selectable"] = False

    with pytest.raises(ValidationError, match="user_selectable=false"):
        Profile.model_validate(payload)


def test_policy_accepts_only_seven_modes_or_inherit(invoice: Profile) -> None:
    payload = _payload(invoice)
    policies = payload["policies"]
    assert isinstance(policies, dict)
    policies["PERSON"] = "encrypt_and_email"

    with pytest.raises(ValidationError):
        Profile.model_validate(payload)


def test_original_preservation_is_schema_invariant(invoice: Profile) -> None:
    payload = _payload(invoice)
    export = payload["export"]
    assert isinstance(export, dict)
    export["preserve_original"] = False

    with pytest.raises(ValidationError):
        Profile.model_validate(payload)


def test_llm_upload_requires_restore_path(llm_upload: Profile) -> None:
    payload = _payload(llm_upload)
    vault = payload["vault"]
    assert isinstance(vault, dict)
    vault["reversible"] = False
    vault["persist_mapping"] = False

    with pytest.raises(ValidationError, match="LLM-Upload"):
        Profile.model_validate(payload)


def test_llm_upload_requires_reversible_standard_mode(llm_upload: Profile) -> None:
    payload = _payload(llm_upload)
    mode = payload["mode"]
    assert isinstance(mode, dict)
    mode["default"] = "remove"
    mode["allowed"] = ["remove"]

    with pytest.raises(ValidationError, match="rueckholbaren Standardmodus"):
        Profile.model_validate(payload)


def test_llm_upload_verbietet_unumkehrbare_entity_policy(llm_upload: Profile) -> None:
    payload = _payload(llm_upload)
    policies = payload["policies"]
    assert isinstance(policies, dict)
    policies["API_KEY"] = "remove"

    with pytest.raises(ValidationError, match="unumkehrbaren Entity-Policies"):
        Profile.model_validate(payload)


def test_review_and_apply_normalize_org_alias(invoice: Profile) -> None:
    payload = _payload(invoice)
    review = payload["review"]
    policies = payload["policies"]
    assert isinstance(review, dict)
    assert isinstance(policies, dict)
    review["required_entities"] = ["ORG"]
    review["block_export_on_unreviewed"] = True
    policies["ORG"] = "placeholder"
    aliased = Profile.model_validate(payload)

    assert aliased.presidio_entities.count("ORGANIZATION") == 1
    assert aliased.required_review_entities == ("ORGANIZATION",)
    assert aliased.export_blockers(["ORGANIZATION"]) == ("ORGANIZATION",)
    assert aliased.apply_defaults()["policies"]["ORGANIZATION"] == "placeholder"  # type: ignore[index]


def test_unknown_fields_are_not_ignored(invoice: Profile) -> None:
    payload = _payload(invoice)
    review = payload["review"]
    assert isinstance(review, dict)
    review["block_export_on_unreviewd"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Profile.model_validate(payload)


def test_loader_encapsulates_yaml_and_schema_errors(tmp_path: Path, invoice: Profile) -> None:
    malformed = tmp_path / "kaputt.yaml"
    malformed.write_text("id: [\n", encoding="utf-8")
    with pytest.raises(ProfileLoadError):
        load_profile(malformed)

    wrong_schema = tmp_path / "falsch.yaml"
    payload = _payload(invoice)
    payload["unexpected"] = True
    wrong_schema.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ProfileLoadError):
        load_profile(wrong_schema)


def test_catalog_rejects_duplicate_ids(tmp_path: Path, invoice: Profile) -> None:
    serialized = yaml.safe_dump(_payload(invoice), sort_keys=False)
    (tmp_path / "eins.yaml").write_text(serialized, encoding="utf-8")
    (tmp_path / "zwei.yml").write_text(serialized, encoding="utf-8")

    with pytest.raises(DuplicateProfileError, match="Doppelte Profil-ID"):
        load_profiles(tmp_path)
