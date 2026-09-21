import logging
import json
import unicodedata
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError
from pydantic import ValidationError

import app.main as main_module
from app.services.cover_letter import (
    ConfirmedExperienceEvidence,
    CoverLetterContext,
    CoverLetterError,
    CoverLetterGenerationService,
    CoverLetterOut,
    DraftIntentPolicy,
    ExperienceTranslation,
    LanguageEvidence,
    ListedSkillEvidence,
    LocationPreferenceEvidence,
    ProfileLevelEvidence,
    TargetRoleEvidence,
    WorkplacePreferenceEvidence,
    WorkHistoryEvidence,
    ResolvedCoverLetterPlan,
    _provider_plan_model,
    _resolve_provider_plan,
    _safe_label,
    _translation_policy,
    _validate_plan,
    build_context,
    render_cover_letter,
)
from app.services.letter_languages import CoverLetterRequest
from conftest import TestSessionLocal, client
from test_match_api import create_application, create_user, put_profile


def setup_context(**changes):
    user_id = create_user(900)
    put_profile(user_id, skills=["Python", "FastAPI", "PostgreSQL"])
    app_id, _ = create_application(user_id, **changes)
    with TestSessionLocal() as session:
        return user_id, app_id, build_context(session, user_id, app_id, "ru")


def make_plan(context, facts=None, *, anchors=None, translations=None, style="direct", closing="learn_more"):
    return ResolvedCoverLetterPlan(
        vacancy_anchor_ids=anchors if anchors is not None else [context.vacancy_evidence[0].id],
        selected_fact_ids=facts or [context.candidate_evidence[0].id],
        experience_translations=translations or [],
        composition_style=style,
        closing=closing,
    )


def service_with_plan(context, plan, *, response_id=None, usage=None, calls=None):
    def provider_payload():
        translations = {item.fact_id: item.translated_text for item in plan.experience_translations}
        evidence = {item.id: item for item in context.candidate_evidence}
        selected = []
        for fact_id in plan.selected_fact_ids:
            item = evidence[fact_id]
            if not isinstance(item, ConfirmedExperienceEvidence):
                selected.append({"selection_kind": "candidate_fact", "fact_id": fact_id})
                continue
            policy = _translation_policy(item.confirmed_text, context.language)
            if policy == "forbidden":
                selected.append({"selection_kind": "experience_original", "fact_id": fact_id})
            elif policy == "required":
                selected.append({
                    "selection_kind": "experience_translated", "fact_id": fact_id,
                    "translated_text": translations[fact_id],
                })
            else:
                selected.append({
                    "selection_kind": "experience_optional", "fact_id": fact_id,
                    "translated_text": translations.get(fact_id),
                })
        return {
            "vacancy_anchor_ids": plan.vacancy_anchor_ids,
            "selected_evidence": selected,
            "composition_style": plan.composition_style,
            "closing": plan.closing,
        }

    class Responses:
        def parse(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            parsed = kwargs["text_format"].model_validate(provider_payload())
            return SimpleNamespace(id=response_id, usage=usage, output_parsed=parsed)

    return CoverLetterGenerationService(client=SimpleNamespace(responses=Responses()))


def test_endpoint_generates_each_time_without_writes_or_variant(monkeypatch):
    user_id, app_id, context = setup_context()
    calls = []
    service = service_with_plan(context, make_plan(context, ["skill_0", "skill_1"]), calls=calls)
    monkeypatch.setattr(main_module, "cover_letter_service", service)
    before = client.get(f"/users/{user_id}/applications/{app_id}").json()

    for _ in range(2):
        response = client.post(f"/users/{user_id}/applications/{app_id}/cover-letter", json={"language": "ru"})
        assert response.status_code == 200
        assert response.json()["language"] == "ru"
        assert response.json()["used_profile_fact_ids"] == ["skill_0", "skill_1"]

    assert len(calls) == 2
    assert calls[0]["store"] is False
    assert calls[0]["reasoning"] == {"effort": "minimal"}
    assert calls[0]["text_format"].__name__ == "CoverLetterPlan"
    assert calls[0]["text_format"] is not calls[1]["text_format"]
    assert calls[0]["input"][1]["content"] == calls[1]["input"][1]["content"]
    assert "salary_min" not in calls[0]["input"][1]["content"]
    assert "score" not in context.matching
    assert before == client.get(f"/users/{user_id}/applications/{app_id}").json()


def test_provider_schema_contains_only_typed_plan_not_candidate_prose():
    _, _, context = setup_context()
    properties = _provider_plan_model(context).model_json_schema()["properties"]
    assert set(properties) == {"vacancy_anchor_ids", "selected_evidence", "composition_style", "closing"}
    assert "letter" not in properties and "used_profile_fact_ids" not in properties


@pytest.mark.parametrize("job_text", ["description", "requirements_text"])
def test_generation_succeeds_when_job_has_only_non_anchorable_text(monkeypatch, job_text):
    changes = {
        "title": None,
        "company": None,
        "description": None,
        "requirements_text": None,
        "required_skills": [],
        "seniority": "unknown",
        "ai_enrichment_status": "failed",
    }
    changes[job_text] = "Build reliable backend services"
    owner, app_id, context = setup_context(**changes)
    assert context.vacancy_evidence
    assert not any(item.anchorable for item in context.vacancy_evidence)
    plan = make_plan(context, ["skill_0"], anchors=[], closing="none")
    calls = []
    monkeypatch.setattr(main_module, "cover_letter_service", service_with_plan(context, plan, calls=calls))

    response = client.post(
        f"/users/{owner}/applications/{app_id}/cover-letter", json={"language": "ru"},
    )

    assert response.status_code == 200
    assert response.json()["used_profile_fact_ids"] == ["skill_0"]
    assert len(calls) == 1
    assert "when none are available, return an empty vacancy_anchor_ids list" in calls[0]["input"][0]["content"]


def test_anchorless_provider_schema_accepts_only_empty_anchor_list():
    context = CoverLetterContext(
        candidate_evidence=[ListedSkillEvidence(id="skill_0", skill="Python")],
        vacancy_evidence=[{
            "id": "vacancy:description", "kind": "description",
            "value": "Build reliable backend services", "anchorable": False,
        }],
        matching={}, language="en",
    )
    model = _provider_plan_model(context)
    valid = model.model_validate(_provider_payload([
        {"selection_kind": "candidate_fact", "fact_id": "skill_0"},
    ], anchors=[]))
    assert valid.vacancy_anchor_ids == []
    for invalid_id in ("vacancy:description", "vacancy:unknown"):
        with pytest.raises(ValidationError):
            model.model_validate(_provider_payload([
                {"selection_kind": "candidate_fact", "fact_id": "skill_0"},
            ], anchors=[invalid_id]))


def test_provider_schema_requires_one_or_two_known_anchors_when_available():
    _, _, context = setup_context(description="Employer-controlled description")
    model = _provider_plan_model(context)
    selected = [{"selection_kind": "candidate_fact", "fact_id": "skill_0"}]
    with pytest.raises(ValidationError):
        model.model_validate(_provider_payload(selected, anchors=[]))
    for anchors, reason in (
        (["vacancy:description"], "invalid_unsupported_plan"),
        (["vacancy:unknown"], "invalid_unknown_vacancy_anchor"),
    ):
        parsed = model.model_validate(_provider_payload(selected, anchors=anchors))
        with pytest.raises(CoverLetterError) as caught:
            _validate_plan(context, _resolve_provider_plan(parsed))
        assert caught.value.reason == reason
    assert model.model_validate(
        _provider_payload(selected, anchors=["vacancy:title"])
    ).vacancy_anchor_ids == ["vacancy:title"]


def test_no_confirmed_experience_omits_every_translation_branch():
    _, _, context = setup_context()
    schema = json.dumps(_provider_plan_model(context).model_json_schema())
    assert "candidate_fact" in schema
    assert "experience_original" not in schema
    assert "experience_translated" not in schema
    assert "experience_optional" not in schema
    assert "translated_text" not in schema


def _provider_payload(selected_evidence, *, anchors=None, style="direct", closing="none"):
    return {
        "vacancy_anchor_ids": anchors if anchors is not None else ["vacancy:title"],
        "selected_evidence": selected_evidence,
        "composition_style": style,
        "closing": closing,
    }


def test_request_scoped_schema_makes_translation_ownership_structural():
    context = CoverLetterContext(
        candidate_evidence=[
            ListedSkillEvidence(id="skill_0", skill="Python"),
            WorkHistoryEvidence(
                id="work_experience:1", company="Acme", position="Developer",
                engagement_kind="unknown", start_year=None, start_month=None,
                end_year=None, end_month=None, is_current=None,
            ),
            ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text="Интегрировал API"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Build APIs"},
        ],
        matching={}, language="en",
    )
    model = _provider_plan_model(context)
    valid = model.model_validate(_provider_payload([
        {"selection_kind": "candidate_fact", "fact_id": "skill_0"},
        {
            "selection_kind": "experience_translated", "fact_id": "experience_fact:1",
            "translated_text": "Integrated APIs",
        },
    ]))
    assert [item.fact_id for item in valid.selected_evidence] == ["skill_0", "experience_fact:1"]

    invalid_items = [
        {"selection_kind": "candidate_fact", "fact_id": "skill_0", "translated_text": "Python"},
        {"selection_kind": "candidate_fact", "fact_id": "work_experience:1", "translated_text": "Acme"},
        {"selection_kind": "experience_translated", "fact_id": "experience_fact:1"},
        {
            "selection_kind": "experience_translated", "fact_id": "skill_0",
            "translated_text": "Python",
        },
        {
            "selection_kind": "experience_translated", "fact_id": "experience_fact:999",
            "translated_text": "Integrated APIs",
        },
    ]
    for item in invalid_items:
        with pytest.raises(ValidationError):
            model.model_validate(_provider_payload([item]))


def test_ru_to_ru_schema_omits_translation_field_and_translation_branches():
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:1", confirmed_text="Интегрировал сторонние API",
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="ru",
    )
    model = _provider_plan_model(context)
    schema = json.dumps(model.model_json_schema())
    assert "experience_original" in schema
    assert "experience_translated" not in schema
    assert "experience_optional" not in schema
    parsed = model.model_validate(_provider_payload([
        {"selection_kind": "experience_original", "fact_id": "experience_fact:1"},
    ]))
    assert parsed.selected_evidence[0].selection_kind == "experience_original"
    with pytest.raises(ValidationError):
        model.model_validate(_provider_payload([{
            "selection_kind": "experience_original", "fact_id": "experience_fact:1",
            "translated_text": "Integrated third-party APIs",
        }]))
    with pytest.raises(ValidationError):
        model.model_validate(_provider_payload([{
            "selection_kind": "experience_translated", "fact_id": "experience_fact:1",
            "translated_text": "Integrated third-party APIs",
        }]))


