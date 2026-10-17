"""Simulated external services (registries, demand, receiving). Every response says so."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import VLModel
from vl_core.schemas.jobs import DemandLine


class RegistryCheckRequest(VLModel):
    doc_type: str = Field(description="DocSpec id, e.g. aadhaar, fssai, pirt")
    number: str
    name: str = Field(description="Name the vendor gave, to match against the registry")


class RegistryCheckResult(VLModel):
    doc_type: str
    status: Literal["pass", "fail"]
    code: str = Field(description="OK, NOT_FOUND, NAME_MISMATCH, EXPIRED, ...")
    detail: str = ""
    registered_name: str | None = None
    valid_until: date | None = None
    simulated: Literal[True] = True


class DemandResponse(VLModel):
    store_id: str
    for_date: date
    lines: list[DemandLine]
    simulated: Literal[True] = True
