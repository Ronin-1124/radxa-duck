"""Explicit motion adapter boundary; no physical controller is assumed."""

from __future__ import annotations

import re


class ActionError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ActionService:
    """An injected controller must acknowledge completion and honor cancellation.

    Action IDs come from the controller's whitelist, never from shell commands
    or automatically executed model output. No controller is configured yet.
    """

    def __init__(self, adapter=None):
        self._adapter = adapter

    def snapshot(self):
        if self._adapter is None:
            return {"schema_version": 1, "available": False, "reason": "controller_unavailable", "actions": []}
        try:
            raw = self._adapter.list_actions()
            if not isinstance(raw, list) or len(raw) > 64:
                raise ValueError()
            actions, seen = [], set()
            for action in raw:
                identifier, label = action["id"], action["label"]
                if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier)
                        or identifier in seen or not isinstance(label, str) or not 1 <= len(label) <= 80
                        or any(ord(c) < 32 for c in label)):
                    raise ValueError()
                seen.add(identifier)
                actions.append({"id": identifier, "label": label})
            return {"schema_version": 1, "available": bool(actions),
                    "reason": None if actions else "actions_unavailable", "actions": actions}
        except Exception:
            return {"schema_version": 1, "available": False, "reason": "controller_unavailable", "actions": []}

    def execute(self, action_id, *, cancelled):
        status = self.snapshot()
        if not status["available"]:
            raise ActionError("controller_unavailable", "The motion controller is not connected")
        if action_id not in {action["id"] for action in status["actions"]}:
            raise ActionError("invalid_request", "The requested action is not advertised by the controller")
        if cancelled.is_set():
            raise ActionError("cancelled", "The action was cancelled")
        try:
            result = self._adapter.execute(action_id, cancelled=cancelled)
        except Exception:
            raise ActionError("action_failed", "The controller did not confirm the action") from None
        if cancelled.is_set():
            raise ActionError("cancelled", "The action was cancelled")
        if not isinstance(result, dict) or result.get("state") != "succeeded" or result.get("action_id") != action_id:
            raise ActionError("action_failed", "The controller did not confirm action completion")
        return {"action_id": action_id, "state": "succeeded"}