def test_cyrillic_to_english_schema_only_allows_translated_experience():
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:1", confirmed_text="Интегрировал сторонние API",
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="en",
    )
    model = _provider_plan_model(context)
    schema = json.dumps(model.model_json_schema())
    assert "experience_translated" in schema
    assert "experience_original" not in schema
    assert "experience_optional" not in schema
    with pytest.raises(ValidationError):
        model.model_validate(_provider_payload([{
            "selection_kind": "experience_original", "fact_id": "experience_fact:1",
        }]))


def test_non_confirmed_evidence_ids_never_enter_experience_branches():
    non_confirmed = [
        ListedSkillEvidence(id="skill_0", skill="Python"),
        WorkHistoryEvidence(
            id="work_experience:1", company="Acme", position="Developer",
            engagement_kind="unknown", start_year=None, start_month=None,
            end_year=None, end_month=None, is_current=None,
        ),
        LocationPreferenceEvidence(id="location_0", location="Yerevan"),
        ProfileLevelEvidence(id="experience", level="junior"),
    ]
    context = CoverLetterContext(
        candidate_evidence=[
            *non_confirmed,
            ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text="Интегрировал API"),
        ],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="en",
    )
    model = _provider_plan_model(context)
    for evidence in non_confirmed:
        with pytest.raises(ValidationError):
            model.model_validate(_provider_payload([{
                "selection_kind": "experience_translated", "fact_id": evidence.id,
                "translated_text": "Translated",
            }]))


def test_provider_selection_resolves_in_order_with_stable_ids_and_translation():
    context = CoverLetterContext(
        candidate_evidence=[
            ListedSkillEvidence(id="skill_0", skill="Python"),
            ConfirmedExperienceEvidence(id="experience_fact:7", confirmed_text="Интегрировал API"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Build APIs"},
        ],
        matching={}, language="en",
    )
    provider_plan = _provider_plan_model(context).model_validate(_provider_payload([
        {
            "selection_kind": "experience_translated", "fact_id": "experience_fact:7",
            "translated_text": "Integrated APIs",
        },
        {"selection_kind": "candidate_fact", "fact_id": "skill_0"},
    ]))
    resolved = _resolve_provider_plan(provider_plan)
    assert resolved == ResolvedCoverLetterPlan(
        vacancy_anchor_ids=["vacancy:title"],
        selected_fact_ids=["experience_fact:7", "skill_0"],
        experience_translations=[ExperienceTranslation(
            fact_id="experience_fact:7", translated_text="Integrated APIs",
        )],
        composition_style="direct", closing="none",
    )
    result = render_cover_letter(context, resolved)
    assert result.used_profile_fact_ids == ["experience_fact:7"]
    assert "Integrated APIs" in result.letter and "Python" not in result.letter


def test_optional_latin_to_latin_branch_requires_nullable_translation_field():
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:1", confirmed_text="Integrated third-party APIs",
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="de",
    )
    model = _provider_plan_model(context)
    for value in (None, "Drittanbieter-APIs integriert"):
        parsed = model.model_validate(_provider_payload([{
            "selection_kind": "experience_optional", "fact_id": "experience_fact:1",
            "translated_text": value,
        }]))
        assert parsed.selected_evidence[0].translated_text == value
    with pytest.raises(ValidationError):
        model.model_validate(_provider_payload([{
            "selection_kind": "experience_optional", "fact_id": "experience_fact:1",
        }]))


@pytest.mark.parametrize("source,language", [
    ("Ինտեգրել եմ API", "en"),
    ("Ինտեգրել եմ API", "ru"),
    ("Έκανα ενσωμάτωση API", "en"),
    ("Έκανα ενσωμάτωση API", "ru"),
    ("APIを開発しました", "en"),
    ("APIを開発しました", "ru"),
])
def test_other_alphabetic_scripts_require_translation(source, language):
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:1", confirmed_text=source,
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language=language,
    )
    assert _translation_policy(source, language) == "required"
    model = _provider_plan_model(context)
    schema = json.dumps(model.model_json_schema())
    assert "experience_translated" in schema
    assert "experience_optional" not in schema
    with pytest.raises(ValidationError):
        model.model_validate(_provider_payload([{
            "selection_kind": "experience_translated",
            "fact_id": "experience_fact:1",
            "translated_text": None,
        }]))


def test_russian_fact_with_latin_technology_stays_original_for_russian():
    source = "Разрабатывал REST API на FastAPI и работал с PostgreSQL."
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:1", confirmed_text=source,
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="ru",
    )
    assert _translation_policy(source, "ru") == "forbidden"
    parsed = _provider_plan_model(context).model_validate(_provider_payload([{
        "selection_kind": "experience_original", "fact_id": "experience_fact:1",
    }]))
    assert parsed.selected_evidence[0].fact_id == "experience_fact:1"


