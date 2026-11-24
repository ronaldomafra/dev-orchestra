"""Central de alertas do aplicativo: calcula os alertas, avisa (som e sistema) e mostra um aviso na TUI."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App

from ..core import alerts as al
from ..core import config

INTERVAL = 4.0


class AlertCenter:
    def __init__(self, app: App) -> None:
        self.app = app
        self.items: list[al.Alert] = []
        self.store = al.AlertStore()
        self.version = 0
        self._busy = False

    # ----------------------------------------------------------------- ciclo

    def start(self) -> None:
        self.app.set_interval(INTERVAL, self.tick)
        self.tick()

    def tick(self) -> None:
        if not self._busy:
            self.app.run_worker(self.refresh, exclusive=True, group="alerts")

    async def refresh(self) -> None:
        self._busy = True
        try:
            projects = config.list_projects()
            if not any(p.delegator for p in projects):
                if self.items:
                    self.items = []
                    self._changed()
                return
            cfg = config.load_global()
            services = getattr(self.app, "services", None)
            notifier = getattr(self.app, "notifier", None) or al.Notifier(cfg.alerts)
            self.store = self.store if self.store else al.AlertStore()
            alerts, fired = await asyncio.to_thread(al.refresh, projects, cfg, services, self.store, notifier)
            before = [(a.key, a.state, a.since) for a in self.items]
            self.items = alerts
            for alert, done in fired:
                self.toast(alert, done, cfg.alerts.sound)
            if before != [(a.key, a.state, a.since) for a in alerts] or fired:
                self._changed()
        except Exception:  # noqa: BLE001 - alertas nunca derrubam a TUI
            return
        finally:
            self._busy = False

    # --------------------------------------------------------------- consulta

    @property
    def active(self) -> list[al.Alert]:
        """O que ainda pede a atenção da pessoa (nem reconhecido nem silenciado)."""
        return [a for a in self.items if a.state in ("novo", "visto")]

    def top_severity(self) -> str | None:
        active = self.active
        return min((a.severity for a in active), key=lambda s: al.SEVERITY_ORDER[s]) if active else None

    def for_project(self, project_id: str) -> list[al.Alert]:
        return [a for a in self.active if a.project == project_id]

    def for_agent(self, project_id: str, agent: str) -> list[al.Alert]:
        return [a for a in self.for_project(project_id) if a.agent == agent]

    # ------------------------------------------------------------------ avisos

    def toast(self, alert: al.Alert, done: dict[str, Any], sound_on: bool) -> None:
        if sound_on and done.get("sound") is None and alert.severity == al.CRIT:
            try:
                self.app.bell()  # sem player de som: o sino do terminal
            except Exception:  # noqa: BLE001
                pass
        how = []
        if done.get("sound"):
            how.append("som")
        if done.get("os"):
            how.append("notificação do sistema")
        extra = f"\n({' + '.join(how)})" if how else ""
        where = f"{alert.project}" + (f" · {alert.task}" if alert.task else "")
        self.app.notify(
            f"{alert.agent}: {alert.message}\n{where}{extra}",
            title=f"{al.SEVERITY_ICON[alert.severity]} {alert.label}",
            severity="error" if alert.severity == al.CRIT else "warning",
            timeout=20 if alert.severity == al.CRIT else 10,
            markup=False,
        )

    def _changed(self) -> None:
        """Avisa a tela atual: barra de menus (sino), cartões e a própria central."""
        self.version += 1
        try:
            screen = self.app.screen
            for hook in ("on_alerts_changed",):
                fn = getattr(screen, hook, None)
                if fn:
                    fn()
            from .menus import MenuBar

            for bar in screen.query(MenuBar):
                bar.refresh_bar()
        except Exception:  # noqa: BLE001
            pass
