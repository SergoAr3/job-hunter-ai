from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from typing import TypeAlias, cast

from app.services.job_normalizer import ExtractedJobData

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class JobPostingExtractor:
    def extract(self, html: str) -> ExtractedJobData:
        parser = _PageParser()
        parser.feed(html)
        records: list[JsonObject] = []
        for value in parser.json_ld:
            try:
                records.extend(_job_postings(json.loads(value)))
            except (json.JSONDecodeError, TypeError):
                continue
        data = ExtractedJobData()
        for record in records:
            data = _merge(data, _from_json_ld(record))
        fallback = ExtractedJobData(title=parser.meta.get("og:title") or parser.meta.get("title") or parser.title, description=parser.meta.get("og:description") or parser.meta.get("description"))
        return _merge(data, fallback)


def _job_postings(value: object) -> list[JsonObject]:
    if isinstance(value, list):
        return [item for child in value for item in _job_postings(child)]
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return []
    record = cast(JsonObject, value)
    graph = record.get("@graph")
    if graph:
        return _job_postings(graph)
    types = record.get("@type", [])
    if isinstance(types, str):
        type_names = [types]
    elif isinstance(types, list):
        type_names = [item for item in types if isinstance(item, str)]
    else:
        type_names = []
    return [record] if "JobPosting" in type_names else []


def _from_json_ld(item: JsonObject) -> ExtractedJobData:
    organization = item.get("hiringOrganization") or {}
    salary = item.get("baseSalary") or {}
    value = salary.get("value") if isinstance(salary, dict) else {}
    if not isinstance(value, dict): value = {"value": value}
    location = item.get("jobLocation") or {}
    if isinstance(location, list): location = location[0] if location else {}
    address = location.get("address") if isinstance(location, dict) else None
    if isinstance(address, dict): location = ", ".join(str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key))
    employment = item.get("employmentType")
    if isinstance(employment, list): employment = employment[0] if employment else None
    return ExtractedJobData(title=_string(item.get("title")), company=_string(organization.get("name") if isinstance(organization, dict) else None), description=_strip_html(_string(item.get("description"))), requirements_text=_strip_html(_string(item.get("qualifications"))), salary_min=_decimal(value.get("minValue") or value.get("value")), salary_max=_decimal(value.get("maxValue") or value.get("value")), salary_currency=_string(salary.get("currency") if isinstance(salary, dict) else None), salary_period=_string(value.get("unitText")), location=_string(location), workplace_raw=_string(item.get("jobLocationType")), employment_raw=_string(employment))


def _merge(primary: ExtractedJobData, secondary: ExtractedJobData) -> ExtractedJobData:
    return ExtractedJobData(**{field: getattr(primary, field) if getattr(primary, field) is not None else getattr(secondary, field) for field in ExtractedJobData.__dataclass_fields__})


def _string(value: object) -> str | None:
    return str(value) if value is not None else None


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def _strip_html(value: str | None) -> str | None:
    if value is None:
        return None
    return unescape(__import__("re").sub(r"<[^>]+>", " ", value))


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.json_ld: list[str] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._script = False
        self._script_data: list[str] = []
        self._title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        content_type = data.get("type")
        if tag == "script" and content_type is not None and content_type.lower() == "application/ld+json":
            self._script = True
            self._script_data = []
        if tag == "title":
            self._title = True
        if tag == "meta":
            key = data.get("property") or data.get("name")
            content = data.get("content")
            if key and content:
                self.meta[key.lower()] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script:
            self.json_ld.append("".join(self._script_data))
            self._script = False
        if tag == "title":
            self._title = False

    def handle_data(self, data: str) -> None:
        if self._script:
            self._script_data.append(data)
        if self._title:
            self.title += data