def test_context_uses_typed_candidate_and_separate_vacancy_evidence():
    owner, app_id, _ = setup_context(
        title="API Integration Engineer", company="Example", required_skills=["FastAPI"],
        responsibilities=["Integrate partner APIs"],
    )
    put_profile(owner, target_roles=["Backend Developer"], skills=["Python", "FastAPI"],
                experience="middle", location=["Yerevan"], workplace_preference="remote",
                languages=[{"language": "English", "level": "B2"}])
    practical = client.post(f"/users/{owner}/profile/experience-facts", json={"text": "Интегрировал сторонние API"}).json()
    work = client.post(f"/users/{owner}/profile/work-experiences", json={
        "company": "Acme", "position": "Python Developer", "start_year": 2024,
        "end_year": 2025, "is_current": False,
    }).json()

    with TestSessionLocal() as session:
        context = build_context(session, owner, app_id, "ru")

    facts = {fact.id: fact for fact in context.candidate_evidence}
    assert isinstance(facts["skill_1"], ListedSkillEvidence)
    assert facts["skill_1"].skill == "FastAPI"
    confirmed = facts[f"experience_fact:{practical['id']}"]
    assert isinstance(confirmed, ConfirmedExperienceEvidence)
    assert confirmed.confirmed_text == "Интегрировал сторонние API"
    history = facts[f"work_experience:{work['id']}"]
    assert isinstance(history, WorkHistoryEvidence)
    assert history.company == "Acme" and history.position == "Python Developer"
    vacancy = {item.id: item for item in context.vacancy_evidence}
    assert vacancy["vacancy:title"].value == "API Integration Engineer"
    assert vacancy["vacancy:responsibility:0"].value == "Integrate partner APIs"
    assert all(item.id.startswith("vacancy:") for item in context.vacancy_evidence)
    assert not {item.id for item in context.candidate_evidence} & set(vacancy)
    for forbidden in ('"score"', '"coverage"', '"confidence"', '"recommendation"', '"verdict"'):
        assert forbidden not in context.model_dump_json()


def test_candidate_evidence_is_ordered_by_strength_for_provider_selection():
    owner, app_id, _ = setup_context()
    practical = client.post(
        f"/users/{owner}/profile/experience-facts",
        json={"text": "Интегрировал сторонние API"},
    ).json()
    work = client.post(f"/users/{owner}/profile/work-experiences", json={
        "company": "Acme", "position": "Backend Developer", "engagement_kind": "employment",
        "start_year": 2022, "end_year": 2024, "is_current": False,
    }).json()
    with TestSessionLocal() as session:
        context = build_context(session, owner, app_id, "ru")
    assert context.candidate_evidence[0].id == f"experience_fact:{practical['id']}"
    assert context.candidate_evidence[1].id == f"work_experience:{work['id']}"
    assert isinstance(context.candidate_evidence[2], ListedSkillEvidence)


def test_vacancies_change_relevance_without_changing_candidate_evidence():
    owner, _, first = setup_context(title="FastAPI Engineer", required_skills=["FastAPI"])
    second_id, _ = create_application(owner, title="PostgreSQL Engineer", required_skills=["PostgreSQL"])
    with TestSessionLocal() as session:
        second = build_context(session, owner, second_id, "ru")
    assert first.candidate_evidence == second.candidate_evidence
    assert first.vacancy_evidence != second.vacancy_evidence
    assert first.matching["strengths"] != second.matching["strengths"]


@pytest.mark.parametrize("language", ["ru", "en", "de", "fr", "es"])
def test_supported_languages_use_same_deterministic_skill_semantics(language):
    _, _, context = setup_context(title="Database Engineer")
    context = context.model_copy(update={"language": language})
    result = render_cover_letter(context, make_plan(context, ["skill_2"]))
    assert "PostgreSQL" in result.letter
    assert result.used_profile_fact_ids == ["skill_2"]
    expected = {
        "ru": "Среди моих навыков PostgreSQL.",
        "en": "My skills include PostgreSQL.",
        "de": "Zu meinen Kenntnissen gehören PostgreSQL.",
        "fr": "Mes compétences incluent PostgreSQL.",
        "es": "Entre mis conocimientos están PostgreSQL.",
    }[language]
    assert expected in result.letter
    forbidden = {
        "ru": ("работаю с", "использую", "есть опыт"),
        "en": ("i use", "worked with", "experience with"),
        "de": ("ich arbeite", "ich verwende", "erfahrung mit"),
        "fr": ("je travaille", "j'utilise", "expérience avec"),
        "es": ("trabajo con", "utilizo", "experiencia con"),
    }[language]
    assert not any(phrase in result.letter.casefold() for phrase in forbidden)
    assert "—" not in result.letter


def test_confirmed_experience_dominates_work_without_cross_attribution():
    context = CoverLetterContext(
        candidate_evidence=[
            WorkHistoryEvidence(
                id="work_experience:1", company="BrightOps", position="Backend Developer",
                engagement_kind="employment", start_year=2022, start_month=None,
                end_year=2024, end_month=None, is_current=False,
            ),
            ConfirmedExperienceEvidence(id="experience_fact:2", confirmed_text="Интегрировал сторонние API"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Backend Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Развивать внутренние сервисы"},
        ],
        matching={}, language="ru",
    )
    result = render_cover_letter(context, make_plan(
        context, ["work_experience:1", "experience_fact:2"],
        anchors=["vacancy:responsibility:0"], closing="none",
    ))
    assert "BrightOps" not in result.letter and "Интегрировал сторонние API" in result.letter
    assert "Развивать внутренние сервисы" not in result.letter
    assert "BrightOps интегрировал" not in result.letter
    assert result.used_profile_fact_ids == ["experience_fact:2"]


def test_required_skill_anchor_does_not_narrate_or_duplicate_selected_skill():
    context = CoverLetterContext(
        candidate_evidence=[ListedSkillEvidence(id="skill_0", skill="Django")],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Backend Developer"},
            {"id": "vacancy:required_skill:0", "kind": "required_skill", "value": "Django"},
        ],
        matching={}, language="ru",
    )
    result = render_cover_letter(context, make_plan(
        context, ["skill_0"], anchors=["vacancy:required_skill:0"], closing="none",
    ))
    assert result.letter.count("Django") == 1
    assert "Среди требований" not in result.letter
    assert result.used_profile_fact_ids == ["skill_0"]


@pytest.mark.parametrize(
    "kind,value",
    [
        ("responsibility", "Build private APIs"),
        ("required_skill", "Django"),
        ("nice_to_have_skill", "Kubernetes"),
        ("seniority", "middle"),
        ("location", "Yerevan"),
        ("workplace", "remote"),
        ("employment_type", "full-time"),
        ("language_requirement", "English B2"),
        ("experience_requirement", "3 years"),
    ],
)
def test_non_identity_vacancy_anchor_is_relevance_only(kind, value):
    anchor_id = f"vacancy:{kind}:0"
    context = CoverLetterContext(
        candidate_evidence=[ListedSkillEvidence(id="skill_0", skill="Python")],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Backend Developer"},
            {"id": anchor_id, "kind": kind, "value": value},
        ],
        matching={}, language="en",
    )
    result = render_cover_letter(context, make_plan(
        context, ["skill_0"], anchors=[anchor_id], closing="none",
    ))
    assert value not in result.letter
    assert "My skills include Python." in result.letter


def test_work_only_uses_identity_period_and_kind_without_responsibilities():
    context = CoverLetterContext(
        candidate_evidence=[WorkHistoryEvidence(
            id="work_experience:1", company="BrightOps", position="Backend Developer",
            engagement_kind="employment", start_year=2022, start_month=None,
            end_year=2024, end_month=None, is_current=False,
        )],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Platform Engineer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Build internal services"},
        ],
        matching={}, language="en",
    )
    result = render_cover_letter(context, make_plan(
        context, ["work_experience:1"], anchors=["vacancy:responsibility:0"], closing="none",
    ))
    assert "BrightOps" in result.letter and "Backend Developer" in result.letter
    assert "2022" in result.letter and "2024" in result.letter
    assert "Build internal services" not in result.letter
    assert result.used_profile_fact_ids == ["work_experience:1"]


