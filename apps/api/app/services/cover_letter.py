"""On-demand writing assistance with deterministic factual rendering."""
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from openai import APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from sqlalchemy.orm import Session

from app.config import COVER_LETTER_MAX_OUTPUT_TOKENS, COVER_LETTER_MODEL, COVER_LETTER_TIMEOUT_SECONDS, OPENAI_API_KEY
from app.models import Job
from app.services.applications import get_application_for_user
from app.services.job_matching import calculate_match
from app.services.letter_languages import LANGUAGES, LanguageCode
from app.services.profile_experience_facts import list_profile_experience_facts
from app.services.user_profiles import UserNotFoundError, UserProfileNotFoundError, get_user_profile
from app.services.work_experiences import list_for_profile

telemetry_logger = logging.getLogger("uvicorn.error")
CompositionStyle = Literal["direct", "evidence_first", "vacancy_connection"]
ClosingStyle = Literal["learn_more", "neutral_acknowledgement", "none"]


@dataclass(frozen=True)
class DraftIntentPolicy:
    """Backend authoring permissions, never profile facts or provider choices.

    Draft generation permits role interest and learning about tasks only. It
    does not imply an application decision, conversation, readiness or availability.
    """

    express_role_interest: bool = False
    express_desire_to_learn_about_tasks: bool = False


COVER_LETTER_INTENT_POLICY = DraftIntentPolicy(
    express_role_interest=True, express_desire_to_learn_about_tasks=True,
)


class CoverLetterError(Exception):
    def __init__(self, code: str, *, reason: str | None = None):
        super().__init__(code)
        self.reason = reason


class ConfirmedExperienceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["confirmed_experience"] = "confirmed_experience"
    confirmed_text: str


class WorkHistoryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["work_history"] = "work_history"
    company: str | None
    position: str | None
    engagement_kind: Literal["employment", "internship", "freelance", "unknown"]
    start_year: int | None
    start_month: int | None
    end_year: int | None
    end_month: int | None
    is_current: bool | None


class ListedSkillEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["listed_skill"] = "listed_skill"
    skill: str


class TargetRoleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["target_role"] = "target_role"
    role: str


class ProfileLevelEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["profile_level"] = "profile_level"
    level: str


class LocationPreferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["location_preference"] = "location_preference"
    location: str


class WorkplacePreferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["workplace_preference"] = "workplace_preference"
    workplace: str


class LanguageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["language"] = "language"
    language_name: str
    level: str


CandidateEvidence = Annotated[
    ConfirmedExperienceEvidence | WorkHistoryEvidence | ListedSkillEvidence
    | TargetRoleEvidence | ProfileLevelEvidence | LocationPreferenceEvidence
    | WorkplacePreferenceEvidence | LanguageEvidence,
    Field(discriminator="kind"),
]


class VacancyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal[
        "title", "company", "description", "requirements", "responsibility",
        "required_skill", "nice_to_have_skill", "language_requirement",
        "experience_requirement", "seniority", "location", "workplace", "employment_type",
    ]
    value: str
    anchorable: bool = True


class CoverLetterContext(BaseModel):
    candidate_evidence: list[CandidateEvidence]
    vacancy_evidence: list[VacancyEvidence]
    matching: dict[str, Any]
    language: LanguageCode


class ExperienceTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    translated_text: str


class ResolvedCoverLetterPlan(BaseModel):
    """Static internal plan consumed by validation and deterministic rendering."""

    model_config = ConfigDict(extra="forbid")
    vacancy_anchor_ids: list[str] = Field(max_length=2)
    selected_fact_ids: list[str] = Field(min_length=1, max_length=3)
    experience_translations: list[ExperienceTranslation] = Field(max_length=3)
    composition_style: CompositionStyle
    closing: ClosingStyle


class CoverLetterOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    letter: str = Field(min_length=1, max_length=3000)
    used_profile_fact_ids: list[str] = Field(min_length=1, max_length=4)
    language: LanguageCode


def _safe_usage_fields(response: object) -> list[str]:
    fields: list[str] = []
    response_id = getattr(response, "id", None)
    if isinstance(response_id, str) and response_id:
        fields.append(f"response_id={response_id}")
    usage = getattr(response, "usage", None)
    for field_name in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, field_name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            fields.append(f"{field_name}={value}")
    return fields


