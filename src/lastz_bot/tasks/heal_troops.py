"""Automatiza la rutina de curación, reclamo y solicitud de ayuda para tropas."""

from __future__ import annotations

from typing import Any, List, Sequence

from .base import TaskContext
from .utils import tap_back_button


class HealTroopsTask:
    """Ejecuta la rotación completa de curar tropas y pedir help en la alianza."""
    name = "heal_troops"

    def run(self, ctx: TaskContext, params: dict[str, Any]) -> None:  # type: ignore[override]
        """Recoge tropas curadas, inicia nuevas curaciones y solicita ayuda."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; 'heal_troops' requiere detecciones")
            return

        world_button_paths = self._template_paths(ctx, params.get("world_button_template") or "world_button")
        sede_button_paths = self._template_paths(ctx, params.get("sede_button_template") or "sede_button")
        healing_icon_paths = self._template_paths(ctx, params.get("healing_icon_template") or "healing_icon")
        handshake_icon_paths = self._template_paths(
            ctx, params.get("handshake_icon_template") or "healing_icon_handshake"
        )
        troops_icon_paths = self._template_paths(
            ctx, params.get("troops_icon_template") or "healing_icon_troops"
        )
        healing_button_paths = self._template_paths(
            ctx, params.get("healing_button_template") or "healing_button"
        )

        if not world_button_paths or not sede_button_paths:
            ctx.console.log("[warning] Faltan templates de navegación (world/sede) para 'heal_troops'")
            return

        world_threshold = float(params.get("world_button_threshold", 0.7))
        world_timeout = float(params.get("world_button_timeout", 6.0))
        sede_threshold = float(params.get("sede_button_threshold", 0.75))
        icon_threshold = float(params.get("icon_threshold", 0.82))
        handshake_threshold = float(params.get("handshake_threshold", icon_threshold))
        button_threshold = float(params.get("healing_button_threshold", 0.82))

        world_transition_delay = float(params.get("world_transition_delay", 3.0))
        collect_delay = float(params.get("collect_delay", 2.0))
        panel_delay = float(params.get("panel_delay", 2.0))
        heal_action_delay = float(params.get("heal_action_delay", 2.0))
        post_return_delay = float(params.get("post_return_delay", 2.0))
        handshake_delay = float(params.get("handshake_delay", 1.5))

        icon_timeout = float(params.get("icon_timeout", 6.0))
        collect_timeout = float(params.get("collect_timeout", icon_timeout))
        handshake_timeout = float(params.get("handshake_timeout", icon_timeout))
        button_timeout = float(params.get("healing_button_timeout", 5.0))

        max_collect_cycles = max(1, int(params.get("max_collect_cycles", 2)))

        if not self._ensure_world_scene(
            ctx,
            world_button_paths,
            world_threshold,
            world_timeout,
            sede_button_paths,
            sede_threshold,
            world_transition_delay,
        ):
            return

        self._collect_ready_troops(
            ctx,
            troops_icon_paths,
            icon_threshold,
            collect_timeout,
            collect_delay,
            max_collect_cycles,
        )

        healed = self._perform_heal(
            ctx,
            healing_icon_paths,
            icon_threshold,
            icon_timeout,
            panel_delay,
            healing_button_paths,
            button_threshold,
            button_timeout,
            heal_action_delay,
            post_return_delay,
        )

        self._request_help(
            ctx,
            handshake_icon_paths,
            handshake_threshold,
            handshake_timeout,
            handshake_delay,
            expect_help=healed,
        )

        self._return_home(
            ctx,
            sede_button_paths,
            sede_threshold,
            world_timeout,
        )

    def _ensure_world_scene(
        self,
        ctx: TaskContext,
        world_paths: Sequence[Any],
        world_threshold: float,
        world_timeout: float,
        sede_paths: Sequence[Any],
        sede_threshold: float,
        transition_delay: float,
    ) -> bool:
        """Asegura que la cámara esté en el mapa mundial detectando world/sede."""
        if self._wait_template(ctx, sede_paths, sede_threshold, timeout=2.0, label="sede-check"):
            return True

        if not self._tap_template(
            ctx,
            world_paths,
            world_threshold,
            timeout=world_timeout,
            label="world-button",
            delay=transition_delay,
        ):
            ctx.console.log("[warning] No se pudo abrir el mapa del mundo para curar tropas")
            return False
        return self._wait_template(
            ctx,
            sede_paths,
            sede_threshold,
            timeout=world_timeout,
            label="sede-check",
        )

    def _collect_ready_troops(
        self,
        ctx: TaskContext,
        troops_paths: Sequence[Any],
        threshold: float,
        timeout: float,
        delay: float,
        max_cycles: int,
    ) -> None:
        """Pulsa repetidamente el ícono de tropas listas para recoger curaciones previas."""
        if not troops_paths:
            return
        cycles = 0
        while cycles < max_cycles:
            tapped = self._tap_template(
                ctx,
                troops_paths,
                threshold,
                timeout=timeout,
                label="healing-collect",
                delay=delay,
            )
            if not tapped:
                break
            ctx.console.log("Tropas curadas recogidas desde el mapa")
            cycles += 1

    def _perform_heal(
        self,
        ctx: TaskContext,
        icon_paths: Sequence[Any],
        icon_threshold: float,
        icon_timeout: float,
        panel_delay: float,
        button_paths: Sequence[Any],
        button_threshold: float,
        button_timeout: float,
        heal_delay: float,
        return_delay: float,
    ) -> bool:
        """Abre el panel de hospital y pulsa el botón principal de curación."""
        if not icon_paths or not button_paths:
            return False
        if not self._tap_template(
            ctx,
            icon_paths,
            icon_threshold,
            timeout=icon_timeout,
            label="healing-icon",
            delay=panel_delay,
        ):
            ctx.console.log("[info] No se encontró ícono de curación en el mapa")
            return False
        if not self._tap_template(
            ctx,
            button_paths,
            button_threshold,
            timeout=button_timeout,
            label="healing-button",
            delay=heal_delay,
        ):
            ctx.console.log("[warning] No se pudo pulsar el botón de curación")
            return False
        if return_delay > 0:
            ctx.device.sleep(return_delay)
        ctx.console.log("Curación de tropas iniciada")
        return True

    def _request_help(
        self,
        ctx: TaskContext,
        handshake_paths: Sequence[Any],
        threshold: float,
        timeout: float,
        delay: float,
        *,
        expect_help: bool,
    ) -> None:
        """Toca el ícono de handshake para pedir help; loguea si faltó tras curar."""
        if not handshake_paths:
            return
        tapped = self._tap_template(
            ctx,
            handshake_paths,
            threshold,
            timeout=timeout,
            label="healing-handshake",
            delay=delay,
        )
        if tapped:
            ctx.console.log("Ayuda de alianza solicitada para la curación")
        elif expect_help:
            ctx.console.log("[info] No apareció el ícono de ayuda tras curar las tropas")

    def _return_home(
        self,
        ctx: TaskContext,
        sede_paths: Sequence[Any],
        threshold: float,
        timeout: float,
    ) -> None:
        """Regresa a la ciudad usando el botón sede o, en fallback, el botón back."""
        if self._tap_template(
            ctx,
            sede_paths,
            threshold,
            timeout=timeout,
            label="sede-button",
            delay=2.0,
        ):
            return
        if not tap_back_button(ctx, label="heal-back"):
            ctx.console.log("[warning] No se detectó el botón 'back' para volver a la sede tras curar tropas")

    def _tap_template(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Any],
        threshold: float,
        *,
        timeout: float,
        label: str,
        delay: float,
    ) -> bool:
        """Espera un template y realiza tap con la etiqueta indicada."""
        if not template_paths or not ctx.vision:
            return False
        result = ctx.vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            threshold=threshold,
            poll_interval=0.5,
            raise_on_timeout=False,
        )
        if not result:
            return False
        coords, matched = result
        ctx.console.log(f"Template '{matched.name}' detectado para '{label}'")
        ctx.device.tap(coords, label=label)
        if delay > 0:
            ctx.device.sleep(delay)
        return True

    def _wait_template(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Any],
        threshold: float,
        *,
        timeout: float,
        label: str,
    ) -> bool:
        """Solo verifica si un template apareció dentro del tiempo máximo."""
        if not template_paths or not ctx.vision:
            return False
        result = ctx.vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            threshold=threshold,
            poll_interval=0.5,
            raise_on_timeout=False,
        )
        return bool(result)

    def _template_paths(self, ctx: TaskContext, template_spec: Any) -> List[Any]:
        """Convierte nombres declarativos en rutas de template manejando listas o strings."""
        if template_spec is None:
            return []
        names: List[str] = []
        if isinstance(template_spec, str):
            names = [template_spec]
        elif isinstance(template_spec, Sequence):  # type: ignore[arg-type]
            for entry in template_spec:
                name = str(entry).strip()
                if name:
                    names.append(name)
        else:
            ctx.console.log(f"[warning] Especificación de template inválida: {template_spec}")
            return []

        paths: List[Any] = []
        for name in names:
            try:
                paths.extend(ctx.layout.template_paths(name))
            except KeyError:
                ctx.console.log(
                    f"[warning] Template '{name}' no está definido en el layout actual"
                )
        return paths