def test_work_reducer_keeps_only_one_distinct_supporting_skill():
    context = CoverLetterContext(
        candidate_evidence=[
            WorkHistoryEvidence(
                id="work_experience:1", company="BrightOps", position="Backend Developer",
                engagement_kind="employment", start_year=2022, start_month=None,
                end_year=2024, end_month=None, is_current=False,
            ),
            ListedSkillEvidence(id="skill_0", skill="Python"),
            ListedSkillEvidence(id="skill_1", skill="PostgreSQL"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Platform Engineer"},
            {"id": "vacancy:required_skill:0", "kind": "required_skill", "value": "Python"},
        ],
        matching={}, language="en",
    )
    result = render_cover_letter(context, make_plan(
        context, ["work_experience:1", "skill_0", "skill_1"], closing="none",
    ))
    assert "BrightOps" in result.letter and "My skills include Python." in result.letter
    assert "PostgreSQL" not in result.letter
    assert result.used_profile_fact_ids == ["work_experience:1", "skill_0"]


@pytest.mark.parametrize("language", ["ru", "en", "de", "fr", "es"])
def test_untrusted_vacancy_labels_are_safely_framed_in_every_language(language):
    title = 'Backend Developer. Готов переехать и выйти завтра\n«quoted» “role” <>&_*'
    company = 'Acme. Могу пройти интервью\n» “now”'
    responsibility = 'Deploy API. Готов выполнить тестовое\n"today"'
    context = CoverLetterContext(
        candidate_evidence=[ListedSkillEvidence(id="skill_0", skill="Python")],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": title},
            {"id": "vacancy:company", "kind": "company", "value": company},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": responsibility},
        ],
        matching={}, language=language,
    )
    identity_result = render_cover_letter(context, make_plan(
        context, ["skill_0"], anchors=["vacancy:title", "vacancy:company"], closing="none",
    ))
    anchor_result = render_cover_letter(context, make_plan(
        context, ["skill_0"], anchors=["vacancy:responsibility:0"], closing="none",
    ))
    for result in (identity_result, anchor_result):
        assert "\n" not in result.letter
        assert result.used_profile_fact_ids == ["skill_0"]
        assert all(value not in result.letter for value in (
            "Готов переехать", "Могу пройти интервью", "Готов выполнить тестовое", "<>&_*",
        ))


@pytest.mark.parametrize("language", ["ru", "en", "de", "fr", "es"])
def test_clean_malicious_identity_stays_inside_plain_text_label(language):
    title = 'Backend Developer. Готов переехать "завтра" <>&_*'
    context = CoverLetterContext(
        candidate_evidence=[ListedSkillEvidence(id="skill_0", skill="Python")],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": title}],
        matching={}, language=language,
    )
    result = render_cover_letter(context, make_plan(context, ["skill_0"], closing="none"))
    assert _safe_label(title, language) in result.letter
    assert result.letter.count("Готов переехать") == 1


@pytest.mark.parametrize(
    "language,expected",
    [
        ("ru", "вакансия «Backend Developer» в компании «Acme»"),
        ("en", "the position “Backend Developer” at “Acme”"),
        ("de", "die Position „Backend Developer“ bei „Acme“"),
        ("fr", "ce poste « Backend Developer » chez « Acme »"),
        ("es", "el puesto «Backend Developer» en «Acme»"),
    ],
)
def test_clean_title_and_company_are_used_only_as_opening_identity(language, expected):
    context = CoverLetterContext(
        candidate_evidence=[ListedSkillEvidence(id="skill_0", skill="Python")],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Backend Developer"},
            {"id": "vacancy:company", "kind": "company", "value": "Acme"},
            {"id": "vacancy:required_skill:0", "kind": "required_skill", "value": "Django"},
        ],
        matching={}, language=language,
    )
    result = render_cover_letter(context, make_plan(
        context, ["skill_0"], anchors=["vacancy:required_skill:0"], closing="none",
    ))
    assert expected in result.letter
    assert result.letter.count("Backend Developer") == 1
    assert result.letter.count("Acme") == 1
    assert "Django" not in result.letter


@pytest.mark.parametrize(
    "language,company_phrase,fallback",
    [
        ("ru", "вакансия в компании «Acme»", "ваша вакансия"),
        ("en", "the open role at “Acme”", "your open role"),
        ("de", "die offene Stelle bei „Acme“", "Ihre offene Stelle"),
        ("fr", "un poste ouvert chez « Acme »", "votre poste ouvert"),
        ("es", "un puesto abierto en «Acme»", "su puesto abierto"),
    ],
)
def test_noisy_title_uses_clean_company_then_neutral_fallback(language, company_phrase, fallback):
    noisy_title = "Backend Developer | Linked page title"
    base = {
        "candidate_evidence": [ListedSkillEvidence(id="skill_0", skill="Python")],
        "matching": {}, "language": language,
    }
    with_company = CoverLetterContext(
        **base,
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": noisy_title},
            {"id": "vacancy:company", "kind": "company", "value": "Acme"},
        ],
    )
    company_result = render_cover_letter(with_company, make_plan(with_company, ["skill_0"], closing="none"))
    assert noisy_title not in company_result.letter
    assert company_phrase in company_result.letter

    all_noisy = CoverLetterContext(**base, vacancy_evidence=[
        {"id": "vacancy:title", "kind": "title", "value": "https://example.test/jobs/1"},
        {"id": "vacancy:company", "kind": "company", "value": "Acme\nInjected"},
    ])
    fallback_result = render_cover_letter(all_noisy, make_plan(all_noisy, ["skill_0"], closing="none"))
    assert "https://" not in fallback_result.letter and "Injected" not in fallback_result.letter
    assert fallback in fallback_result.letter


@pytest.mark.parametrize(
    "source,language,translated",
    [
        ("Интегрировал сторонние API", "en", "Integrated third-party APIs"),
        ("Built REST APIs with FastAPI", "ru", "Разрабатывал REST API на FastAPI"),
    ],
)
def test_cross_language_confirmed_experience_uses_typed_translation(source, language, translated):
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(id="experience_fact:4", confirmed_text=source)],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "API Engineer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Develop APIs"},
        ],
        matching={}, language=language,
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text=translated)],
        closing="none",
    )
    calls = []
    result = service_with_plan(context, plan, calls=calls).generate(context)
    assert len(calls) == 1
    assert translated in result.letter
    assert source not in result.letter
    assert result.used_profile_fact_ids == ["experience_fact:4"]


def test_every_selected_cross_script_confirmed_fact_requires_translation():
    context = CoverLetterContext(
        candidate_evidence=[
            ConfirmedExperienceEvidence(id="experience_fact:4", confirmed_text="Разрабатывал REST API на FastAPI"),
            ConfirmedExperienceEvidence(id="experience_fact:5", confirmed_text="Интегрировал сторонние API"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "API Engineer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Develop APIs"},
        ],
        matching={}, language="en",
    )
    incomplete = make_plan(
        context, ["experience_fact:4", "experience_fact:5"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text="Developed REST APIs with FastAPI")],
        closing="none",
    )
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, incomplete)
    assert caught.value.reason == "invalid_missing_translation"

    complete = incomplete.model_copy(update={"experience_translations": [
        ExperienceTranslation(fact_id="experience_fact:4", translated_text="Developed REST APIs with FastAPI"),
        ExperienceTranslation(fact_id="experience_fact:5", translated_text="Integrated third-party APIs"),
    ]})
    result = service_with_plan(context, complete).generate(context)
    assert "Developed REST APIs with FastAPI. Integrated third-party APIs." in result.letter
    assert not any("CYRILLIC" in unicodedata.name(char, "") for char in result.letter)