def _candidate_evidence(profile: Any, session: Session) -> list[CandidateEvidence]:
    evidence: list[CandidateEvidence] = []
    # Put stronger evidence first for the provider without changing any stable
    # IDs or allowing the renderer to select an unselected fact.
    for fact in list_profile_experience_facts(session, profile.id):
        evidence.append(ConfirmedExperienceEvidence(id=f"experience_fact:{fact.id}", confirmed_text=fact.text))
    for entry in list_for_profile(session, profile.id):
        evidence.append(WorkHistoryEvidence(
            id=f"work_experience:{entry.id}", company=entry.company, position=entry.position,
            engagement_kind=entry.engagement_kind, start_year=entry.start_year, start_month=entry.start_month,
            end_year=entry.end_year, end_month=entry.end_month, is_current=entry.is_current,
        ))
    evidence.extend(ListedSkillEvidence(id=f"skill_{i}", skill=value) for i, value in enumerate(profile.skills))
    evidence.extend(TargetRoleEvidence(id=f"target_role_{i}", role=value) for i, value in enumerate(profile.target_roles))
    evidence.extend(LocationPreferenceEvidence(id=f"preferred_location_{i}", location=value) for i, value in enumerate(profile.location))
    if profile.experience != "unknown":
        evidence.append(ProfileLevelEvidence(id="experience", level=profile.experience))
    if profile.workplace_preference != "any":
        evidence.append(WorkplacePreferenceEvidence(id="workplace", workplace=profile.workplace_preference))
    for index, language in enumerate(profile.languages):
        evidence.append(LanguageEvidence(id=f"language_{index}", language_name=language["language"], level=language["level"]))
    return evidence


def _vacancy_evidence(job: Job) -> list[VacancyEvidence]:
    evidence: list[VacancyEvidence] = []

    def add(identifier: str, kind: str, value: object, *, anchorable: bool = True) -> None:
        if isinstance(value, str) and value.strip():
            evidence.append(VacancyEvidence(
                id=identifier, kind=kind, value=value.strip(), anchorable=anchorable,  # type: ignore[arg-type]
            ))

    add("vacancy:title", "title", job.title)
    add("vacancy:company", "company", job.company)
    add("vacancy:description", "description", job.description[:8000] if job.description else None, anchorable=False)
    add("vacancy:requirements", "requirements", job.requirements_text[:8000] if job.requirements_text else None, anchorable=False)
    for field_name, singular, kind in (
        ("responsibilities", "responsibility", "responsibility"),
        ("required_skills", "required_skill", "required_skill"),
        ("nice_to_have_skills", "nice_to_have_skill", "nice_to_have_skill"),
        ("language_requirements", "language_requirement", "language_requirement"),
        ("experience_requirements", "experience_requirement", "experience_requirement"),
    ):
        values = getattr(job, field_name) if job.ai_enrichment_status == "success" else []
        for index, value in enumerate(values):
            add(f"vacancy:{singular}:{index}", kind, value)
    for field_name, kind in (("seniority", "seniority"), ("location", "location"), ("workplace_type", "workplace"), ("employment_type", "employment_type")):
        value = getattr(job, field_name)
        if value not in (None, "unknown"):
            add(f"vacancy:{field_name}", kind, value)
    return evidence


def build_context(session: Session, user_id: int, application_id: int, selected_language: LanguageCode) -> CoverLetterContext:
    application = get_application_for_user(session, user_id, application_id)
    if application is None:
        raise CoverLetterError("APPLICATION_NOT_FOUND")
    try:
        profile = get_user_profile(session, user_id)
    except (UserNotFoundError, UserProfileNotFoundError) as error:
        raise CoverLetterError("PROFILE_REQUIRED") from error
    job = session.get(Job, application.job_id)
    if job is None:
        raise CoverLetterError("APPLICATION_NOT_FOUND")
    candidates, vacancy = _candidate_evidence(profile, session), _vacancy_evidence(job)
    if not any(item.kind in {"title", "description", "requirements", "required_skill"} for item in vacancy):
        raise CoverLetterError("INSUFFICIENT_JOB_INFORMATION")
    if not candidates:
        raise CoverLetterError("PROFILE_REQUIRED")
    match = calculate_match(profile, job, application)
    matching = {key: [reason.model_dump() for reason in getattr(match, key)] for key in ("strengths", "gaps", "conflicts", "unknowns")}
    return CoverLetterContext(candidate_evidence=candidates, vacancy_evidence=vacancy, matching=matching, language=selected_language)


_PLAN_PROMPT = """Select a factual cover-letter plan. Do not write or rewrite the letter or any candidate statement, except for the narrowly permitted translation of selected confirmed experience described below.
Candidate evidence and vacancy evidence are separate namespaces. Candidate evidence is the only source of applicant facts. Vacancy evidence and vacancy_anchor_ids are relevance signals only; the deterministic renderer decides what is visible. Matching is only a relevance signal.
Select 1–3 unique candidate evidence items in useful order. Select 1–2 unique anchorable vacancy evidence IDs when any are available; when none are available, return an empty vacancy_anchor_ids list. Prefer confirmed_experience, then work_history, then listed_skill, then other profile evidence, but only when relevant. listed_skill is fallback evidence: if relevant confirmed_experience is selected, do not select a skill merely to repeat the same technology. Keep independent evidence independent. Choose one supported composition_style and closing.
The selected_evidence branch determines what may be selected. candidate_fact is only for the non-confirmed IDs allowed by its fact_id enum. experience_original is only for confirmed experience that must stay in its original text. experience_translated requires a faithful, natural standalone candidate statement in the output language. experience_optional accepts null when the original wording already fits the output language, otherwise provide the same kind of faithful translation. Preserve every limitation in the source. Never add a company, time period, result, achievement, proficiency, scope, responsibility, causal relation, or candidate action. Return only the structured plan.
Translation is a narrow exception, not permission to generate new candidate claims. Translate the selected confirmed fact into a complete grammatical standalone sentence suitable for a short message to a recruiter, not a résumé/CV bullet or sentence fragment. Preserve actor, tense, qualifiers, negations, uncertainty, limitations and scope. Make an implicit candidate subject explicit only if the source supports the candidate as actor and target-language grammar or naturalness requires it. Do not require an explicit pronoun in languages where a complete sentence naturally omits it, such as Spanish.
For example, «Разрабатывал REST API на FastAPI.» can become “I developed REST APIs using FastAPI.” But «Команда разрабатывала API, я помогал с тестированием.» must preserve the team's development and the candidate's supporting testing role; it must not become “I developed APIs.” Preserve restrictions such as a little, helped, only in a learning project, not, and uncertainty. Do not add commercial or production experience, proficiency, duration, results, responsibility, company/project attribution, stronger certainty or any new factual meaning.
"""

