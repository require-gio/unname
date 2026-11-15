"""Deterministic Presidio recognizers for German and Austrian data.

Presidio 2.2.364 ships the required German recognizers but does not
activate them in the standard registry. This module instantiates the
explicitly required, model-free registry. ``DE_PLZ`` is intentionally
opt-in: the upstream recognizer has a base score of only 0.05 due to
high confusion risk and must only be enabled in an address context.

Austrian types are missing in Presidio 2.2.364. Their patterns use
fixed lengths and word boundaries on both sides. This prevents a
following word from becoming part of a match (spec §2).

Recognizers for numbers and IDs depend on the **country**
(``countries``), not the language. The language packs provide month
names for dates ("3. März 2024", "March 3, 2024") and field labels
for customer and file numbers.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

import phonenumbers
import pycountry
import tldextract
from presidio_analyzer import (
    AnalysisExplanation,
    EntityRecognizer,
    LocalRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    CryptoRecognizer,
    DateRecognizer,
    DeHandelsregisterRecognizer,
    DeHealthInsuranceRecognizer,
    DeIdCardRecognizer,
    DeKfzRecognizer,
    DePassportRecognizer,
    DePlzRecognizer,
    DeSocialSecurityRecognizer,
    DeTaxIdRecognizer,
    DeTaxNumberRecognizer,
    DeVatIdRecognizer,
    EmailRecognizer,
    IbanRecognizer,
    IpRecognizer,
    PhoneRecognizer,
    UrlRecognizer,
)

from .detection.context import GERMAN_STREET_SUFFIXES

__all__ = [
    "BUILT_IN_ENTITY_TYPES",
    "CUSTOM_ENTITY_TYPES",
    "DEFAULT_ENTITY_TYPES",
    "AtCompanyRegisterRecognizer",
    "AtSocialSecurityRecognizer",
    "AtUidRecognizer",
    "ApiKeyRecognizer",
    "BicRecognizer",
    "BoundedUrlRecognizer",
    "CaseNumberRecognizer",
    "ContextualDePlzRecognizer",
    "CustomerNumberRecognizer",
    "IdentifyingIpRecognizer",
    "MonthNameDateRecognizer",
    "OfflineEmailRecognizer",
    "SecretRecognizer",
    "StreetAddressRecognizer",
    "create_deterministic_recognizers",
    "register_deterministic_recognizers",
]

BUILT_IN_ENTITY_TYPES = frozenset(
    {
        "IBAN_CODE",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "IP_ADDRESS",
        "URL",
        "CREDIT_CARD",
        "CRYPTO",
        "DATE_TIME",
        "DE_TAX_ID",
        "DE_TAX_NUMBER",
        "DE_PASSPORT",
        "DE_ID_CARD",
        "DE_SOCIAL_SECURITY",
        "DE_HEALTH_INSURANCE",
        "DE_KFZ",
        "DE_HANDELSREGISTER",
        "DE_VAT_ID",
    }
)
CUSTOM_ENTITY_TYPES = frozenset(
    {
        "AT_UID",
        "AT_SVNR",
        "AT_FIRMENBUCH",
        "BIC",
        "CASE_NUMBER",
        "CUSTOMER_NUMBER",
        "API_KEY",
        "SECRET",
        "STREET_ADDRESS",
    }
)
DEFAULT_ENTITY_TYPES = BUILT_IN_ENTITY_TYPES | CUSTOM_ENTITY_TYPES

_LEFT_WORD_BOUNDARY = r"(?<!\w)"
_RIGHT_WORD_BOUNDARY = r"(?!\w)"
_SEPARATOR = r"[ \t.\-]?"
_ISO_COUNTRY_CODES = frozenset(country.alpha_2 for country in pycountry.countries)


class OfflineEmailRecognizer(EmailRecognizer):
    """Presidio email detection with an exclusively bounded TLD list.

    ``EmailRecognizer.validate_result`` otherwise uses the module-wide
    ``tldextract.extract``. This attempts to load the Public Suffix List
    from the network when the cache is empty, violating the sidecar's
    offline promise.
    """

    _TLD_EXTRACTOR: ClassVar[tldextract.TLDExtract] = tldextract.TLDExtract(
        cache_dir=None,
        suffix_list_urls=(),
        fallback_to_snapshot=True,
    )

    def validate_result(self, pattern_text: str) -> bool:
        return self._TLD_EXTRACTOR(pattern_text).fqdn != ""


class BoundedUrlRecognizer(UrlRecognizer):
    """Presidio URL detection only for whole words.

    The pattern without schema otherwise finds domains in the middle of
    filenames and paths: ``ollama.se`` in ``ollama.service``, ``M.gg`` in
    ``model-q4_K_M.gguf``, ``sources.li`` in ``sources.list``. An address
    never ends before a letter and never starts after one.

    Without a schema, an ending like ``Sc`` in ``M.Sc`` or ``B.Sc`` (both
    genuine country endings) is a conclusion: addresses are written in
    lowercase or all caps, never with a capitalized first letter of the
    ending.
    """

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        return [
            result
            for result in results
            if not _continues_word(text, result.start - 1)
            and not _continues_word(text, result.end)
            and not _is_abbreviation(text[result.start : result.end])
        ]


def _continues_word(text: str, index: int) -> bool:
    return 0 <= index < len(text) and (text[index].isalnum() or text[index] in "_-")


def _is_abbreviation(value: str) -> bool:
    if "://" in value:
        return False
    host = re.split(r"[/:?#]", value, maxsplit=1)[0]
    tld = host.rsplit(".", 1)[-1]
    return len(tld) >= 2 and tld[0].isupper() and tld[1:].islower()


class IdentifyingIpRecognizer(IpRecognizer):
    """IP addresses that may designate a device.

    ``127.0.0.1``, ``0.0.0.0``, ``::1``, broadcast and multicast appear in
    tutorials and configurations; they designate no one.
    """

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        return [result for result in results if _identifies_device(text[result.start : result.end])]


def _identifies_device(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return True
    return not (
        address.is_loopback
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or (isinstance(address, ipaddress.IPv4Address) and int(address) == 0xFFFFFFFF)
    )


#: month/year or day.month.year, single or as a range
#: ("05/2024 – 07/2025"). libphonenumber reads this for DE as a landline number.
_DATE_LIKE_PHONE = re.compile(
    r"(?:\d{1,2}[./])?(?:0?[1-9]|1[0-2])[./](?:19|20)\d{2}"
    r"(?:\s*[-–—]\s*(?:\d{1,2}[./])?(?:0?[1-9]|1[0-2])[./](?:19|20)\d{2})?"
)
#: Score of a valid number in international notation
#: ("+49 177 …", "0043 …"): Data and article numbers never start this way.
INTERNATIONAL_PHONE_SCORE = 0.95


class RegionalPhoneRecognizer(PhoneRecognizer):
    """Also limits explicit international numbers to profile regions.

    Additionally discards dates that libphonenumber reads as numbers,
    and accepts valid numbers with country codes without asking.
    """

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
    ) -> list[RecognizerResult]:
        results = super().analyze(
            text,
            entities,
            nlp_artifacts=nlp_artifacts,
        )
        allowed = frozenset(self.supported_regions)
        fallback_region = next(iter(self.supported_regions))
        filtered: list[RecognizerResult] = []
        for result in results:
            value = text[result.start : result.end]
            if _DATE_LIKE_PHONE.fullmatch(value.strip("()[] ")):
                continue
            try:
                parsed = phonenumbers.parse(value, fallback_region)
            except phonenumbers.NumberParseException:
                continue
            if phonenumbers.region_code_for_number(parsed) not in allowed:
                continue
            if value.lstrip("( ").startswith(("+", "00")) and phonenumbers.is_valid_number(parsed):
                result.score = max(result.score, INTERNATIONAL_PHONE_SCORE)
            filtered.append(result)
        return filtered


class ContextualDePlzRecognizer(DePlzRecognizer):
    """DE-PLZ only in explicit address context, even without NLP artifacts."""

    _ADDRESS_CONTEXT = re.compile(
        r"\b(?:plz|postleitzahl|postanschrift|adresse|anschrift|"
        r"wohnanschrift|lieferadresse|rechnungsadresse|hausnummer|"
        r"stra(?:ß|ss)e|gasse|allee|platz|wohnort|ort|stadt)\b",
        re.IGNORECASE,
    )

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        results = super().analyze(
            text,
            entities,
            nlp_artifacts=nlp_artifacts,
            regex_flags=regex_flags,
        )
        contextual: list[RecognizerResult] = []
        for result in results:
            line_start = max(
                text.rfind("\n", 0, result.start),
                text.rfind("\r", 0, result.start),
            )
            line_end_candidates = [
                position
                for position in (
                    text.find("\n", result.end),
                    text.find("\r", result.end),
                )
                if position >= 0
            ]
            line_end = min(line_end_candidates, default=len(text))
            before = text[max(line_start + 1, result.start - 96) : result.start]
            after = text[result.end : min(line_end, result.end + 48)]
            if self._ADDRESS_CONTEXT.search(f"{before} {after}"):
                contextual.append(result)
        return contextual


class AtUidRecognizer(PatternRecognizer):
    """Detects Austrian UID numbers and validates the ATU check digit."""

    COUNTRY_CODE = "at"
    PATTERNS: ClassVar[list[Pattern]] = [
        Pattern(
            "Oesterreichische UID (ATU + 8 Ziffern)",
            (
                rf"{_LEFT_WORD_BOUNDARY}AT{_SEPARATOR}U"
                rf"{_SEPARATOR}\d{{8}}{_RIGHT_WORD_BOUNDARY}"
            ),
            0.5,
        )
    ]
    CONTEXT: ClassVar[list[str]] = [
        "uid",
        "uid-nummer",
        "umsatzsteuer-identifikationsnummer",
        "umsatzsteuer",
        "ust-id",
        "vat",
        "rechnung",
    ]

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entity="AT_UID",
            supported_language=supported_language,
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:
        """Check the eighth digit using the Austrian ATU procedure."""
        normalized = re.sub(r"[ \t.\-]", "", pattern_text).upper()
        if not re.fullmatch(r"ATU\d{8}", normalized):
            return False

        digits = [int(character) for character in normalized[3:]]
        digit_sum = 0
        for index, digit in enumerate(digits[:7]):
            product = digit * (1 if index % 2 == 0 else 2)
            digit_sum += product // 10 + product % 10

        expected_check_digit = (10 - ((digit_sum + 4) % 10)) % 10
        return digits[-1] == expected_check_digit


#: "2026-03-27 20" matches the grouped SVNR pattern; about every tenth
#: such timestamp passes the check digit test—a date, not an SVNR.
_ISO_DATE_PREFIX = re.compile(r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)")


class AtSocialSecurityRecognizer(PatternRecognizer):
    """Detects the ten-digit Austrian social security number."""

    COUNTRY_CODE = "at"
    PATTERNS: ClassVar[list[Pattern]] = [
        Pattern(
            "Oesterreichische SVNR (kompakt)",
            rf"{_LEFT_WORD_BOUNDARY}[1-9]\d{{9}}{_RIGHT_WORD_BOUNDARY}",
            0.5,
        ),
        Pattern(
            "Oesterreichische SVNR (gruppiert)",
            (
                rf"{_LEFT_WORD_BOUNDARY}[1-9]\d{{2}}{_SEPARATOR}\d"
                rf"[ \t.\-]\d{{2}}{_SEPARATOR}\d{{2}}"
                rf"{_SEPARATOR}\d{{2}}{_RIGHT_WORD_BOUNDARY}"
            ),
            0.45,
        ),
    ]
    CONTEXT: ClassVar[list[str]] = [
        "svnr",
        "vsnr",
        "sozialversicherungsnummer",
        "versicherungsnummer",
        "e-card",
        "oegk",
        "sozialversicherung",
    ]
    _WEIGHTS: ClassVar[tuple[int, ...]] = (3, 7, 9, 5, 8, 4, 2, 1, 6)

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entity="AT_SVNR",
            supported_language=supported_language,
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:
        """Check position four using the official Modulo-11 procedure."""
        if _ISO_DATE_PREFIX.match(pattern_text):
            return False
        normalized = re.sub(r"[ \t.\-]", "", pattern_text)
        if not re.fullmatch(r"[1-9]\d{9}", normalized):
            return False

        digits = [int(character) for character in normalized]
        # The last six digits may contain a fabricated birth date according
        # to the umbrella association. A calendar check would therefore be a
        # dangerous false-negative filter.
        weighted_digits = digits[:3] + digits[4:]
        remainder = (
            sum(
                digit * weight for digit, weight in zip(weighted_digits, self._WEIGHTS, strict=True)
            )
            % 11
        )
        return remainder != 10 and digits[3] == remainder


class AtCompanyRegisterRecognizer(PatternRecognizer):
    """Detects canonical Austrian company register numbers with ``FN``."""

    COUNTRY_CODE = "at"
    _CHECK_LETTERS: ClassVar[str] = "abdfghikmpstvwxyz"
    PATTERNS: ClassVar[list[Pattern]] = [
        Pattern(
            "Oesterreichische Firmenbuchnummer",
            (
                rf"{_LEFT_WORD_BOUNDARY}FN[ \t.\-]?"
                rf"\d{{1,6}}[ \t.\-]?[a-z]{_RIGHT_WORD_BOUNDARY}"
            ),
            0.65,
        )
    ]
    CONTEXT: ClassVar[list[str]] = [
        "firmenbuch",
        "firmenbuchnummer",
        "firmenbuchgericht",
        "fn",
    ]

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entity="AT_FIRMENBUCH",
            supported_language=supported_language,
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:
        """Checks the check letter via ``company register number mod 17``."""

        normalized = re.sub(r"[ \t.\-]", "", pattern_text).casefold()
        match = re.fullmatch(r"fn(\d{1,6})([a-z])", normalized)
        if match is None:
            return False
        number, check_letter = match.groups()
        numeric_value = int(number)
        return numeric_value > 0 and check_letter == self._CHECK_LETTERS[numeric_value % 17]


class BicRecognizer(PatternRecognizer):
    """Detects 8- and 11-digit BIC/SWIFT codes."""

    _COMMON_WORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "ANGEBOTE",
            "BESTELLT",
            "CONTAINER",
            "LIEFERUNG",
            "RECHNUNG",
            "SENDUNGEN",
        }
    )
    PATTERNS: ClassVar[list[Pattern]] = [
        Pattern(
            "BIC oder SWIFT-Code",
            (
                rf"{_LEFT_WORD_BOUNDARY}[A-Z0-9]{{4}}[A-Z]{{2}}"
                rf"[A-Z0-9]{{2}}(?:[A-Z0-9]{{3}})?{_RIGHT_WORD_BOUNDARY}"
            ),
            0.55,
        )
    ]
    CONTEXT: ClassVar[list[str]] = [
        "bic",
        "swift",
        "swift-code",
    ]

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entity="BIC",
            supported_language=supported_language,
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        """Discard codes whose country position is not a known region code."""
        country_code = pattern_text[4:6].upper()
        return None if country_code in _ISO_COUNTRY_CODES else False

    def invalidate_result(self, pattern_text: str) -> bool:
        """Suppress common eight-character business words."""
        return pattern_text.upper() in self._COMMON_WORDS

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        """Report codes only after a BIC/SWIFT field label.

        ISO 9362 has no check digit. Without the continuously updated
        SWIFT reference directory, an eight-character word alone is
        not a reliable BIC proof.
        """

        results = super().analyze(
            text,
            entities,
            nlp_artifacts=nlp_artifacts,
            regex_flags=regex_flags,
        )
        label = re.compile(
            r"(?:\bBIC(?:[ -]?CODE)?|\bSWIFT(?:[ -]?CODE)?)"
            r"\s*(?:[:#=]\s*|\s+)$",
            re.IGNORECASE,
        )
        return [
            result
            for result in results
            if label.search(text[max(0, result.start - 32) : result.start])
        ]


@dataclass(frozen=True, slots=True)
class _CapturePattern:
    name: str
    regex: re.Pattern[str]
    score: float


class _CaptureGroupRecognizer(LocalRecognizer):
    """Recognizer base whose regex only reports the named group ``value``."""

    CAPTURE_PATTERNS: ClassVar[tuple[_CapturePattern, ...]]

    def load(self) -> None:
        """No external resources are loaded."""

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
    ) -> list[RecognizerResult]:
        del nlp_artifacts
        if not set(entities).intersection(self.supported_entities):
            return []

        results: list[RecognizerResult] = []
        for pattern in self.CAPTURE_PATTERNS:
            for match in pattern.regex.finditer(text):
                start, end = match.span("value")
                explanation = AnalysisExplanation(
                    recognizer=self.name,
                    original_score=pattern.score,
                    pattern_name=pattern.name,
                    pattern=pattern.regex.pattern,
                    textual_explanation=(
                        f"Detected by `{self.name}` using capture pattern `{pattern.name}`"
                    ),
                )
                results.append(
                    RecognizerResult(
                        entity_type=self.supported_entities[0],
                        start=start,
                        end=end,
                        score=pattern.score,
                        analysis_explanation=explanation,
                        recognition_metadata={
                            RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                            RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                        },
                    )
                )

        return EntityRecognizer.remove_duplicates(results)


_CUSTOMER_LABELS: tuple[str, ...] = (
    r"kunden[ _-]?(?:nummer|nr\.?)",
    r"debitoren[ _-]?(?:nummer|nr\.?)",
    r"customer[ _-]?(?:number|no\.?|id)",
    r"client[ _-]?id",
)
_CUSTOMER_VALUE = (
    r"(?P<value>(?=[A-Z0-9._/-]{3,32}(?![A-Z0-9_/-]))"
    r"(?=[A-Z0-9._/-]*\d)[A-Z0-9][A-Z0-9._/-]{1,30}[A-Z0-9])"
)


def _customer_pattern(labels: Iterable[str]) -> _CapturePattern:
    label = "(?:" + "|".join(dict.fromkeys(labels)) + ")"
    return _CapturePattern(
        "Kundennummer nach Feldbezeichner",
        re.compile(
            rf"(?<!\w){label}\s*"
            rf"(?:(?:lautet|ist|is)\s+|[:#=]\s*|-\s+|\s+){_CUSTOMER_VALUE}"
            rf"(?![\w/-])",
            re.IGNORECASE,
        ),
        0.7,
    )


class CustomerNumberRecognizer(_CaptureGroupRecognizer):
    """Detects context-bound customer and debtor numbers.

    ``labels`` are the field identifiers of the enabled language packs;
    if unspecified, the built-in German and English ones apply.
    """

    CAPTURE_PATTERNS = (_customer_pattern(_CUSTOMER_LABELS),)

    def __init__(
        self,
        supported_language: str = "de",
        *,
        labels: Iterable[str] | None = None,
    ) -> None:
        if labels is not None:
            self.CAPTURE_PATTERNS = (_customer_pattern(labels),)
        super().__init__(
            supported_entities=["CUSTOMER_NUMBER"],
            supported_language=supported_language,
            context=["kundennummer", "kunden-nr", "debitor", "customer id"],
        )


class SecretRecognizer(_CaptureGroupRecognizer):
    """Erkennt gaengige API-Tokens sowie kontextgebundene Zugangsdaten."""

    CAPTURE_PATTERNS = (
        _CapturePattern(
            "AWS Access Key",
            re.compile(
                r"(?<!\w)"
                r"(?P<value>(?:AKIA|ASIA)[A-Z0-9]{16})"
                r"(?!\w)"
            ),
            0.95,
        ),
        _CapturePattern(
            "GitHub Token",
            re.compile(
                r"(?<!\w)(?P<value>(?:gh[pousr]_[A-Za-z0-9]{36,255}|"
                r"github_pat_[A-Za-z0-9_]{22,255}))(?!\w)"
            ),
            0.95,
        ),
        _CapturePattern(
            "API-Key mit sk-Praefix",
            re.compile(
                r"(?<![\w-])(?P<value>sk-[A-Za-z0-9_-]{20,255})"
                r"(?![\w-])"
            ),
            0.9,
        ),
        _CapturePattern(
            "JSON Web Token",
            re.compile(
                r"(?<![\w-])(?P<value>eyJ[A-Za-z0-9_-]{8,}\."
                r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})(?![\w-])"
            ),
            0.9,
        ),
        _CapturePattern(
            "Zugangsdaten in Anfuehrungszeichen",
            re.compile(
                r"(?<!\w)(?:api[ _-]?(?:key|token)|access[ _-]?token|"
                r"client[ _-]?secret|password|passwort|secret)\s*(?:=|:)\s*"
                r"[\"'](?P<value>[^\"'\r\n]{8,})[\"']",
                re.IGNORECASE,
            ),
            0.8,
        ),
        _CapturePattern(
            "Zugangsdaten als Feldwert",
            re.compile(
                r"(?<!\w)(?:api[ _-]?(?:key|token)|access[ _-]?token|"
                r"client[ _-]?secret|password|passwort|secret)\s*(?:=|:)\s*"
                r"(?P<value>[^\s,;'\"<>]{8,})(?!\S)",
                re.IGNORECASE,
            ),
            0.75,
        ),
    )

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entities=["SECRET"],
            supported_language=supported_language,
            context=["api-key", "token", "password", "passwort", "secret"],
        )


class ApiKeyRecognizer(_CaptureGroupRecognizer):
    """Kennzeichnet technische API-Schluessel getrennt von Passwoertern."""

    CAPTURE_PATTERNS = SecretRecognizer.CAPTURE_PATTERNS[:4]

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entities=["API_KEY"],
            supported_language=supported_language,
            context=["api-key", "access key", "token"],
        )


_CASE_LABELS: tuple[str, ...] = (
    r"aktenzeichen",
    r"aktenz\.?",
    r"az\.?",
    r"geschaeftszahl",
    r"geschäftszahl",
    r"gz",
    r"case[ _-]?(?:number|no\.?)",
)


def _case_pattern(labels: Iterable[str]) -> _CapturePattern:
    label = "(?:" + "|".join(dict.fromkeys(labels)) + ")"
    return _CapturePattern(
        "Aktenzeichen nach Feldbezeichner",
        re.compile(
            rf"(?<!\w){label}\s*"
            r"(?:[:#=]\s*|-\s+|\s+)"
            r"(?P<value>(?=[A-Z0-9./_-]{3,40}(?![A-Z0-9/_-]))"
            r"(?=[A-Z0-9./_-]*\d)[A-Z0-9][A-Z0-9./_-]{1,38}[A-Z0-9])"
            r"(?![\w/-])",
            re.IGNORECASE,
        ),
        0.75,
    )


class CaseNumberRecognizer(_CaptureGroupRecognizer):
    """Context-bound case/business numbers; identifiers per language pack."""

    CAPTURE_PATTERNS = (_case_pattern(_CASE_LABELS),)

    def __init__(
        self,
        supported_language: str = "de",
        *,
        labels: Iterable[str] | None = None,
    ) -> None:
        if labels is not None:
            self.CAPTURE_PATTERNS = (_case_pattern(labels),)
        super().__init__(
            supported_entities=["CASE_NUMBER"],
            supported_language=supported_language,
            context=["aktenzeichen", "az", "geschäftszahl", "gz"],
        )


_DAY = r"(?P<day>[0-3]?\d)"
_YEAR = r"(?P<year>\d{4})"


class MonthNameDateRecognizer(LocalRecognizer):
    """Dates with month names from the enabled language packs.

    Presidio's ``DateRecognizer`` only knows digit formats. „3. März 2024",
    „3. Jänner", „March 3, 2024" and „3rd March 2024" would otherwise
    remain unrecognized. Month names are case-sensitive to prevent "Section 3
    may …" from becoming a date; the day is validated as 1–31.
    """

    def __init__(
        self,
        months: Mapping[str, int],
        supported_language: str = "de",
    ) -> None:
        self._months = {name.casefold(): month for name, month in months.items()}
        if not self._months:
            raise ValueError("months darf nicht leer sein.")
        names = sorted(
            {name[:1].upper() + name[1:] for name in self._months},
            key=len,
            reverse=True,
        )
        month = "(?P<month>" + "|".join(re.escape(name) for name in names) + ")"
        self._patterns = (
            re.compile(
                rf"(?<!\w){_DAY}(?:\.|st|nd|rd|th)?\s+(?:of\s+)?{month}\.?"
                rf"(?:,?\s+{_YEAR})?(?![\w])"
            ),
            re.compile(rf"(?<!\w){month}\.?\s+{_DAY}(?:st|nd|rd|th)?,?\s+{_YEAR}(?![\w])"),
        )
        super().__init__(
            supported_entities=["DATE_TIME"],
            supported_language=supported_language,
            name="MonthNameDateRecognizer",
        )

    def load(self) -> None:
        """No external resources are loaded."""

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
    ) -> list[RecognizerResult]:
        del nlp_artifacts
        if "DATE_TIME" not in entities:
            return []
        results: list[RecognizerResult] = []
        for pattern in self._patterns:
            for match in pattern.finditer(text):
                day = int(match.group("day"))
                if not 1 <= day <= 31 or match.group("month").casefold() not in self._months:
                    continue
                start, end = match.span()
                results.append(
                    RecognizerResult(
                        entity_type="DATE_TIME",
                        start=start,
                        end=end,
                        score=0.6,
                        recognition_metadata={
                            RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                            RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                        },
                    )
                )
        return EntityRecognizer.remove_duplicates(results)


_UPPER = "A-ZÄÖÜ"
_LOWER = "a-zäöüß"
_STREET_NAME_WORD = rf"[{_UPPER}][{_LOWER}]+"
_STREET_SUFFIX = "|".join(
    sorted((re.escape(suffix) for suffix in GERMAN_STREET_SUFFIXES), key=len, reverse=True)
)
_STREET_WORD = (
    rf"(?=[{_UPPER}])(?:{_STREET_NAME_WORD}-)*[{_UPPER}{_LOWER}]*?"
    rf"(?i:{_STREET_SUFFIX})(?![{_LOWER}])\.?"
)
#: "Obere Musterstraße", "Wiedner Hauptstraße", "St. Pöltner Straße": a
#: preceding adjective belongs to the street name, an article does not
#: ("Die Straße 5 ist gesperrt").
_STREET_ATTRIBUTE = (
    r"(?:(?:St\.|Sankt)[ \t]+)?"
    r"(?!(?:Die|Der|Das|Den|Dem|Des|Eine|Einer|Einen|Diese|Dieser|Diesen|Jede|Jeder"
    r"|Ihre|Ihrer|Unsere|Unserer|Seine|Meine|Keine)[ \t])"
    rf"[{_UPPER}][{_LOWER}]*(?:e|er|es|en)"
)
#: "At Quarry 5", "Under Linden 3" – no suffix, so only postal code is used.
_STREET_PREPOSITION = (
    r"(?:Am|Im|Zum|Zur|An[ \t]de[mnr]|Auf[ \t]de[mnr]|In[ \t]de[mnr]|Unter[ \t]de[mnr]"
    r"|Hinter[ \t]de[mnr]|Bei[ \t]de[mnr])"
)
#: House numbers with stair/top: "12", "12a", "49-51",
#: "12/1/30", "5/Top 3", "7, Stair 2".
_HOUSE_NUMBER = (
    r"\d{1,4}(?:[ \t]?[a-zA-Z](?!\w))?"
    r"(?:[ \t]?[-–][ \t]?\d{1,4}[a-zA-Z]?)?"
    r"(?:[ \t]?/[ \t]?(?:\d{1,4}[a-zA-Z]?|Top[ \t]?\d{1,4}|Stg\.?[ \t]?\d{1,3}))*"
    r"(?:,?[ \t]+(?:Stiege|Stg\.|Top|Tür)[ \t]?\d{1,4})*"
)
#: A number with a unit is a measurement ("Rückweg 3 km"), not a house number.
_MEASUREMENT = r"(?![ \t]?(?:km|m|cm|mm|min|h|kg|t|%|€|EUR|Stück|Stk)(?!\w))"
_POSTAL_CODE = r"(?:(?:A|D|AT|DE)-[ \t]?)?\d{4,5}"
#: A period belongs only to abbreviations ("St. Pölten"), not as a sentence end to the place.
_CITY_WORD = rf"(?:[{_UPPER}][{_LOWER}]{{1,2}}\.|[{_UPPER}][{_LOWER}]+|[{_UPPER}]{{2,}})"
_CITY = (
    rf"{_CITY_WORD}(?:(?:[ \t]+(?:am|an[ \t]der|im|in|ob[ \t]der|bei))?[ \t-]+{_CITY_WORD}){{0,2}}"
)
_BARE_STREET_SUFFIXES = frozenset(GERMAN_STREET_SUFFIXES)
_STREET_ADDRESS = re.compile(
    rf"(?<![\w-])"
    rf"(?:(?P<plz_before>{_POSTAL_CODE})[ \t]+{_CITY}[ \t]*,[ \t]*)?"
    rf"(?:(?P<street>(?P<attribute>{_STREET_ATTRIBUTE}[ \t]+)?(?P<street_word>{_STREET_WORD}))"
    rf"|(?P<preposition>{_STREET_PREPOSITION}[ \t]+{_STREET_NAME_WORD}))"
    rf"[ \t]+{_HOUSE_NUMBER}(?![\w/]){_MEASUREMENT}"
    # "Ring Road 30, Postbox 80, 1010 Vienna": the mailbox belongs to the address.
    rf"(?:(?:[ \t]*,[ \t]*Postfach[ \t]+\d{{1,6}})?"
    rf"(?:[ \t]*,[ \t]*|[ \t]+|[ \t]*\r?\n[ \t]*)"
    rf"(?P<plz_after>(?<![\w-]){_POSTAL_CODE})[ \t]+{_CITY}(?!\w))?"
)


class StreetAddressRecognizer(LocalRecognizer):
    """German and Austrian addresses: street, house number, PLZ, place.

    GLiNER only asks for persons, companies and places and reports from
    "Musterbergstraße 12/1/30, 1110 Wien" only the place – the street
    would remain (reasoning at ``DEFAULT_GLINER_LABELS``). This pattern
    also works in the spaCy fallback without GLiNER. It follows the word
    formation of German street names (suffix like ``-straße``, ``-gasse``, ``-platz``)
    and requires a house number. With PLZ and place before or after, the
    hit is unambiguous; without them it remains a question until a field
    identifier ("Anschrift:") resolves it. Streets without suffix ("Am Graben 5")
    only count with PLZ – "Im Jahr 2024" is not an address.
    """

    ENTITY = "STREET_ADDRESS"
    WITH_POSTAL_CODE = 0.95
    STREET_ONLY = 0.85

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entities=[self.ENTITY],
            supported_language=supported_language,
            name="StreetAddressRecognizer",
            context=["adresse", "anschrift", "wohnhaft", "wohnort", "wohnsitz", "address"],
        )

    def load(self) -> None:
        """No external resources are loaded."""

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: object | None = None,
    ) -> list[RecognizerResult]:
        del nlp_artifacts
        if self.ENTITY not in entities:
            return []
        results: list[RecognizerResult] = []
        for match in _STREET_ADDRESS.finditer(text):
            postal = match.group("plz_before") or match.group("plz_after")
            if match.group("preposition") and not postal:
                continue
            # "Straße 5" alone is not a street; the name requires a base name
            # ("Hauptstraße") or an attribute ("Wiener Straße").
            street_word = (match.group("street_word") or "").rstrip(".").casefold()
            if street_word in _BARE_STREET_SUFFIXES and not match.group("attribute"):
                continue
            start, end = match.span()
            results.append(
                RecognizerResult(
                    entity_type=self.ENTITY,
                    start=start,
                    end=end,
                    score=self.WITH_POSTAL_CODE if postal else self.STREET_ONLY,
                    recognition_metadata={
                        RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                        RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                    },
                )
            )
        return EntityRecognizer.remove_duplicates(results)


#: Countries with their own recognizers for numbers and IDs.
SUPPORTED_COUNTRIES: tuple[str, ...] = ("DE", "AT")


def create_deterministic_recognizers(
    *,
    enable_de_plz: bool = False,
    phone_regions: Sequence[str] | None = None,
    countries: Sequence[str] = SUPPORTED_COUNTRIES,
    languages: Sequence[str] | None = None,
) -> tuple[EntityRecognizer, ...]:
    """Create the model-free recognizers.

    ``enable_de_plz`` may only be set for profiles or fields where an address context is
    fixed. Otherwise, the recognizer is completely excluded from the registry.

    ``countries`` selects national numbers and IDs; without ``phone_regions``, the same
    countries also apply to phone numbers. ``languages`` selects language packs for
    month names and field labels (``None``: all).
    """
    from .languages import packs

    normalized_countries = tuple(dict.fromkeys(country.strip().upper() for country in countries))
    unknown = set(normalized_countries) - set(SUPPORTED_COUNTRIES)
    if not normalized_countries or unknown:
        raise ValueError("countries muss Laender aus DE, AT nennen.")
    normalized_phone_regions = tuple(
        dict.fromkeys(
            region.strip().upper()
            for region in (phone_regions if phone_regions is not None else normalized_countries)
        )
    )
    if not normalized_phone_regions:
        raise ValueError("phone_regions darf nicht leer sein.")
    language_packs = packs(languages)
    months: dict[str, int] = {}
    for language_pack in language_packs:
        months.update(language_pack.months)
    germany = "DE" in normalized_countries
    austria = "AT" in normalized_countries
    recognizers: list[EntityRecognizer] = [
        IbanRecognizer(supported_language="de"),
        OfflineEmailRecognizer(supported_language="de", name="EmailRecognizer"),
        RegionalPhoneRecognizer(
            supported_language="de",
            supported_regions=normalized_phone_regions,
            name="PhoneRecognizer",
            context=[
                "telefon",
                "telefonnummer",
                "mobil",
                "mobilnummer",
                "handy",
                "rufnummer",
                "tel",
                "fax",
                "telefax",
                "durchwahl",
                "festnetz",
                "hotline",
                "phone",
                "mobile",
                "cell",
            ],
        ),
        IdentifyingIpRecognizer(supported_language="de"),
        BoundedUrlRecognizer(supported_language="de"),
        CreditCardRecognizer(supported_language="de"),
        CryptoRecognizer(supported_language="de"),
        DateRecognizer(
            supported_language="de",
            context=[
                "datum",
                "geburtsdatum",
                "lieferdatum",
                "rechnungsdatum",
                "termin",
                "zeitpunkt",
            ],
        ),
        MonthNameDateRecognizer(months),
        BicRecognizer(),
        CaseNumberRecognizer(
            labels=[label for item in language_packs for label in item.case_labels]
        ),
        CustomerNumberRecognizer(
            labels=[label for item in language_packs for label in item.customer_labels]
        ),
        ApiKeyRecognizer(),
        SecretRecognizer(),
        # Streets are formed identically in DE and AT; the pattern applies to both.
        StreetAddressRecognizer(),
    ]
    if germany:
        recognizers.extend(
            (
                DeTaxIdRecognizer(),
                DeTaxNumberRecognizer(),
                DePassportRecognizer(),
                DeIdCardRecognizer(),
                DeSocialSecurityRecognizer(),
                DeHealthInsuranceRecognizer(),
                DeKfzRecognizer(),
                DeHandelsregisterRecognizer(),
                # Present in Presidio 2.2.364; a local duplicate would be riskier.
                DeVatIdRecognizer(),
            )
        )
    if austria:
        recognizers.extend(
            (
                AtUidRecognizer(),
                AtSocialSecurityRecognizer(),
                AtCompanyRegisterRecognizer(),
            )
        )
    if enable_de_plz and germany:
        recognizers.append(ContextualDePlzRecognizer())
    return tuple(recognizers)


def register_deterministic_recognizers(
    registry: RecognizerRegistry,
    *,
    enable_de_plz: bool = False,
    replace_existing: bool = True,
) -> tuple[EntityRecognizer, ...]:
    """Register the recognizer idempotently in a Presidio registry."""
    recognizers = create_deterministic_recognizers(enable_de_plz=enable_de_plz)
    if replace_existing and not enable_de_plz:
        registry.remove_recognizer(DePlzRecognizer.__name__, "de")
        registry.remove_recognizer(ContextualDePlzRecognizer.__name__, "de")
    for recognizer in recognizers:
        if replace_existing:
            registry.remove_recognizer(recognizer.name, recognizer.supported_language)
        registry.add_recognizer(recognizer)
    return recognizers