def test_obviously_unnecessary_cyrillic_to_russian_translation_is_rejected():
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:4", confirmed_text="Разрабатывал REST API на FastAPI",
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "API Engineer"}],
        matching={}, language="ru",
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text="Developed REST APIs with FastAPI")],
    )
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, plan)
    assert caught.value.reason == "invalid_unnecessary_translation"


@pytest.mark.parametrize(
    "source,language,translated",
    [
        ("Интегрировал API", "en", "Интегрировал API"),
        ("Интегрировал API", "de", "Интегрировал API"),
        ("Интегрировал API", "fr", "Интегрировал API"),
        ("Интегрировал API", "es", "Интегрировал API"),
        ("Integrated APIs", "ru", "Integrated APIs"),
    ],
)
def test_required_translation_must_switch_to_target_script(source, language, translated):
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(id="experience_fact:4", confirmed_text=source)],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "API Engineer"}],
        matching={}, language=language,
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text=translated)],
    )
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, plan)
    assert caught.value.reason == "invalid_translation_language"


@pytest.mark.parametrize("translated", [
    "Ինտեգրել եմ API",
    "Έκανα ενσωμάτωση API",
    "APIを開発しました",
])
def test_latin_target_rejects_residual_incompatible_alphabetic_scripts(translated):
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:4", confirmed_text=translated,
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "API Engineer"}],
        matching={}, language="en",
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text=translated)],
    )
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, plan)
    assert caught.value.reason == "invalid_translation_language"


@pytest.mark.parametrize("source,language,translated", [
    ("Ինտեգրել եմ API", "en", "I integrated an API."),
    ("Έκανα ενσωμάτωση API", "en", "I integrated an API."),
    ("APIを開発しました", "en", "I developed an API."),
    ("Ինտեգրել եմ API", "de", "Ich habe eine API integriert."),
    ("Έκανα ενσωμάτωση API", "fr", "J’ai intégré une API."),
    ("APIを開発しました", "es", "Desarrollé una API."),
])
def test_required_other_script_translation_accepts_complete_latin_target_text(source, language, translated):
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:4", confirmed_text=source,
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "API Engineer"}],
        matching={}, language=language,
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text=translated)],
    )
    _validate_plan(context, plan)


@pytest.mark.parametrize("residual", [
    "Разрабатывал API. Ինտեգրել եմ արտաքին API.",
    "Разрабатывал API. Έκανα ενσωμάτωση API.",
    "Разрабатывал API. APIを開発しました.",
])
def test_russian_target_rejects_residual_incompatible_alphabetic_scripts(residual):
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:4", confirmed_text="Developed an API.",
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "API Engineer"}],
        matching={}, language="ru",
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text=residual)],
    )
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, plan)
    assert caught.value.reason == "invalid_translation_language"


def test_russian_target_allows_cyrillic_with_latin_technology_labels():
    translated = "Разрабатывал REST API на FastAPI и работал с PostgreSQL."
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(
            id="experience_fact:4", confirmed_text="Developed REST APIs and worked with PostgreSQL.",
        )],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "API Engineer"}],
        matching={}, language="ru",
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text=translated)],
    )
    _validate_plan(context, plan)


@pytest.mark.parametrize(
    "translated_id,selected_ids,candidates",
    [
        ("skill_0", ["skill_0"], [ListedSkillEvidence(id="skill_0", skill="Python")]),
        (
            "work_experience:1", ["work_experience:1"],
            [WorkHistoryEvidence(id="work_experience:1", company="Acme", position="Developer", engagement_kind="unknown", start_year=None, start_month=None, end_year=None, end_month=None, is_current=None)],
        ),
        (
            "experience_fact:4", ["skill_0"],
            [ConfirmedExperienceEvidence(id="experience_fact:4", confirmed_text="Built APIs"), ListedSkillEvidence(id="skill_0", skill="Python")],
        ),
        ("experience_fact:999", ["skill_0"], [ListedSkillEvidence(id="skill_0", skill="Python")]),
    ],
)
def test_translation_rejects_non_confirmed_unselected_and_unknown_ids(translated_id, selected_ids, candidates):
    context = CoverLetterContext(
        candidate_evidence=candidates,
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="en",
    )
    plan = make_plan(
        context, selected_ids,
        translations=[ExperienceTranslation(fact_id=translated_id, translated_text="Translated text")],
        closing="none",
    )
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, plan)
    assert caught.value.reason == "invalid_translation_fact_id"


def test_duplicate_experience_translation_is_rejected_with_safe_telemetry(caplog):
    _, _, context = setup_context()
    context.candidate_evidence.append(ConfirmedExperienceEvidence(id="experience_fact:4", confirmed_text="PRIVATE SOURCE"))
    translation = ExperienceTranslation(fact_id="experience_fact:4", translated_text="PRIVATE TRANSLATION")
    plan = make_plan(context, ["experience_fact:4"], translations=[translation, translation])
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, plan)
    assert caught.value.reason == "invalid_duplicate_translation"
    assert "PRIVATE SOURCE" not in caplog.text and "PRIVATE TRANSLATION" not in caplog.text


@pytest.mark.parametrize("text", ["", "translated\nclaim", "translated\x00claim", "translated — claim"])
def test_invalid_translation_text_is_rejected(text):
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(id="experience_fact:4", confirmed_text="Built APIs")],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="en",
    )
    plan = make_plan(
        context, ["experience_fact:4"],
        translations=[ExperienceTranslation(fact_id="experience_fact:4", translated_text=text)],
        closing="none",
    )
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, plan)
    assert caught.value.reason == "invalid_translation_text"


def test_prompt_limits_translation_to_selected_confirmed_experience():
    _, _, context = setup_context()
    calls = []
    service_with_plan(context, make_plan(context), calls=calls).generate(context)
    prompt = calls[0]["input"][0]["content"]
    assert "selected_evidence branch determines" in prompt
    assert "candidate_fact is only for the non-confirmed IDs" in prompt
    assert "experience_original is only for confirmed experience" in prompt
    assert "experience_translated requires a faithful, natural standalone candidate statement" in prompt
    assert "experience_optional accepts null" in prompt
    assert "Never add a company, time period, result, achievement, proficiency, scope, responsibility, causal relation, or candidate action" in prompt


def test_public_cover_letter_contract_is_unchanged():
    schema = CoverLetterOut.model_json_schema()
    assert set(schema["properties"]) == {
        "letter", "used_profile_fact_ids", "language",
    }
    assert schema["properties"]["used_profile_fact_ids"]["maxItems"] == 4


def test_translation_branches_describe_complete_faithful_sentences():
    context = CoverLetterContext(
        candidate_evidence=[
            ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text="Разрабатывал API."),
            ConfirmedExperienceEvidence(id="experience_fact:2", confirmed_text="Developed APIs."),
        ],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language="en",
    )
    definitions = _provider_plan_model(context).model_json_schema()["$defs"]
    for name in ("ExperienceTranslatedSelection", "ExperienceOptionalSelection"):
        branch = definitions[name]
        assert set(branch["properties"]) == {"selection_kind", "fact_id", "translated_text"}
        assert "translated_text" in branch["required"]
        description = branch["properties"]["translated_text"]["description"]
        for phrase in ("complete grammatical standalone", "not a résumé/CV bullet",
                       "actor, tense, qualifiers, negations, uncertainty, limitations and scope",
                       "do not add factual meaning", "explicit pronoun is not mandatory"):
            assert phrase in description