_TRANSLATION_DESCRIPTION = (
    "Faithful, natural, complete grammatical standalone candidate statement in the target language, "
    "suitable for a short recruiter message, not a résumé/CV bullet or sentence fragment. "
    "Preserve actor, tense, qualifiers, negations, uncertainty, limitations and scope; do not add factual meaning. "
    "Make an implicit candidate subject explicit only when supported by the source and required by target-language "
    "grammar or naturalness. An explicit pronoun is not mandatory."
)

_OPENINGS = {
    "ru": {"direct": "Здравствуйте! Пишу по поводу {vacancy}.", "evidence_first": "Здравствуйте! Пишу насчёт {vacancy}.", "vacancy_connection": "Здравствуйте! Пишу по {vacancy}."},
    "en": {"direct": "Hello! I am writing about {vacancy}.", "evidence_first": "Hello! I am reaching out about {vacancy}.", "vacancy_connection": "Hello! I am writing regarding {vacancy}."},
    "de": {"direct": "Guten Tag! Ich schreibe wegen {vacancy}.", "evidence_first": "Guten Tag! Ich melde mich wegen {vacancy}.", "vacancy_connection": "Guten Tag! Ich schreibe wegen {vacancy}."},
    "fr": {"direct": "Bonjour ! Je vous écris au sujet de {vacancy}.", "evidence_first": "Bonjour ! Je vous contacte concernant {vacancy}.", "vacancy_connection": "Bonjour ! Je vous écris à propos de {vacancy}."},
    "es": {"direct": "Hola. Escribo por {vacancy}.", "evidence_first": "Hola. Me pongo en contacto por {vacancy}.", "vacancy_connection": "Hola. Escribo en relación con {vacancy}."},
}

_CLOSINGS = {
    "ru": {"learn_more": "Хотелось бы узнать подробнее о задачах.", "neutral_acknowledgement": "Спасибо за внимание."},
    "en": {"learn_more": "I'd like to learn more about the responsibilities.", "neutral_acknowledgement": "Thank you for your time."},
    "de": {"learn_more": "Ich würde gern mehr über die Aufgaben erfahren.", "neutral_acknowledgement": "Vielen Dank für Ihre Zeit."},
    "fr": {"learn_more": "J’aimerais en savoir plus sur les missions.", "neutral_acknowledgement": "Merci pour votre temps."},
    "es": {"learn_more": "Me gustaría saber más sobre las tareas.", "neutral_acknowledgement": "Gracias por su tiempo."},
}

_INTEREST_OPENINGS = {
    "ru": "Здравствуйте! Меня заинтересовала {vacancy}.",
    "en": "Hello! I'm interested in {vacancy}.",
    "de": "Guten Tag! Ich interessiere mich für {vacancy}.",
    "fr": "Bonjour ! Je m’intéresse à {vacancy}.",
    "es": "Hola. Me interesa {vacancy}.",
}

_WORKPLACE = {
    "ru": {"remote": "удалённый", "hybrid": "гибридный", "onsite": "на месте работодателя"},
    "en": {"remote": "remote", "hybrid": "hybrid", "onsite": "on-site"},
    "de": {"remote": "Remote-Arbeit", "hybrid": "hybrides Arbeiten", "onsite": "Arbeit vor Ort"},
    "fr": {"remote": "à distance", "hybrid": "hybride", "onsite": "sur site"},
    "es": {"remote": "remoto", "hybrid": "híbrido", "onsite": "presencial"},
}

def _plain(value: str) -> str:
    return " ".join(value.replace(" — ", ", ").replace("—", "-").split()).strip(" .")


_LABEL_QUOTES = {
    "ru": ("«", "»"),
    "en": ("“", "”"),
    "de": ("„", "“"),
    "fr": ("« ", " »"),
    "es": ("«", "»"),
}


