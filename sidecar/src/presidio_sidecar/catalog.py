"""Category catalog: the single source of truth for categories, styles, and
placeholders.

The UI no longer knows about protection levels or entity types, but only about
categories ("Namen", "Anschriften" ...). Which type belongs to which category is defined
exclusively here; ``GET /catalog`` serves this data, and the frontend renders the chips
from this response.

Every type from ``processing.ENTITY_MINIMUM_LEVEL`` belongs to exactly one category, and
the category carries the same level as its types. A contract test keeps both tables in
sync: a new type without a category would be invisible in the UI and pass silently in
plain text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

Language = Literal["de", "en"]
LANGUAGES: Final[tuple[Language, ...]] = ("de", "en")

Method = Literal["reversible", "irreversible"]
Style = Literal["codes", "surrogates", "placeholder", "mask", "remove"]


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    labels: Mapping[str, str]
    entity_types: tuple[str, ...]
    #: Protection level from which the category originates — for audit and
    #: review report only; the UI does not display it.
    level: int
    #: Included in the default selection (current level 2).
    default: bool
    #: Always present and cannot be disabled; only "Keep only here" per item.
    locked: bool
    #: Circumstances: indirect attributes that reveal a person via context.
    circumstances: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "labels": dict(self.labels),
            "entity_types": list(self.entity_types),
            "level": self.level,
            "default": self.default,
            "locked": self.locked,
            "circumstances": self.circumstances,
        }


def _labels(de: str, en: str) -> Mapping[str, str]:
    return MappingProxyType({"de": de, "en": en})


CATEGORIES: Final[tuple[Category, ...]] = (
    Category(
        id="finance_ids",
        labels=_labels("Konto-, Ausweis- & Zugangsdaten", "Account, ID & access data"),
        entity_types=(
            "IBAN_CODE",
            "BIC",
            "CREDIT_CARD",
            "CRYPTO",
            "DE_TAX_ID",
            "DE_TAX_NUMBER",
            "DE_VAT_ID",
            "AT_UID",
            "DE_SOCIAL_SECURITY",
            "AT_SVNR",
            "DE_PASSPORT",
            "DE_ID_CARD",
            "DE_HEALTH_INSURANCE",
            "SECRET",
            "API_KEY",
            "ACCESS_TOKEN",
            "PASSWORD",
        ),
        level=1,
        default=True,
        locked=True,
        circumstances=False,
    ),
    Category(
        id="names",
        labels=_labels("Namen", "Names"),
        entity_types=("PERSON",),
        level=2,
        default=True,
        locked=False,
        circumstances=False,
    ),
    Category(
        id="addresses",
        labels=_labels("Anschriften", "Addresses"),
        entity_types=("ADDRESS", "STREET_ADDRESS"),
        level=2,
        default=True,
        locked=False,
        circumstances=False,
    ),
    Category(
        id="contact",
        labels=_labels("Telefon & E-Mail", "Phone & email"),
        entity_types=("PHONE_NUMBER", "EMAIL_ADDRESS", "IP_ADDRESS"),
        level=2,
        default=True,
        locked=False,
        circumstances=False,
    ),
    Category(
        id="numbers",
        labels=_labels("Kunden- & Aktennummern", "Customer & file numbers"),
        entity_types=("CUSTOMER_NUMBER", "CASE_NUMBER", "DE_KFZ"),
        level=2,
        default=True,
        locked=False,
        circumstances=False,
    ),
    # Web addresses are usually links to company and documentation pages; only
    # some point to a person (profile, private domain). Every one is detected,
    # but only those matching the selected category are masked.
    Category(
        id="web",
        labels=_labels("Webadressen", "Web addresses"),
        entity_types=("URL",),
        level=3,
        default=False,
        locked=False,
        circumstances=False,
    ),
    Category(
        id="organizations",
        labels=_labels("Firmen", "Companies"),
        entity_types=("ORGANIZATION", "DE_HANDELSREGISTER", "AT_FIRMENBUCH"),
        level=3,
        default=False,
        locked=False,
        circumstances=True,
    ),
    Category(
        id="locations",
        labels=_labels("Orte", "Places"),
        entity_types=("LOCATION", "DE_PLZ"),
        level=3,
        default=False,
        locked=False,
        circumstances=True,
    ),
    Category(
        id="dates",
        labels=_labels("Datumsangaben", "Dates"),
        entity_types=("DATE_TIME",),
        level=3,
        default=False,
        locked=False,
        circumstances=True,
    ),
    Category(
        id="other_indirect",
        labels=_labels("Weitere Merkmale", "Other characteristics"),
        entity_types=("NRP", "JOB_TITLE", "PROFESSION", "OTHER"),
        level=3,
        default=False,
        locked=False,
        circumstances=True,
    ),
)

CATEGORY_IDS: Final[tuple[str, ...]] = tuple(category.id for category in CATEGORIES)
CATEGORIES_BY_ID: Final[Mapping[str, Category]] = MappingProxyType(
    {category.id: category for category in CATEGORIES}
)
CATEGORY_BY_ENTITY: Final[Mapping[str, Category]] = MappingProxyType(
    {entity: category for category in CATEGORIES for entity in category.entity_types}
)

STYLES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "reversible": ("codes", "surrogates"),
        "irreversible": ("placeholder", "mask", "remove"),
    }
)

#: Style determines the processing mode. ``encrypt`` and ``hash`` remain in
#: the sidecar; the UI no longer offers them.
STYLE_MODES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "codes": "token_deterministic",
        "surrogates": "surrogate",
        "placeholder": "placeholder",
        "mask": "mask",
        "remove": "remove",
    }
)

#: Placeholder text per type and language. The processor adds the square
#: brackets; here only the word is stored.
PLACEHOLDERS: Final[Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {
        "PERSON": _labels("Name", "Name"),
        "ADDRESS": _labels("Anschrift", "Address"),
        "STREET_ADDRESS": _labels("Anschrift", "Address"),
        "PHONE_NUMBER": _labels("Telefon", "Phone"),
        "EMAIL_ADDRESS": _labels("E-Mail", "Email"),
        "IP_ADDRESS": _labels("IP-Adresse", "IP address"),
        "URL": _labels("URL", "URL"),
        "CUSTOMER_NUMBER": _labels("Kundennummer", "Customer number"),
        "CASE_NUMBER": _labels("Aktenzeichen", "File number"),
        "DE_KFZ": _labels("Kennzeichen", "Licence plate"),
        "IBAN_CODE": _labels("IBAN", "IBAN"),
        "BIC": _labels("BIC", "BIC"),
        "CREDIT_CARD": _labels("Kreditkarte", "Credit card"),
        "CRYPTO": _labels("Wallet", "Wallet"),
        "DE_TAX_ID": _labels("Steuer-ID", "Tax ID"),
        "DE_TAX_NUMBER": _labels("Steuernummer", "Tax number"),
        "DE_VAT_ID": _labels("USt-IdNr.", "VAT ID"),
        "AT_UID": _labels("UID", "VAT ID"),
        "DE_SOCIAL_SECURITY": _labels("Sozialversicherungsnummer", "Social security number"),
        "AT_SVNR": _labels("Sozialversicherungsnummer", "Social security number"),
        "DE_PASSPORT": _labels("Reisepassnummer", "Passport number"),
        "DE_ID_CARD": _labels("Ausweisnummer", "ID card number"),
        "DE_HEALTH_INSURANCE": _labels("Versichertennummer", "Health insurance number"),
        "SECRET": _labels("Zugangsdaten", "Credentials"),
        "API_KEY": _labels("Zugangsdaten", "Credentials"),
        "ACCESS_TOKEN": _labels("Zugangsdaten", "Credentials"),
        "PASSWORD": _labels("Zugangsdaten", "Credentials"),
        "ORGANIZATION": _labels("Firma", "Company"),
        "DE_HANDELSREGISTER": _labels("Registernummer", "Register number"),
        "AT_FIRMENBUCH": _labels("Registernummer", "Register number"),
        "LOCATION": _labels("Ort", "Place"),
        "DE_PLZ": _labels("PLZ", "Postcode"),
        "DATE_TIME": _labels("Datum", "Date"),
        "NRP": _labels("Herkunft", "Origin"),
        "JOB_TITLE": _labels("Beruf", "Occupation"),
        "PROFESSION": _labels("Beruf", "Occupation"),
        "OTHER": _labels("Angabe", "Detail"),
    }
)


class UnknownCategoryError(ValueError):
    """A category ID or type is unknown in the catalog."""


def category_for_entity(entity_type: str) -> Category:
    """Category of a type; unknown types fail instead of guessing."""

    try:
        return CATEGORY_BY_ENTITY[entity_type]
    except KeyError as error:
        raise UnknownCategoryError(f"Typ {entity_type!r} hat keine Kategorie.") from error


def placeholder_for(entity_type: str, language: str = "de") -> str:
    """Placeholder ``[Word]`` in the desired language, fallback German."""

    labels = PLACEHOLDERS.get(entity_type)
    if labels is None:
        word = entity_type.replace("_", " ").title()
    else:
        word = labels.get(language) or labels["de"]
    return f"[{word}]"


def default_category_ids() -> tuple[str, ...]:
    return tuple(category.id for category in CATEGORIES if category.default or category.locked)


def locked_category_ids() -> tuple[str, ...]:
    return tuple(category.id for category in CATEGORIES if category.locked)


def catalog_payload() -> dict[str, object]:
    """Response from ``GET /catalog``."""

    return {
        "categories": [category.as_dict() for category in CATEGORIES],
        "styles": {method: list(styles) for method, styles in STYLES.items()},
        "style_modes": dict(STYLE_MODES),
        "placeholders": {
            entity: {language: f"[{word}]" for language, word in labels.items()}
            for entity, labels in PLACEHOLDERS.items()
        },
        "defaults": {
            "method": "reversible",
            "style": STYLES["reversible"][0],
            "categories": list(default_category_ids()),
        },
        "languages": list(LANGUAGES),
    }


__all__ = [
    "CATEGORIES",
    "CATEGORIES_BY_ID",
    "CATEGORY_BY_ENTITY",
    "CATEGORY_IDS",
    "LANGUAGES",
    "PLACEHOLDERS",
    "STYLES",
    "STYLE_MODES",
    "Category",
    "Language",
    "Method",
    "Style",
    "UnknownCategoryError",
    "catalog_payload",
    "category_for_entity",
    "default_category_ids",
    "locked_category_ids",
    "placeholder_for",
]
