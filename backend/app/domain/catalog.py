"""Shared business vocabularies (spend taxonomy, vendor statuses)."""

from enum import StrEnum


class SpendCategory(StrEnum):
    """Top-level spend taxonomy. Also the label set for the spend-classification model (P9)."""

    IT_SERVICES = "it_services"
    TRAVEL = "travel"
    FACILITIES = "facilities"
    MARKETING = "marketing"
    PROFESSIONAL_SERVICES = "professional_services"
    HARDWARE = "hardware"
    SOFTWARE = "software"
    LOGISTICS = "logistics"
    OFFICE_SUPPLIES = "office_supplies"
    MANUFACTURING = "manufacturing"


class VendorStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    BLOCKED = "blocked"


class VendorRiskLevel(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ContractStatus(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    EXPIRED = "expired"