def _safe_label(value: str, language: str) -> str:
    """Keep employer text visibly inside one plain-text label."""
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") or unicodedata.category(char) in {"Zl", "Zp"} else char
        for char in value
    )
    cleaned = " ".join(cleaned.replace("—", "-").split()).strip()
    opening, closing = _LABEL_QUOTES[language]
    # Neutralize the active delimiters only; punctuation and wording remain unchanged.
    for delimiter in {opening.strip(), closing.strip()}:
        cleaned = cleaned.replace(delimiter, '"')
    return f"{opening}{cleaned}{closing}"


def _display_identity(value: str, *, max_length: int) -> str | None:
    """Return an identity label only when it is clearly not a page/browser title."""
    if len(value) > max_length or "|" in value:
        return None
    if re.search(r"(?:https?://|www\.)", value, re.IGNORECASE):
        return None
    if any(
        unicodedata.category(char).startswith("C") or unicodedata.category(char) in {"Zl", "Zp"}
        for char in value
    ):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _vacancy_reference(evidence: list[VacancyEvidence], language: str, *, interest: bool = False) -> str:
    values = {
        item.kind: item.value
        for item in evidence
        if item.kind in {"title", "company"}
    }
    clean_title = _display_identity(values["title"], max_length=160) if "title" in values else None
    clean_company = _display_identity(values["company"], max_length=120) if "company" in values else None
    title = _safe_label(clean_title, language) if clean_title else None
    company = _safe_label(clean_company, language) if clean_company else None
    if language == "ru":
        if interest:
            return f"вакансия {title} в компании {company}" if title and company else f"вакансия {title}" if title else f"вакансия в компании {company}" if company else "ваша вакансия"
        return f"вакансии {title} в компании {company}" if title and company else f"вакансии {title}" if title else f"вакансии в компании {company}" if company else "вашей вакансии"
    if language == "en":
        return f"the position {title} at {company}" if title and company else f"the position {title}" if title else f"the open role at {company}" if company else "your open role"
    if language == "de":
        if interest:
            return f"die Position {title} bei {company}" if title and company else f"die Position {title}" if title else f"die offene Stelle bei {company}" if company else "Ihre offene Stelle"
        return f"der Position {title} bei {company}" if title and company else f"der Position {title}" if title else f"der offenen Stelle bei {company}" if company else "Ihrer offenen Stelle"
    if language == "fr":
        return f"ce poste {title} chez {company}" if title and company else f"ce poste {title}" if title else f"un poste ouvert chez {company}" if company else "votre poste ouvert"
    return f"el puesto {title} en {company}" if title and company else f"el puesto {title}" if title else f"un puesto abierto en {company}" if company else "su puesto abierto"


def _join(values: list[str], language: str) -> str:
    if len(values) < 2:
        return values[0]
    conjunction = {"ru": "и", "en": "and", "de": "und", "fr": "et", "es": "y"}[language]
    return f"{', '.join(values[:-1])} {conjunction} {values[-1]}"


def _date(year: int, month: int | None) -> str:
    return f"{month:02d}.{year}" if month is not None else str(year)


def _period(item: WorkHistoryEvidence, language: str) -> str | None:
    start = _date(item.start_year, item.start_month) if item.start_year else None
    end = _date(item.end_year, item.end_month) if item.end_year else None
    if item.is_current is True:
        if start:
            return {"ru": f"с {start} по настоящее время", "en": f"from {start} to present", "de": f"seit {start}", "fr": f"depuis {start}", "es": f"desde {start}"}[language]
        return {"ru": "текущее место работы", "en": "current", "de": "aktuell", "fr": "poste actuel", "es": "puesto actual"}[language]
    if start and end:
        return {"ru": f"с {start} по {end}", "en": f"from {start} to {end}", "de": f"von {start} bis {end}", "fr": f"de {start} à {end}", "es": f"de {start} a {end}"}[language]
    if start:
        return {"ru": f"с {start}", "en": f"starting in {start}", "de": f"ab {start}", "fr": f"à partir de {start}", "es": f"a partir de {start}"}[language]
    if end:
        return {"ru": f"до {end}", "en": f"ending in {end}", "de": f"bis {end}", "fr": f"jusqu’en {end}", "es": f"hasta {end}"}[language]
    return None


