"""Make Hikari-core's loguru records fit AstrBot's log format.

AstrBot formats every loguru record with extra fields (plugin_tag, ...) that it
only adds to its own patched logger instance. Hikari logs through the raw loguru
logger, so its records miss them and every line fails with KeyError. A global
patcher fills them in for Hikari's records only and redacts registered secrets.
"""

from __future__ import annotations

from loguru import logger

try:
    from astrbot.core.log import _patch_record as _astrbot_patch_record
except Exception:  # pragma: no cover - older/newer AstrBot layouts
    _astrbot_patch_record = None

PLUGIN_TAG = "[astrbot_plugin_wows]"
_secrets: set[str] = set()


def _patch(record) -> None:
    for secret in _secrets:
        record["message"] = record["message"].replace(secret, "[REDACTED]")
    if not (record["name"] or "").startswith("hikari_core"):
        return
    record["extra"].setdefault("plugin_tag", PLUGIN_TAG)
    if _astrbot_patch_record is not None:
        _astrbot_patch_record(record)


def install(secret: str | None = None) -> None:
    if secret:
        _secrets.add(secret)
    logger.configure(patcher=_patch)


def uninstall() -> None:
    _secrets.clear()
    logger.configure(patcher=None)
