import ast
from io import BytesIO
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pypdf

from app.services import cv_profile_draft as cv
from app.services.cv_pdf_logging import PYPDF_DIAGNOSTIC_LOGGERS
from test_cv_profile_draft import make_pdf
from test_cv_profile_draft import make_docx
from app.services.cv_profile_draft import CVProfileDraftError
import pytest


def malformed_readable_pdf(marker="SYNTHETIC_PRIVATE_CV_MARKER"):
    return make_pdf("Python Engineer").replace(
        b"/Type /Catalog", f"({marker}) 1 /Type /Catalog".encode()
    )


def parse(content):
    return cv.extract_cv_text(filename="resume.pdf", content_type="application/pdf", content=content)


def assert_private_logs_absent(caplog, *markers):
    # Inspect the entire records as well as rendered output: not only getMessage.
    captured = caplog.text + repr([vars(record) for record in caplog.records])
    for marker in markers:
        assert marker not in captured
    assert not any(record.name.startswith("pypdf") for record in caplog.records)


def test_malformed_readable_pdf_keeps_text_without_private_diagnostics(caplog):
    with caplog.at_level(logging.DEBUG):
        assert parse(malformed_readable_pdf()) == "Python Engineer"
    assert_private_logs_absent(caplog, "SYNTHETIC_PRIVATE_CV_MARKER")


def test_overlapping_pdf_parsers_have_stable_privacy_policy(monkeypatch, caplog):
    gate = Barrier(2)
    reader = cv.PdfReader
    def overlapping_reader(*args, **kwargs):
        gate.wait(timeout=5)
        return reader(*args, **kwargs)
    monkeypatch.setattr(cv, "PdfReader", overlapping_reader)
    def configuration():
        return [(log.level, log.disabled, log.propagate, tuple(log.filters), tuple(log.handlers))
                for log in map(logging.getLogger, PYPDF_DIAGNOSTIC_LOGGERS)]
    before = configuration()
    markers = ("SYNTHETIC_PRIVATE_CV_MARKER_A", "SYNTHETIC_PRIVATE_CV_MARKER_B")
    with caplog.at_level(logging.DEBUG), ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(parse, map(malformed_readable_pdf, markers))) == ["Python Engineer"] * 2
    assert configuration() == before
    assert_private_logs_absent(caplog, *markers)


def test_complete_pdf_records_are_dropped_before_direct_handlers(caplog):
    records = []
    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)
    handler = Capture()
    with caplog.at_level(logging.DEBUG):
        for name in PYPDF_DIAGNOSTIC_LOGGERS:
            log = logging.getLogger(name)
            log.addHandler(handler)
            try:
                try:
                    raise ValueError("PRIVATE_EXCEPTION")
                except ValueError:
                    log.error("PRIVATE_MSG %s", "PRIVATE_ARG", exc_info=True, stack_info=True,
                              extra={"document": "PRIVATE_EXTRA"})
                log.debug("PRIVATE_DEBUG")
            finally:
                log.removeHandler(handler)
        logging.getLogger("app.services.auth").info("auth_operational_event")
        cv.timing_logger.info("cv_operational_event")
    assert records == []
    assert_private_logs_absent(caplog, "PRIVATE_MSG", "PRIVATE_ARG", "PRIVATE_EXCEPTION", "PRIVATE_EXTRA", "PRIVATE_DEBUG")
    assert "auth_operational_event" in caplog.text
    assert "cv_operational_event" in caplog.text


def test_installed_pypdf_diagnostic_logger_inventory_is_covered():
    root = Path(pypdf.__file__).parent
    discovered = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            helper = isinstance(node.func, ast.Name) and node.func.id in {"logger_warning", "logger_error"}
            direct = isinstance(node.func, ast.Attribute) and node.func.attr == "getLogger"
            if not (helper or direct):
                continue
            parts = path.relative_to(root).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            discovered.add(".".join(("pypdf", *parts)))
    assert discovered <= set(PYPDF_DIAGNOSTIC_LOGGERS)


def test_docx_xml_exception_does_not_log_document_fragment(caplog):
    source = BytesIO(make_docx(paragraph="Python Engineer"))
    result = BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(result, "w") as target:
        for item in original.infolist():
            content = (b'<SYNTHETIC_PRIVATE_DOCX_MARKER><unclosed' if item.filename == "word/document.xml"
                       else original.read(item.filename))
            target.writestr(item, content)
    with caplog.at_level(logging.DEBUG), pytest.raises(CVProfileDraftError) as error:
        cv.extract_cv_text(filename="cv.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", content=result.getvalue())
    assert error.value.code == "malformed_document"
    assert_private_logs_absent(caplog, "SYNTHETIC_PRIVATE_DOCX_MARKER", "<unclosed")