@pytest.mark.parametrize("source,language,translation", [
    ("Разрабатывал REST API на FastAPI.", "en", "I developed REST APIs using FastAPI."),
    ("Работал с PostgreSQL при разработке backend-приложений.", "en",
     "I worked with PostgreSQL while developing backend applications."),
    ("Немного работал с Docker.", "en", "I worked with Docker a little."),
    ("Немного работал с Docker, только в учебных проектах.", "en",
     "I have worked a little with Docker, only in learning projects."),
    ("Помогал разрабатывать API.", "en", "I helped develop APIs."),
    ("Работал с API только в учебном проекте.", "en", "I worked with APIs only in a learning project."),
    ("Не настраивал production-сервисы.", "en", "I did not configure production services."),
    ("Возможно, использовал PostgreSQL в учебном проекте.", "en",
     "I may have used PostgreSQL in a learning project."),
    ("Команда разрабатывала API, я помогал с тестированием.", "en",
     "The team developed APIs, and I helped with testing."),
    ("Разрабатывал REST API.", "de", "Ich habe REST-APIs entwickelt."),
    ("Разрабатывал REST API.", "fr", "J’ai développé des API REST."),
    ("Разрабатывал REST API.", "es", "Desarrollé API REST."),
    ("Разрабатывал REST API на FastAPI.", "ru", None),
])
def test_complete_translation_fixtures_pass_through_service_unchanged(source, language, translation):
    # These fixtures verify transport/preservation, not real provider fidelity.
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text=source)],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Developer"}],
        matching={}, language=language,
    )
    translations = [] if translation is None else [
        ExperienceTranslation(fact_id="experience_fact:1", translated_text=translation),
    ]
    calls = []
    result = service_with_plan(context, make_plan(context, ["experience_fact:1"],
        translations=translations, closing="none"), calls=calls).generate(context)
    assert result.letter.endswith(translation or source)
    assert result.used_profile_fact_ids == ["experience_fact:1"]
    assert len(calls) == 1 and calls[0]["store"] is False
    prompt = calls[0]["input"][0]["content"]
    for instruction in (
        "except for the narrowly permitted translation",
        "complete grammatical standalone sentence",
        "not a résumé/CV bullet or sentence fragment",
        "Preserve actor, tense, qualifiers, negations, uncertainty, limitations and scope",
        "source supports the candidate as actor",
        "Do not require an explicit pronoun",
        "company/project attribution, stronger certainty or any new factual meaning",
    ):
        assert instruction in prompt


def test_renderer_preserves_practical_scope_and_location_is_only_preference():
    context = CoverLetterContext(
        candidate_evidence=[
            ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text="Немного работал с Docker"),
            LocationPreferenceEvidence(id="preferred_location_0", location="Yerevan"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Developer"},
            {"id": "vacancy:required_skill:0", "kind": "required_skill", "value": "Docker"},
        ],
        matching={}, language="ru",
    )
    result = render_cover_letter(context, make_plan(context, ["experience_fact:1", "preferred_location_0"]))
    assert "Немного работал с Docker" in result.letter
    assert "Yerevan" not in result.letter
    assert result.used_profile_fact_ids == ["experience_fact:1"]
    assert "живу" not in result.letter and "нахожусь" not in result.letter
    assert "production" not in result.letter and "результат" not in result.letter


def test_two_practical_facts_render_naturally_without_new_linkage():
    context = CoverLetterContext(
        candidate_evidence=[
            ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text="Разрабатывал REST API на FastAPI"),
            ConfirmedExperienceEvidence(id="experience_fact:2", confirmed_text="Интегрировал сторонние API"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Backend Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Разрабатывать API"},
        ],
        matching={}, language="ru",
    )
    result = render_cover_letter(context, make_plan(
        context, ["experience_fact:1", "experience_fact:2"], closing="none",
    ))
    assert "Из практического опыта" not in result.letter
    assert "Разрабатывал REST API на FastAPI. Интегрировал сторонние API." in result.letter
    assert "компании" not in result.letter.casefold()


@pytest.mark.parametrize(
    "language,translated,old_phrases",
    [
        ("ru", None, ("релевантны подтверждённые сведения", "В опыте указаны", "Целевая роль")),
        ("en", "Integrated third-party APIs", ("confirmed details below", "work history lists", "Target role")),
        ("de", "Integrierte Drittanbieter-APIs", ("bestätigten Angaben", "beruflichen Werdegang", "Zielposition")),
        ("fr", "Intégration d’API tierces", ("éléments confirmés", "parcours professionnel", "Poste recherché")),
        ("es", "Integración de API de terceros", ("datos confirmados", "historial laboral", "Puesto objetivo")),
    ],
)
def test_five_languages_keep_equivalent_safe_semantics_with_natural_phrasing(language, translated, old_phrases):
    practical = ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text="Интегрировал сторонние API")
    work = WorkHistoryEvidence(
        id="work_experience:1", company="BrightOps", position="Backend Developer",
        engagement_kind="employment", start_year=2022, start_month=None,
        end_year=2024, end_month=None, is_current=False,
    )
    context = CoverLetterContext(
        candidate_evidence=[practical, work, ListedSkillEvidence(id="skill_0", skill="Python")],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Backend Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Build APIs"},
        ],
        matching={}, language=language,
    )
    translations = [] if translated is None else [
        ExperienceTranslation(fact_id=practical.id, translated_text=translated),
    ]
    plan = make_plan(
        context, [practical.id, work.id, "skill_0"],
        anchors=["vacancy:title", "vacancy:responsibility:0"],
        translations=translations, style="vacancy_connection", closing="none",
    )
    result = service_with_plan(context, plan).generate(context)
    assert (translated or practical.confirmed_text) in result.letter
    assert "Backend Developer" in result.letter
    assert all(value not in result.letter for value in ("BrightOps", "2022", "2024", "Python"))
    assert result.used_profile_fact_ids == [practical.id]
    assert not any(phrase.casefold() in result.letter.casefold() for phrase in old_phrases)
    assert "Build APIs" not in result.letter
    assert not any(term in result.letter.casefold() for term in ("production", "achievement", "результат"))


@pytest.mark.parametrize(
    "extra_vacancy_evidence",
    [
        None,
        {"id": "vacancy:location", "kind": "location", "value": "Yerevan"},
        {"id": "vacancy:seniority", "kind": "seniority", "value": "junior"},
        {"id": "vacancy:workplace", "kind": "workplace", "value": "remote"},
        {"id": "vacancy:employment_type", "kind": "employment_type", "value": "full-time"},
    ],
    ids=["title-only", "location", "seniority", "workplace", "employment-type"],
)
def test_weak_vacancy_context_renders_only_most_informative_selected_fact(extra_vacancy_evidence):
    vacancy_evidence = [{"id": "vacancy:title", "kind": "title", "value": "Python Developer"}]
    if extra_vacancy_evidence is not None:
        vacancy_evidence.append(extra_vacancy_evidence)
    context = CoverLetterContext(
        candidate_evidence=[
            TargetRoleEvidence(id="target_role_0", role="Python Developer"),
            ListedSkillEvidence(id="skill_0", skill="Python"),
            ProfileLevelEvidence(id="experience", level="junior"),
        ],
        vacancy_evidence=vacancy_evidence,
        matching={}, language="ru",
    )
    result = render_cover_letter(context, make_plan(
        context, ["target_role_0", "skill_0", "experience"], closing="learn_more",
    ))
    assert result.used_profile_fact_ids == ["skill_0"]
    assert "Python" in result.letter
    assert "Интересуют позиции" not in result.letter
    assert "уровень" not in result.letter.casefold()
    assert len(result.letter.split(". ")) <= 4