def _render_work(item: WorkHistoryEvidence, language: str) -> str:
    company = _plain(item.company) if item.company else None
    position = _plain(item.position) if item.position else None
    kind = item.engagement_kind
    if language == "ru":
        if kind == "internship":
            identity = f"Есть опыт стажировки в {company} на позиции {position}" if company and position else f"Есть опыт стажировки в {company}" if company else f"Есть опыт стажировки на позиции {position}"
        elif kind == "freelance":
            identity = f"Есть опыт фриланса для {company} на позиции {position}" if company and position else f"Есть опыт фриланса для {company}" if company else f"Есть опыт фриланса на позиции {position}"
        else:
            identity = f"Есть опыт работы на позиции {position} в {company}" if company and position else f"Есть опыт работы в {company}" if company else f"Есть опыт работы на позиции {position}"
    elif language == "en":
        noun = "an internship" if kind == "internship" else "freelance work" if kind == "freelance" else f"a {position} role" if position else "work experience"
        identity = f"My work history includes {noun} at {company}" if company else f"My work history includes {noun}"
        if position and kind in {"internship", "freelance"}:
            identity += f" as {position}"
    elif language == "de":
        noun = "ein Praktikum" if kind == "internship" else "freiberufliche Arbeit" if kind == "freelance" else f"eine Tätigkeit als {position}" if position else "Berufserfahrung"
        identity = f"Zu meinem Werdegang gehört {noun} bei {company}" if company else f"Zu meinem Werdegang gehört {noun}"
        if position and kind in {"internship", "freelance"}:
            identity += f" als {position}"
    elif language == "fr":
        noun = "un stage" if kind == "internship" else "une activité freelance" if kind == "freelance" else f"un poste de {position}" if position else "une expérience professionnelle"
        identity = f"Mon parcours comprend {noun} chez {company}" if company else f"Mon parcours comprend {noun}"
        if position and kind in {"internship", "freelance"}:
            identity += f" comme {position}"
    else:
        noun = "unas prácticas" if kind == "internship" else "trabajo freelance" if kind == "freelance" else f"un puesto de {position}" if position else "experiencia laboral"
        identity = f"Mi trayectoria incluye {noun} en {company}" if company else f"Mi trayectoria incluye {noun}"
        if position and kind in {"internship", "freelance"}:
            identity += f" como {position}"
    period = _period(item, language)
    return identity + (f", {period}" if period else "") + "."


def _render_confirmed_experience(items: list[ConfirmedExperienceEvidence], language: str, translations: dict[str, str]) -> str:
    facts = [_plain(translations.get(item.id, item.confirmed_text)) for item in items]
    return " ".join(fact if fact.endswith(("!", "?")) else f"{fact}." for fact in facts)


def _render_evidence(item: CandidateEvidence, language: str) -> str:
    if isinstance(item, WorkHistoryEvidence):
        return _render_work(item, language)
    if isinstance(item, TargetRoleEvidence):
        return {"ru": "Рассматриваю позиции {0}.", "en": "I am considering {0} roles.", "de": "Ich ziehe Positionen als {0} in Betracht.", "fr": "J’envisage des postes de {0}.", "es": "Considero puestos de {0}."}[language].format(_plain(item.role))
    if isinstance(item, ProfileLevelEvidence):
        return {"ru": "Свой уровень опыта оцениваю как {0}.", "en": "I describe my experience level as {0}.", "de": "Ich schätze mein Erfahrungsniveau als {0} ein.", "fr": "J’estime mon niveau d’expérience à {0}.", "es": "Considero que mi nivel de experiencia es {0}."}[language].format(_plain(item.level))
    if isinstance(item, LocationPreferenceEvidence):
        return {"ru": "Рассматриваю работу в {0}.", "en": "I am considering roles in {0}.", "de": "Ich ziehe Stellen in {0} in Betracht.", "fr": "J’envisage des postes à {0}.", "es": "Considero puestos en {0}."}[language].format(_plain(item.location))
    if isinstance(item, WorkplacePreferenceEvidence):
        workplace = _WORKPLACE[language].get(item.workplace, _plain(item.workplace))
        return {"ru": "Рассматриваю {0} формат работы.", "en": "I am considering a {0} work arrangement.", "de": "Ich ziehe {0} in Betracht.", "fr": "J’envisage un mode de travail {0}.", "es": "Considero una modalidad de trabajo {0}."}[language].format(workplace)
    if isinstance(item, LanguageEvidence):
        return {"ru": "Мой уровень языка {0}: {1}.", "en": "My {0} level is {1}.", "de": "Mein Niveau in {0} ist {1}.", "fr": "Mon niveau en {0} est {1}.", "es": "Mi nivel de {0} es {1}."}[language].format(_plain(item.language_name), _plain(item.level))
    raise AssertionError("Listed skills are rendered as one controlled group")


_STRONG_VACANCY_KINDS = {
    "responsibility", "required_skill", "nice_to_have_skill", "language_requirement",
    "experience_requirement",
}


