"""Permanent privacy policy for diagnostics from the API's PDF dependency.

pypdf's logger_warning/logger_error use the calling module's __name__, not
just the parent 'pypdf' logger. Parent filters do not filter child records.
These names were audited against the installed dependency; a regression checks
the inventory on upgrades. Reject complete records before any handler sees
message arguments, exception/stack text or arbitrary extra fields.
"""
import logging

PYPDF_DIAGNOSTIC_LOGGERS = (
    "pypdf",
    "pypdf._cmap",
    "pypdf._codecs._codecs",
    "pypdf._crypt_providers._cryptography",
    "pypdf._crypt_providers._pycryptodome",
    "pypdf._doc_common",
    "pypdf._encryption",
    "pypdf._font",
    "pypdf._page",
    "pypdf._page_labels",
    "pypdf._reader",
    "pypdf._text_extraction._layout_mode._fixed_width_page",
    "pypdf._utils",
    "pypdf._writer",
    "pypdf.annotations._non_markup_annotations",
    "pypdf.filters",
    "pypdf.generic._appearance_stream",
    "pypdf.generic._base",
    "pypdf.generic._data_structures",
    "pypdf.generic._image_inline",
    "pypdf.generic._image_xobject",
    "pypdf.generic._link",
    "pypdf.generic._utils",
)


class _SuppressPDFDiagnostics(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


def install_pdf_logging_policy() -> None:
    """Install once at module initialization, never around an individual parse."""
    for name in PYPDF_DIAGNOSTIC_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(item, _SuppressPDFDiagnostics) for item in logger.filters):
            logger.addFilter(_SuppressPDFDiagnostics())