@pytest.mark.parametrize(
    "granular_evidence",
    [
        {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Разработка API"},
        {"id": "vacancy:required_skill:0", "kind": "required_skill", "value": "Python"},
    ],
    ids=["responsibility", "required-skill"],
)
def test_granular_vacancy_context_keeps_rich_rendering_available(granular_evidence):
    context = CoverLetterContext(
        candidate_evidence=[
            TargetRoleEvidence(id="target_role_0", role="Python Developer"),
            ListedSkillEvidence(id="skill_0", skill="Python"),
            ProfileLevelEvidence(id="experience", level="junior"),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Python Developer"},
            granular_evidence,
        ],
        matching={}, language="ru",
    )
    result = render_cover_letter(context, make_plan(
        context, ["target_role_0", "skill_0", "experience"], closing="none",
    ))
    assert result.used_profile_fact_ids == ["skill_0"]
    assert "Рассматриваю позиции" not in result.letter
    assert "Среди моих навыков Python." in result.letter
    assert "уровень опыта" not in result.letter


@pytest.mark.parametrize(
    "language,expected,old_label",
    [
        ("ru", "Свой уровень опыта оцениваю как junior.", "Указанный уровень профиля"),
        ("en", "I describe my experience level as junior.", "Stated profile level"),
        ("de", "Ich schätze mein Erfahrungsniveau als junior ein.", "Angegebenes Profilniveau"),
        ("fr", "J’estime mon niveau d’expérience à junior.", "Niveau de profil indiqué"),
        ("es", "Considero que mi nivel de experiencia es junior.", "Nivel de perfil indicado"),
    ],
)
def test_profile_level_uses_natural_self_reported_wording(language, expected, old_label):
    context = CoverLetterContext(
        candidate_evidence=[ProfileLevelEvidence(id="experience", level="junior")],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Build APIs"},
        ],
        matching={}, language=language,
    )
    result = render_cover_letter(context, make_plan(context, ["experience"], closing="none"))
    assert expected in result.letter
    assert old_label not in result.letter
    assert not any(term in result.letter.casefold() for term in ("years", "commercial", "production", "лет опыта", "коммерчес"))


@pytest.mark.parametrize(
    "language,evidence,expected",
    [
        ("ru", LocationPreferenceEvidence(id="location", location="Yerevan"), "Рассматриваю работу в Yerevan."),
        ("en", LocationPreferenceEvidence(id="location", location="Yerevan"), "I am considering roles in Yerevan."),
        ("de", WorkplacePreferenceEvidence(id="workplace", workplace="remote"), "Ich ziehe Remote-Arbeit in Betracht."),
        ("fr", WorkplacePreferenceEvidence(id="workplace", workplace="remote"), "J’envisage un mode de travail à distance."),
        ("es", LanguageEvidence(id="language:English", language_name="English", level="B2"), "Mi nivel de English es B2."),
    ],
)
def test_preferences_and_languages_use_natural_wording(language, evidence, expected):
    context = CoverLetterContext(
        candidate_evidence=[evidence],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Build APIs"},
        ],
        matching={}, language=language,
    )
    result = render_cover_letter(context, make_plan(context, [evidence.id], closing="none"))
    assert expected in result.letter


def test_ru_system_templates_are_gender_neutral_and_closing_has_no_invented_action():
    _, _, context = setup_context(title="Python Developer")
    result = render_cover_letter(context, make_plan(context, ["skill_0"], closing="learn_more"))
    for forbidden in ("работал", "работала", "готов", "готова", "рад", "рада", "знаком", "знакома", "(а)", "созвон", "интервью", "тестовое", "помочь"):
        assert forbidden not in result.letter.casefold()
    assert "Хотелось бы узнать подробнее" in result.letter


def test_renderer_respects_telegram_utf16_limit_and_fails_closed_beyond_it():
    context = CoverLetterContext(
        candidate_evidence=[ConfirmedExperienceEvidence(id=f"experience_fact:{i}", confirmed_text="😀" * 500) for i in range(3)],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Developer"},
            {"id": "vacancy:responsibility:0", "kind": "responsibility", "value": "Build APIs"},
        ],
        matching={}, language="ru",
    )
    plan = make_plan(context, [f"experience_fact:{i}" for i in range(3)])
    result = render_cover_letter(context, plan)
    assert len(result.letter.encode("utf-16-le")) // 2 <= 3500

    context.candidate_evidence[0].confirmed_text = "😀" * 1300
    with pytest.raises(CoverLetterError) as caught:
        render_cover_letter(context, plan)
    assert caught.value.reason == "invalid_too_long_utf16"


@pytest.mark.parametrize("language,opening,closing", [
    ("ru", "Здравствуйте! Меня заинтересовала ваша вакансия.", "Хотелось бы узнать подробнее о задачах."),
    ("en", "Hello! I'm interested in your open role.", "I'd like to learn more about the responsibilities."),
    ("de", "Guten Tag! Ich interessiere mich für Ihre offene Stelle.", "Ich würde gern mehr über die Aufgaben erfahren."),
    ("fr", "Bonjour ! Je m’intéresse à votre poste ouvert.", "J’aimerais en savoir plus sur les missions."),
    ("es", "Hola. Me interesa su puesto abierto.", "Me gustaría saber más sobre las tareas."),
])
def test_intent_permissions_are_independent_and_do_not_change_evidence(language, opening, closing):
    context = CoverLetterContext(
        candidate_evidence=[ListedSkillEvidence(id="skill_0", skill="Python")],
        vacancy_evidence=[{"id": "vacancy:title", "kind": "title", "value": "Noisy | title"}],
        matching={}, language=language,
    )
    plan = make_plan(context, ["skill_0"], closing="learn_more")
    for interest in (False, True):
        for learn in (False, True):
            result = render_cover_letter(context, plan, intent_policy=DraftIntentPolicy(
                express_role_interest=interest, express_desire_to_learn_about_tasks=learn,
            ))
            assert result.letter.startswith(opening) is interest
            assert (closing in result.letter) is learn
            assert result.used_profile_fact_ids == ["skill_0"]
            assert "Python" in result.letter
    no_closing = render_cover_letter(context, make_plan(context, ["skill_0"], closing="none"))
    assert closing not in no_closing.letter


@pytest.mark.parametrize("text", [
    "Немного работал с Docker.", "Помогал разрабатывать API.",
    "Изучал FastAPI, но ещё не применял.",
    "Работал с API только в учебном проекте.", "Не занимался настройкой production.",
])
def test_intent_frame_preserves_practical_limits_without_join_or_rewrite(text):
    context = CoverLetterContext(
        candidate_evidence=[
            ConfirmedExperienceEvidence(id="experience_fact:1", confirmed_text=text),
            ConfirmedExperienceEvidence(id="experience_fact:2", confirmed_text="Разрабатывал REST API на FastAPI."),
        ],
        vacancy_evidence=[
            {"id": "vacancy:title", "kind": "title", "value": "Backend Developer"},
            {"id": "vacancy:required_skill:0", "kind": "required_skill", "value": "FastAPI"},
        ], matching={}, language="ru",
    )
    result = render_cover_letter(context, make_plan(context, ["experience_fact:1", "experience_fact:2"]))
    assert f"{text} Разрабатывал REST API на FastAPI." in result.letter
    assert result.used_profile_fact_ids == ["experience_fact:1", "experience_fact:2"]
    assert "У меня есть" not in result.letter


@pytest.mark.parametrize("facts,reason", [
    (["skill_0", "skill_0"], "invalid_duplicate_fact_id"),
    (["invented_project"], "invalid_unknown_fact_id"),
])
def test_resolved_plan_candidate_id_validation_remains_defensive(facts, reason):
    _, _, context = setup_context()
    with pytest.raises(CoverLetterError) as caught:
        _validate_plan(context, make_plan(context, facts))
    assert caught.value.reason == reason


def test_unknown_vacancy_anchor_is_rejected():
    _, _, context = setup_context()
    plan = make_plan(context, anchors=["vacancy:invented"])
    service = service_with_plan(context, plan)
    with pytest.raises(CoverLetterError) as caught:
        service.generate(context)
    assert caught.value.reason == "invalid_unknown_vacancy_anchor"


def test_long_raw_vacancy_text_cannot_be_used_as_rendered_anchor():
    _, _, context = setup_context(description="Employer-controlled long description")
    assert next(item for item in context.vacancy_evidence if item.id == "vacancy:description").anchorable is False
    plan = make_plan(context, anchors=["vacancy:description"])
    with pytest.raises(CoverLetterError) as caught:
        service_with_plan(context, plan).generate(context)
    assert caught.value.reason == "invalid_unsupported_plan"


