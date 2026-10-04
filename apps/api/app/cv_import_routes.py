import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth_dependencies import require_principal
from app.cv_import_schemas import ImportAction, ImportEdit, ImportPreview
from app.database import get_session
from app.services.auth import Principal
from app.services.cv_import import apply_import, edit_import, prepare_preview
from app.services.cv_import_state import CVImportError, get_import_store
from app.services.cv_profile_draft import CVProfileDraftError, MAX_UPLOAD_BYTES, create_profile_draft_from_cv

router = APIRouter(prefix="/users/{user_id}/profile/cv-import")


def _safe_error(error):
    if isinstance(error, CVImportError):
        return HTTPException(error.status, detail={"code": error.code, **({"fieldErrors": error.field_errors} if error.field_errors else {})})
    return HTTPException(503, detail={"code": "cv_import_unavailable"})


def _edit_validation_error(error: ValidationError):
    fields = {"company", "position", "engagement_kind", "start_year", "start_month", "end_year", "end_month", "is_current", "text", "target_roles", "skills", "location", "experience", "workplace_preference", "salary_min", "salary_currency", "salary_period", "languages"}
    messages = {}
    for item in error.errors(include_input=False):
        field = next((str(part) for part in item["loc"] if part in fields), "dates" if "work" in item["loc"] else "edit")
        if field == "dates" and str(item.get("ctx", {}).get("error", "")) == "company or position is required":
            messages["company"] = "Укажите компанию / проект или должность."
            continue
        messages[field] = "Проверьте даты: порядок, год и месяц должны быть корректными." if field == "dates" or field in {"start_year", "start_month", "end_year", "end_month"} else "Проверьте значение и допустимую длину поля."
    return HTTPException(422, detail={"code": "cv_import_edit_invalid", "fieldErrors": messages})


@router.patch("/preview", response_model=ImportPreview)
async def edit_preview(user_id: int, request: Request, principal: Principal = Depends(require_principal)):
    try:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 64 * 1024:
                raise CVImportError("cv_import_edit_invalid", 413)
            body.extend(chunk)
        edit = ImportEdit.model_validate_json(bytes(body))
        return await run_in_threadpool(edit_import, user_id, principal.session_hash, edit, get_import_store())
    except ValidationError as error:
        raise _edit_validation_error(error) from None
    except (CVImportError, sqlite3.Error, OSError) as error:
        raise _safe_error(error) from None


@router.post("", response_model=ImportPreview)
async def extract_import(
    user_id: int, request: Request, file: UploadFile = File(...),
    principal: Principal = Depends(require_principal), session: Session = Depends(get_session),
):
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if not content:
            raise CVImportError("cv_file_empty", 422)
        draft = await run_in_threadpool(
            create_profile_draft_from_cv, session, user_id, filename=file.filename,
            content_type=file.content_type, content=content,
            ai_service=request.app.state.cv_import_ai_service,
            work_experience_snapshot=True,
            experience_facts_snapshot=True,
        )
        return await run_in_threadpool(prepare_preview, session, user_id, principal.session_hash, draft, get_import_store())
    except CVProfileDraftError as error:
        statuses = {"file_too_large": 413, "unsupported_file_type": 415, "user_not_found": 404,
                    "ai_unavailable": 503, "ai_timeout": 504, "ai_provider_error": 502, "invalid_ai_output": 502}
        raise HTTPException(statuses.get(error.code, 422), detail={"code": error.code}) from None
    except (CVImportError, sqlite3.Error, OSError) as error:
        raise _safe_error(error) from None
    finally:
        await file.close()


@router.post("/apply")
def apply(user_id: int, payload: ImportAction, principal: Principal = Depends(require_principal), session: Session = Depends(get_session)):
    try:
        apply_import(session, user_id, principal.session_hash, payload.token, get_import_store(), payload.revision)
    except (CVImportError, sqlite3.Error, OSError) as error:
        raise _safe_error(error) from None
    return {"ok": True}


@router.post("/cancel")
def cancel(user_id: int, payload: ImportAction, principal: Principal = Depends(require_principal)):
    try:
        get_import_store().cancel(payload.token, user_id, principal.session_hash)
    except (CVImportError, sqlite3.Error, OSError) as error:
        raise _safe_error(error) from None
    return {"ok": True}