def _selected_for_render(context: CoverLetterContext, plan: ResolvedCoverLetterPlan) -> list[CandidateEvidence]:
    candidates = {item.id: item for item in context.candidate_evidence}
    selected = [candidates[identifier] for identifier in plan.selected_fact_ids]
    rank = {
        "confirmed_experience": 0, "work_history": 1, "listed_skill": 2,
        "target_role": 3, "location_preference": 4, "workplace_preference": 4,
        "profile_level": 5, "language": 5,
    }
    if not any(item.kind in _STRONG_VACANCY_KINDS for item in context.vacancy_evidence):
        # Weak vacancy context cannot justify a profile dump. Render only the
        # strongest provider-selected fact, preserving order within one kind.
        return [min(enumerate(selected), key=lambda pair: (rank[pair[1].kind], pair[0]))[1]]

    confirmed = [item for item in selected if isinstance(item, ConfirmedExperienceEvidence)]
    if confirmed:
        return confirmed[:2]
    work = [item for item in selected if isinstance(item, WorkHistoryEvidence)]
    if work:
        reduced: list[CandidateEvidence] = [work[0]]
        work_identity = " ".join(value for value in (work[0].company, work[0].position) if value).casefold()
        secondary_skill = next(
            (item for item in selected if isinstance(item, ListedSkillEvidence)
             and item.skill.casefold() not in work_identity),
            None,
        )
        if secondary_skill is not None:
            reduced.append(secondary_skill)
        return reduced
    skills = [item for item in selected if isinstance(item, ListedSkillEvidence)]
    if skills:
        return skills[:3]
    return [min(enumerate(selected), key=lambda pair: (rank[pair[1].kind], pair[0]))[1]]


def render_cover_letter(
    context: CoverLetterContext, plan: ResolvedCoverLetterPlan, *,
    intent_policy: DraftIntentPolicy = COVER_LETTER_INTENT_POLICY,
) -> CoverLetterOut:
    selected = _selected_for_render(context, plan)
    vacancy = _vacancy_reference(
        context.vacancy_evidence, context.language, interest=intent_policy.express_role_interest,
    )
    opening_template = (
        _INTEREST_OPENINGS[context.language] if intent_policy.express_role_interest
        else _OPENINGS[context.language][plan.composition_style]
    )
    opening = opening_template.format(vacancy=vacancy)
    translations = {item.fact_id: item.translated_text for item in plan.experience_translations}
    evidence_sentences: list[str] = []
    confirmed = [item for item in selected if isinstance(item, ConfirmedExperienceEvidence)]
    skills = [item.skill for item in selected if isinstance(item, ListedSkillEvidence)]
    confirmed_rendered = skills_rendered = False
    for item in selected:
        if isinstance(item, ConfirmedExperienceEvidence):
            if confirmed_rendered:
                continue
            evidence_sentences.append(_render_confirmed_experience(confirmed, context.language, translations))
            confirmed_rendered = True
            continue
        if isinstance(item, ListedSkillEvidence):
            if skills_rendered:
                continue
            rendered = _join([_plain(skill) for skill in skills], context.language)
            evidence_sentences.append({"ru": "Среди моих навыков {0}.", "en": "My skills include {0}.", "de": "Zu meinen Kenntnissen gehören {0}.", "fr": "Mes compétences incluent {0}.", "es": "Entre mis conocimientos están {0}."}[context.language].format(rendered))
            skills_rendered = True
        else:
            evidence_sentences.append(_render_evidence(item, context.language))
    # 2–4 sentences is a writing guideline, not a reason to rewrite user facts.
    # These are composition blocks: each can contain multiple sentences.
    sentences = [opening, *evidence_sentences]
    closing_allowed = plan.closing != "learn_more" or intent_policy.express_desire_to_learn_about_tasks
    if plan.closing != "none" and closing_allowed and len(sentences) < 4:
        sentences.append(_CLOSINGS[context.language][plan.closing])
    letter = " ".join(sentences).strip()
    if not letter:
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_empty_letter")
    if "\x00" in letter:
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_nul")
    if "—" in letter:
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_em_dash")
    if len(letter) > 3000 or len(letter.encode("utf-16-le")) // 2 > 3500:
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_too_long_utf16")
    return CoverLetterOut(letter=letter, used_profile_fact_ids=[item.id for item in selected], language=context.language)


def _schema_reason(error: ValidationError) -> str:
    locations = {tuple(item["loc"]) for item in error.errors()}
    if any(location and location[0] == "selected_evidence" for location in locations):
        return "invalid_fact_count"
    if any(location and location[0] in {"vacancy_anchor_ids", "composition_style", "closing"} for location in locations):
        return "invalid_unsupported_plan"
    return "invalid_schema"


def _has_script(value: str, script: str) -> bool:
    return any(script in unicodedata.name(char, "") for char in value if char.isalpha())


def _has_other_alphabetic_script(value: str) -> bool:
    return any(
        char.isalpha()
        and "CYRILLIC" not in unicodedata.name(char, "")
        and "LATIN" not in unicodedata.name(char, "")
        for char in value
    )


def _translation_policy(source: str, language: str) -> Literal["required", "forbidden", "optional"]:
    has_cyrillic = _has_script(source, "CYRILLIC")
    has_latin = _has_script(source, "LATIN")
    if _has_other_alphabetic_script(source):
        return "required"
    if has_cyrillic:
        return "forbidden" if language == "ru" else "required"
    if language == "ru" and has_latin:
        return "required"
    # Latin-to-Latin language identity cannot be established safely without a
    # language detector. Translation remains optional for EN/DE/FR/ES.
    return "optional"


def _translation_uses_target_script(value: str, language: str) -> bool:
    has_cyrillic = _has_script(value, "CYRILLIC")
    has_latin = _has_script(value, "LATIN")
    has_other_script = _has_other_alphabetic_script(value)
    if language == "ru":
        return has_cyrillic and not has_other_script
    return has_latin and not has_cyrillic and not has_other_script