@pytest.mark.parametrize("changes,reason", [
    ({"selected_evidence": [
        {"selection_kind": "candidate_fact", "fact_id": "skill_0"},
        {"selection_kind": "candidate_fact", "fact_id": "skill_1"},
        {"selection_kind": "candidate_fact", "fact_id": "skill_2"},
        {"selection_kind": "candidate_fact", "fact_id": "skill_0"},
    ]}, "invalid_fact_count"),
    ({"composition_style": "invented"}, "invalid_unsupported_plan"),
    ({"candidate_sentence": "PRIVATE LETTER"}, "invalid_schema"),
])
def test_schema_failures_are_classified(changes, reason, caplog):
    _, _, context = setup_context()

    class Responses:
        def parse(self, **kwargs):
            payload = _provider_payload([
                {"selection_kind": "candidate_fact", "fact_id": "skill_0"},
            ])
            payload.update(changes)
            return SimpleNamespace(output_parsed=kwargs["text_format"].model_validate(payload))

    with caplog.at_level(logging.INFO, logger="uvicorn.error"), pytest.raises(CoverLetterError):
        CoverLetterGenerationService(client=SimpleNamespace(responses=Responses())).generate(context)
    assert f"reason={reason}" in caplog.text
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("failure,code,reason", [
    ("missing", "cover_letter_invalid_output", "invalid_missing_parsed_output"),
    ("incomplete", "cover_letter_invalid_output", "invalid_incomplete_response"),
    ("timeout", "cover_letter_ai_timeout", None),
    ("provider", "cover_letter_ai_provider_error", None),
])
def test_provider_failures_log_safe_telemetry(failure, code, reason, caplog):
    _, _, context = setup_context(description="PRIVATE VACANCY")

    class Responses:
        def parse(self, **kwargs):
            if failure == "timeout":
                raise APITimeoutError(request=httpx.Request("POST", "https://example.com"))
            if failure == "provider":
                raise RuntimeError("PRIVATE PROMPT")
            return SimpleNamespace(
                id="resp_safe", usage=SimpleNamespace(input_tokens=12, output_tokens=3, total_tokens=15),
                status="incomplete" if failure == "incomplete" else "completed", output_parsed=None,
            )

    service = CoverLetterGenerationService(model="safe-model", client=SimpleNamespace(responses=Responses()))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), pytest.raises(CoverLetterError, match=code):
        service.generate(context)
    message = next(record.getMessage() for record in caplog.records if "event=cover_letter_generation" in record.getMessage())
    assert f"result={code}" in message and "model=safe-model" in message and "duration_seconds=" in message
    if reason:
        assert f"reason={reason}" in message and "response_id=resp_safe" in message and "total_tokens=15" in message
    assert "PRIVATE" not in message


def test_service_logs_safe_success_telemetry(caplog):
    _, _, context = setup_context(description="PRIVATE VACANCY")
    usage = SimpleNamespace(input_tokens=123, output_tokens=45, total_tokens=168)
    service = service_with_plan(context, make_plan(context), response_id="resp_cover_letter_safe", usage=usage)
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = service.generate(context)
    message = next(record.getMessage() for record in caplog.records if "event=cover_letter_generation" in record.getMessage())
    assert "result=success" in message and "response_id=resp_cover_letter_safe" in message
    assert "input_tokens=123" in message and "output_tokens=45" in message and "total_tokens=168" in message
    assert "PRIVATE VACANCY" not in message and result.letter not in message


def test_ownership_missing_profile_and_empty_vacancy():
    owner, app_id, _ = setup_context()
    other = create_user(901)
    for user_id, application_id in ((other, app_id), (owner, 99999)):
        response = client.post(f"/users/{user_id}/applications/{application_id}/cover-letter", json={"language": "ru"})
        assert response.status_code == 404
    user_without_profile = create_user(902)
    empty_id, _ = create_application(user_without_profile, title=None, required_skills=[])
    url = f"/users/{user_without_profile}/applications/{empty_id}/cover-letter"
    assert client.post(url, json={"language": "ru"}).status_code == 409
    put_profile(user_without_profile)
    assert client.post(url, json={"language": "ru"}).status_code == 422


@pytest.mark.parametrize("language", ["ru", "en", "de", "fr", "es"])
def test_generation_endpoint_accepts_exactly_supported_languages(monkeypatch, language):
    owner, app_id, _ = setup_context()

    class Service:
        def generate(self, context):
            return {"letter": "Safe", "used_profile_fact_ids": [context.candidate_evidence[0].id], "language": context.language}

    monkeypatch.setattr(main_module, "cover_letter_service", Service())
    response = client.post(f"/users/{owner}/applications/{app_id}/cover-letter", json={"language": language})
    assert response.status_code == 200 and response.json()["language"] == language


@pytest.mark.parametrize("language", ["it", "hy", "pt", "sw", "ja", "zz", "German"])
def test_generation_endpoint_rejects_unsupported_language_before_provider(monkeypatch, language):
    owner, app_id, _ = setup_context()

    class Service:
        def generate(self, context):
            pytest.fail("Unsupported language must not reach generation")

    monkeypatch.setattr(main_module, "cover_letter_service", Service())
    assert client.post(f"/users/{owner}/applications/{app_id}/cover-letter", json={"language": language}).status_code == 422


@pytest.mark.parametrize("text,expected", [("Russian", "ru"), ("English", "en"), ("Deutsch", "de"), ("Français", "fr"), ("Español", "es")])
def test_language_normalization_only_accepts_supported_languages(text, expected):
    response = client.post("/cover-letter/language", json={"text": text})
    assert response.status_code == 200 and response.json() == {"language": expected}


@pytest.mark.parametrize("text", ["Italiano", "Português", "Հայերեն", "Polski", "Swahili", "JA", "blahblah"])
def test_language_normalization_rejects_custom_languages(text):
    assert client.post("/cover-letter/language", json={"text": text}).status_code == 422


def test_confirmed_experience_keeps_stable_id_after_edit():
    owner, app_id, _ = setup_context()
    fact = client.post(f"/users/{owner}/profile/experience-facts", json={"text": "Немного работал с Docker"}).json()
    with TestSessionLocal() as session:
        before = build_context(session, owner, app_id, "ru")
    evidence = next(item for item in before.candidate_evidence if item.kind == "confirmed_experience")
    assert evidence.id == f"experience_fact:{fact['id']}" and evidence.confirmed_text == "Немного работал с Docker"
    client.put(f"/users/{owner}/profile/experience-facts/{fact['id']}", json={"text": "Настраивал Docker для локального запуска сервисов"})
    with TestSessionLocal() as session:
        after = build_context(session, owner, app_id, "ru")
    edited = next(item for item in after.candidate_evidence if item.kind == "confirmed_experience")
    assert edited.id == evidence.id and edited.confirmed_text == "Настраивал Docker для локального запуска сервисов"


def test_provider_runs_without_open_transaction(monkeypatch):
    owner, app_id, _ = setup_context()
    with TestSessionLocal() as session:
        class Service:
            def generate(self, context):
                assert not session.in_transaction()
                return "probe"
        monkeypatch.setattr(main_module, "cover_letter_service", Service())
        assert main_module.generate_cover_letter(owner, app_id, CoverLetterRequest(language="ru"), session) == "probe"


@pytest.mark.parametrize("code,status", [("cover_letter_ai_timeout", 504), ("cover_letter_ai_unavailable", 503), ("cover_letter_invalid_output", 502)])
def test_endpoint_error_contract_is_unchanged(monkeypatch, code, status):
    owner, app_id, _ = setup_context()
    monkeypatch.setattr(main_module, "cover_letter_service", SimpleNamespace(generate=lambda context: (_ for _ in ()).throw(CoverLetterError(code))))
    response = client.post(f"/users/{owner}/applications/{app_id}/cover-letter", json={"language": "ru"})
    assert response.status_code == status and response.json()["detail"]["code"] == code
