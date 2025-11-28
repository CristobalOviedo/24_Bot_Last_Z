"""Rutinas para reclamar recompensas rápidas y exploración RSS."""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from .base import TaskContext

Coord = Tuple[int, int]


def _as_list(value: object) -> List[str]:
    """Normaliza la entrada a una lista de strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value]
    return [str(value)]


def _coord_from_param(value: object, default: Coord) -> Coord:
    """Convierte un valor a coordenada o usa el default si no aplica."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return default


def _optional_coord(value: object) -> Coord | None:
    """Convierte a coordenada cuando es válido, devolviendo ``None`` si falta."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def _paths_from_names(
    ctx: TaskContext, template_names: Sequence[str], missing: set[str]
) -> List[Path]:
    """Resuelve nombres declarativos a rutas físicas registrando los ausentes."""
    paths: List[Path] = []
    for name in template_names:
        try:
            paths.extend(ctx.layout.template_paths(name))
        except KeyError:
            if name not in missing:
                ctx.console.log(f"[warning] Template '{name}' no está definido en el layout")
                missing.add(name)
    return paths


class ClaimQuickRewardsTask:
    """Busca templates de recompensas rápidas y los reclama en secuencia."""
    name = "claim_quick_rewards"

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()

    def run(self, ctx: TaskContext, params: dict) -> None:  # type: ignore[override]
        """Recorre templates, pulsa la recompensa y cierra overlays si aplica."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; claim_quick_rewards requiere detecciones")
            return

        template_names = _as_list(params.get("reward_templates"))
        if not template_names:
            ctx.console.log("[warning] No se configuraron templates para claim_quick_rewards")
            return

        threshold = float(params.get("template_threshold", params.get("threshold", 0.8)))
        tap_delay = float(params.get("tap_delay", 2.0))
        max_claims = int(params.get("max_claims", 0))
        post_claim_delay = float(params.get("post_claim_delay", 0.0))
        overlay_templates = set(_as_list(params.get("overlay_templates")))
        overlay_dismiss_coord = _optional_coord(params.get("overlay_dismiss_tap"))
        overlay_dismiss_delay = float(params.get("overlay_dismiss_delay", 0.5))
        claimed = 0

        for template_name in template_names:
            if max_claims and claimed >= max_claims:
                break
            paths = _paths_from_names(ctx, [template_name], self._missing_templates)
            if not paths:
                continue
            result = ctx.vision.find_any_template(paths, threshold=threshold)
            if not result:
                continue
            coords, matched_path = result
            ctx.console.log(f"Recompensa '{matched_path.name}' detectada; reclamando")
            ctx.device.tap(coords, label=f"quick-reward-{template_name}")
            claimed += 1
            if post_claim_delay > 0:
                ctx.device.sleep(post_claim_delay)
            if template_name in overlay_templates and overlay_dismiss_coord:
                ctx.device.tap(overlay_dismiss_coord, label="quick-reward-dismiss")
                if overlay_dismiss_delay > 0:
                    ctx.device.sleep(overlay_dismiss_delay)
            if tap_delay > 0:
                ctx.device.sleep(tap_delay)


class ClaimRssExplorationTask:
    """Abre el panel de exploración RSS, reclama y confirma retorno a la ciudad."""
    name = "claim_rss_exploracion"

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()

    def run(self, ctx: TaskContext, params: dict) -> None:  # type: ignore[override]
        """Abre el botón configurado, reclama y valida la pantalla final."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; claim_rss_exploration requiere detecciones")
            return

        button_templates = _as_list(params.get("exploration_button_template"))
        claim_templates = _as_list(params.get("claim_button_template"))
        if not button_templates or not claim_templates:
            ctx.console.log("[warning] Falta configurar templates para claim_rss_exploration")
            return

        threshold = float(params.get("template_threshold", 0.8))
        tap_delay = float(params.get("tap_delay", 2.0))
        panel_delay = float(params.get("panel_delay", 2.0))
        claim_timeout = float(params.get("claim_timeout", 8.0))
        post_claim_delay = float(params.get("post_claim_delay", 1.5))
        dismiss_delay = float(params.get("dismiss_delay", 0.5))
        dismiss_coord = _optional_coord(params.get("dismiss_tap"))
        if dismiss_coord is None:
            dismiss_coord = _optional_coord(params.get("overlay_dismiss_tap"))
        if dismiss_coord is None:
            dismiss_coord = (539, 0)
        ready_timeout = float(params.get("ready_timeout", 6.0))
        ready_threshold = float(params.get("ready_threshold", threshold))

        button_paths = _paths_from_names(ctx, button_templates, self._missing_templates)
        if not button_paths:
            return
        button_match = ctx.vision.find_any_template(button_paths, threshold=threshold)
        if not button_match:
            ctx.console.log("[info] No se detectó rss_exploracion en pantalla")
            return

        coords, matched_path = button_match
        ctx.console.log(f"Exploración '{matched_path.name}' detectada; abriendo panel")
        ctx.device.tap(coords, label="rss-exploration-open")
        if tap_delay > 0:
            ctx.device.sleep(tap_delay)
        ctx.device.sleep(panel_delay)

        claim_paths = _paths_from_names(ctx, claim_templates, self._missing_templates)
        if not claim_paths:
            return
        claim_result = ctx.vision.wait_for_any_template(
            claim_paths,
            timeout=claim_timeout,
            threshold=threshold,
            poll_interval=0.5,
            raise_on_timeout=False,
        )
        if not claim_result:
            ctx.console.log("[warning] No se encontró el botón 'claim' en rss_exploracion")
            return

        claim_coords, claim_path = claim_result
        ctx.console.log(f"Botón de recompensa '{claim_path.name}' encontrado; reclamando")
        ctx.device.tap(claim_coords, label="rss-exploration-claim")
        ctx.device.sleep(post_claim_delay)
        ctx.device.tap(dismiss_coord, label="rss-exploration-dismiss")
        if dismiss_delay > 0:
            ctx.device.sleep(dismiss_delay)
        if not self._wait_for_ready_screen(
            ctx,
            timeout=ready_timeout,
            threshold=ready_threshold,
        ):
            ctx.console.log(
                "[warning] Tras reclamar exploración RSS no se detectó la pantalla principal; el runner intentará recuperarse"
            )

    def _wait_for_ready_screen(
        self,
        ctx: TaskContext,
        *,
        timeout: float,
        threshold: float,
    ) -> bool:
        """Verifica que aparezca algún template de ciudad/mapa tras cerrar el panel."""
        if not ctx.vision:
            return False
        template_names = ["sede_button", "world_button", "game_ready"]
        paths = _paths_from_names(ctx, template_names, self._missing_templates)
        if not paths:
            return True
        result = ctx.vision.wait_for_any_template(
            paths,
            timeout=timeout,
            poll_interval=0.5,
            threshold=threshold,
            raise_on_timeout=False,
        )
        return bool(result)