def _literal(values: list[str]) -> Any:
    """Build a request-scoped Literal enum without leaking IDs into runtime code."""
    return Literal[tuple(values)]  # type: ignore[valid-type]


def _selection_branch(name: str, selection_kind: str, fact_ids: list[str], *, translation: str) -> type[BaseModel]:
    fields: dict[str, tuple[Any, Any]] = {
        "selection_kind": (Literal[selection_kind], ...),  # type: ignore[valid-type]
        "fact_id": (_literal(fact_ids), ...),
    }
    if translation == "required":
        fields["translated_text"] = (str, Field(description=_TRANSLATION_DESCRIPTION))
    elif translation == "optional":
        # The field remains required by Structured Outputs; null means that the
        # original Latin-script evidence already fits the target language.
        fields["translated_text"] = (str | None, Field(description=(
            _TRANSLATION_DESCRIPTION + " Use null when the original wording already fits the output language."
        )))
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)  # type: ignore[call-overload]


def _provider_plan_model(context: CoverLetterContext) -> type[BaseModel]:
    """Create the strict provider schema for this request's actual evidence.

    A branch only exists when it has eligible IDs. Consequently the provider
    cannot attach a translation to a skill/work-history fact, omit a required
    translation, or translate an experience fact for which translation is
    forbidden. Latin-to-Latin identity is intentionally ambiguous without a
    language detector, so that branch carries a required nullable value.
    """
    candidate_ids: list[str] = []
    experience_ids: dict[str, list[str]] = {"forbidden": [], "required": [], "optional": []}
    for evidence in context.candidate_evidence:
        if isinstance(evidence, ConfirmedExperienceEvidence):
            experience_ids[_translation_policy(evidence.confirmed_text, context.language)].append(evidence.id)
        else:
            candidate_ids.append(evidence.id)

    branches: list[type[BaseModel]] = []
    if experience_ids["forbidden"]:
        branches.append(_selection_branch(
            "ExperienceOriginalSelection", "experience_original", experience_ids["forbidden"],
            translation="forbidden",
        ))
    if experience_ids["required"]:
        branches.append(_selection_branch(
            "ExperienceTranslatedSelection", "experience_translated", experience_ids["required"],
            translation="required",
        ))
    if experience_ids["optional"]:
        branches.append(_selection_branch(
            "ExperienceOptionalSelection", "experience_optional", experience_ids["optional"],
            translation="optional",
        ))
    if candidate_ids:
        branches.append(_selection_branch(
            "CandidateFactSelection", "candidate_fact", candidate_ids, translation="forbidden",
        ))
    if not branches:
        raise CoverLetterError("PROFILE_REQUIRED")

    selection_type: Any = branches[0] if len(branches) == 1 else Union[tuple(branches)]  # type: ignore[valid-type]
    anchorable_ids = [evidence.id for evidence in context.vacancy_evidence if evidence.anchorable]
    anchor_field = Field(min_length=1, max_length=2) if anchorable_ids else Field(max_length=0)
    return create_model(
        "CoverLetterPlan",
        __config__=ConfigDict(extra="forbid"),
        vacancy_anchor_ids=(list[str], anchor_field),
        selected_evidence=(list[selection_type], Field(min_length=1, max_length=3)),
        composition_style=(CompositionStyle, ...),
        closing=(ClosingStyle, ...),
    )


def _resolve_provider_plan(plan: BaseModel) -> ResolvedCoverLetterPlan:
    selected_fact_ids: list[str] = []
    translations: list[ExperienceTranslation] = []
    for item in plan.selected_evidence:  # type: ignore[attr-defined]
        selected_fact_ids.append(item.fact_id)
        translated_text = getattr(item, "translated_text", None)
        if translated_text is not None:
            translations.append(ExperienceTranslation(fact_id=item.fact_id, translated_text=translated_text))
    return ResolvedCoverLetterPlan(
        vacancy_anchor_ids=plan.vacancy_anchor_ids,  # type: ignore[attr-defined]
        selected_fact_ids=selected_fact_ids,
        experience_translations=translations,
        composition_style=plan.composition_style,  # type: ignore[attr-defined]
        closing=plan.closing,  # type: ignore[attr-defined]
    )


