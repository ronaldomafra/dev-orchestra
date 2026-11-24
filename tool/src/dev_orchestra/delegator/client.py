"""Cliente HTTP do delegador para o lado do host (TUI, CLI e listener). Só biblioteca padrão."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import DEFAULT_URL


class DelegatorUnavailable(Exception):
    """O serviço não respondeu."""


class DelegatorClient:
    def __init__(self, url: str = DEFAULT_URL, *, timeout: float = 3.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _call(self, path: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
        full = self.url + path
        if params:
            full += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(full, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - loopback
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("error", str(exc))
            except (ValueError, OSError):
                detail = str(exc)
            raise RuntimeError(detail) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise DelegatorUnavailable(str(exc)) from exc

    def health(self) -> bool:
        try:
            return bool(self._call("/healthz").get("ok"))
        except (DelegatorUnavailable, RuntimeError, ValueError):
            return False

    def pending(self, project: str, agent: str) -> dict[str, Any]:
        return self._call("/api/pending", {"project": project, "agent": agent})

    def status(self, project: str) -> dict[str, Any]:
        return self._call("/api/status", {"project": project})

    def events(self, project: str, limit: int = 30) -> list[dict[str, Any]]:
        return self._call("/api/events", {"project": project, "limit": limit}).get("events", [])

    def timeline(self, project: str | None = None, limit: int = 200, after: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "after": after}
        if project:
            params["project"] = project
        return self._call("/api/timeline", params).get("events", [])

    def message(self, project: str, message_id: int) -> dict[str, Any]:
        return self._call("/api/message", {"project": project, "id": message_id})

    def task_history(self, project: str, task_id: str) -> list[dict[str, Any]]:
        return self._call("/api/task", {"project": project, "task": task_id}).get("history", [])

    def stats(self, project: str | None = None) -> dict[str, Any]:
        return self._call("/api/stats", {"project": project} if project else None)

    def signal(self, project: str, agent: str, kind: str, message: str = "", detail: str = "") -> dict[str, Any]:
        body = {"project": project, "agent": agent, "kind": kind, "message": message, "detail": detail}
        return self._call("/api/signal", body=body)

    def clear_signals(self, project: str, agent: str) -> dict[str, Any]:
        return self._call("/api/signal/clear", body={"project": project, "agent": agent})

    def alerts(self, project: str | None = None) -> list[dict[str, Any]]:
        return self._call("/api/alerts", {"project": project} if project else None).get("alerts", [])

    def cancel(self, project: str, task_id: str, reason: str = "") -> dict[str, Any]:
        return self._call("/api/cancel", body={"project": project, "task_id": task_id, "reason": reason})

    def push_profile(self, project: str, role: str) -> dict[str, Any]:
        return self._call("/api/profile", body={"project": project, "role": role})

    # ------------------------------------------------------------ agentes passivos

    def passive_heartbeat(self, name: str, pid: int | None, agents: list[str]) -> dict[str, Any]:
        return self._call("/api/passive/heartbeat", body={"name": name, "pid": pid, "agents": agents})

    def passive_pending(self) -> list[dict[str, Any]]:
        return self._call("/api/passive/pending").get("consults", [])

    def passive_claim(self, consult_id: int, runner: str = "") -> dict[str, Any]:
        return self._call("/api/passive/claim", body={"id": consult_id, "runner": runner})

    def passive_finish(self, consult_id: int, state: str, **fields: Any) -> dict[str, Any]:
        return self._call("/api/passive/finish", body={"id": consult_id, "state": state, **fields})

    def passive_requeue(self, minutes: float = 30.0) -> int:
        return int(self._call("/api/passive/requeue", body={"minutes": minutes}).get("requeued", 0))

    def passive_overview(self, today: str | None = None, week: str | None = None) -> dict[str, Any]:
        params = {k: v for k, v in {"today": today, "week": week}.items() if v}
        return self._call("/api/passive/overview", params or None)

    def passive_consults(self, agent: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if agent:
            params["agent"] = agent
        return self._call("/api/passive/consults", params).get("consults", [])

    def passive_consult(self, consult_id: int) -> dict[str, Any]:
        return self._call("/api/passive/consult", {"id": consult_id})

    def passive_timeline(self, agent: str, limit: int = 40) -> list[dict[str, Any]]:
        return self._call("/api/passive/timeline", {"agent": agent, "limit": limit}).get("events", [])

    def passive_cancel(self, consult_id: int) -> dict[str, Any]:
        return self._call("/api/passive/cancel", body={"id": consult_id})