def _validate_plan(context: CoverLetterContext, plan: ResolvedCoverLetterPlan) -> None:
    if not 1 <= len(plan.selected_fact_ids) <= 3:
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_fact_count")
    if len(set(plan.selected_fact_ids)) != len(plan.selected_fact_ids):
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_duplicate_fact_id")
    if not set(plan.selected_fact_ids) <= {item.id for item in context.candidate_evidence}:
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_unknown_fact_id")
    anchorable_ids = {item.id for item in context.vacancy_evidence if item.anchorable}
    if (
        (anchorable_ids and not 1 <= len(plan.vacancy_anchor_ids) <= 2)
        or (not anchorable_ids and plan.vacancy_anchor_ids)
        or len(set(plan.vacancy_anchor_ids)) != len(plan.vacancy_anchor_ids)
    ):
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_unsupported_plan")
    vacancy_by_id = {item.id: item for item in context.vacancy_evidence}
    if not set(plan.vacancy_anchor_ids) <= set(vacancy_by_id):
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_unknown_vacancy_anchor")
    if any(not vacancy_by_id[identifier].anchorable for identifier in plan.vacancy_anchor_ids):
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_unsupported_plan")

    translation_ids = [translation.fact_id for translation in plan.experience_translations]
    if len(set(translation_ids)) != len(translation_ids):
        raise CoverLetterError("cover_letter_invalid_output", reason="invalid_duplicate_translation")
    candidate_by_id = {item.id: item for item in context.candidate_evidence}
    translations_by_id = {translation.fact_id: translation for translation in plan.experience_translations}
    for translation in plan.experience_translations:
        evidence = candidate_by_id.get(translation.fact_id)
        if translation.fact_id not in plan.selected_fact_ids or not isinstance(evidence, ConfirmedExperienceEvidence):
            raise CoverLetterError("cover_letter_invalid_output", reason="invalid_translation_fact_id")
        translated_text = translation.translated_text.strip()
        if (
            not translated_text
            or len(translated_text) > 500
            or "—" in translated_text
            or any(
                unicodedata.category(char).startswith("C") or unicodedata.category(char) in {"Zl", "Zp"}
                for char in translated_text
            )
        ):
            raise CoverLetterError("cover_letter_invalid_output", reason="invalid_translation_text")
        # This validates ownership, evidence type and text shape only. Without a
        # semantic verifier, the backend cannot mathematically prove that an AI
        # translation is equivalent to its source; that is the accepted
        # multilingual trade-off for this single-call MVP.
    for identifier in plan.selected_fact_ids:
        evidence = candidate_by_id[identifier]
        if not isinstance(evidence, ConfirmedExperienceEvidence):
            continue
        policy = _translation_policy(evidence.confirmed_text, context.language)
        translation = translations_by_id.get(identifier)
        if policy == "required" and translation is None:
            raise CoverLetterError("cover_letter_invalid_output", reason="invalid_missing_translation")
        if policy == "forbidden" and translation is not None:
            raise CoverLetterError("cover_letter_invalid_output", reason="invalid_unnecessary_translation")
        if translation is not None and not _translation_uses_target_script(translation.translated_text, context.language):
            raise CoverLetterError("cover_letter_invalid_output", reason="invalid_translation_language")


class CoverLetterGenerationService:
    def __init__(self, *, client: Any = None, api_key: str | None = OPENAI_API_KEY, model: str = COVER_LETTER_MODEL):
        self.client = client or (OpenAI(api_key=api_key, timeout=COVER_LETTER_TIMEOUT_SECONDS, max_retries=0) if api_key else None)
        self.model = model

    def generate(self, context: CoverLetterContext) -> CoverLetterOut:
        started = time.monotonic()
        outcome, reason = "success", None
        response: object | None = None
        try:
            if self.client is None:
                raise CoverLetterError("cover_letter_ai_unavailable")
            plan_model = _provider_plan_model(context)
            response = self.client.responses.parse(
                model=self.model, store=False,
                input=[{"role": "system", "content": _PLAN_PROMPT}, {"role": "user", "content": context.model_dump_json() + "\nOutput language: " + LANGUAGES[context.language]}],
                text_format=plan_model, reasoning={"effort": "minimal"},
                max_output_tokens=COVER_LETTER_MAX_OUTPUT_TOKENS,
            )
            if getattr(response, "status", None) == "incomplete":
                raise CoverLetterError("cover_letter_invalid_output", reason="invalid_incomplete_response")
            plan = getattr(response, "output_parsed", None)
            if not isinstance(plan, plan_model):
                raise CoverLetterError("cover_letter_invalid_output", reason="invalid_missing_parsed_output")
            resolved_plan = _resolve_provider_plan(plan)
            _validate_plan(context, resolved_plan)
            return render_cover_letter(context, resolved_plan)
        except APITimeoutError:
            outcome = "cover_letter_ai_timeout"
            raise CoverLetterError(outcome) from None
        except ValidationError as error:
            outcome, reason = "cover_letter_invalid_output", _schema_reason(error)
            raise CoverLetterError(outcome, reason=reason) from None
        except CoverLetterError as error:
            outcome, reason = str(error), error.reason
            raise
        except Exception:
            outcome = "cover_letter_ai_provider_error"
            raise CoverLetterError(outcome) from None
        finally:
            fields = ["event=cover_letter_generation", f"result={outcome}", f"model={self.model}", f"duration_seconds={time.monotonic() - started:.3f}"]
            if reason:
                fields.append(f"reason={reason}")
            if response is not None:
                fields.extend(_safe_usage_fields(response))
            telemetry_logger.info("%s", " ".join(fields))
