"""Automatiza el envío de camiones y el reclamo de cofres asociados."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .base import TaskContext
from .utils import dismiss_overlay_if_present, tap_back_button

Coord = Tuple[int, int]

def _coord_from_param(value: object) -> Coord | None:
    """Convierte entradas tipo lista/tupla en coordenadas enteras si son válidas."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


@dataclass
class TemplateSpec:
    """Describe un template de rareza con su lista de rutas y threshold propio."""
    name: str
    paths: List[Any]
    threshold: float


class TrucksTask:
    """Gestiona todo el flujo de camiones: cofres, rerolls y envíos diarios."""
    name = "trucks"
    manual_daily_logging = True

    def run(self, ctx: TaskContext, params: dict[str, Any]) -> None:  # type: ignore[override]
        """Abre el panel, reclama recompensas y envía camiones hasta alcanzar el límite."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; 'trucks' requiere detecciones")
            return

        daily_task_name = str(params.get("daily_task_name", self.name))
        daily_limit = max(1, int(params.get("daily_limit", 4)))
        skip_daily_limit_check = bool(params.get("skip_daily_limit_check", False))
        collect_rewards_only = bool(params.get("collect_rewards_only", False))
        tracker_count = 0
        if ctx.daily_tracker:
            tracker_count = ctx.daily_tracker.current_count(ctx.farm.name, daily_task_name)

        limit_enforced = not skip_daily_limit_check
        if (
            limit_enforced
            and not collect_rewards_only
            and ctx.daily_tracker
            and tracker_count >= daily_limit
        ):
            ctx.console.log(
                "[info] Camiones diarios completos según el registro; se omite abrir el panel"
            )
            return

        icon_paths = self._template_paths(ctx, params.get("icon_template") or "truck_icon")
        menu_paths = self._template_paths(ctx, params.get("menu_template") or "truck_send_menu")
        empty_slot_paths = self._template_paths(ctx, params.get("empty_slot_template") or "truck_empty_slot")
        reward_paths = self._template_paths(ctx, params.get("reward_template") or "truck_reward")
        reward_overlay_sources = params.get("reward_overlay_template") or "truck_menu_reward"
        reward_overlay_paths = self._template_paths(ctx, reward_overlay_sources)
        dice_paths = self._template_paths(ctx, params.get("dice_template") or "truck_dice")
        send_button_paths = self._template_paths(
            ctx, params.get("send_button_template") or "truck_send_button"
        )
        warning_paths = self._template_paths(
            ctx, params.get("warning_template") or "truck_warning_window"
        )
        warning_cancel_paths = self._template_paths(
            ctx, params.get("warning_cancel_template") or "truck_warning_cancel"
        )

        if not icon_paths or not menu_paths or not empty_slot_paths or not dice_paths or not send_button_paths:
            ctx.console.log(
                "[warning] Faltan templates críticos para 'trucks'; verifica la configuración del layout"
            )
            return

        icon_threshold = float(params.get("icon_threshold", 0.8))
        menu_threshold = float(params.get("menu_threshold", 0.8))
        empty_slot_threshold = float(params.get("empty_slot_threshold", 0.8))
        reward_threshold = float(params.get("reward_threshold", 0.8))
        rarity_threshold = float(params.get("rarity_threshold", 0.82))
        dice_threshold = float(params.get("dice_threshold", 0.82))
        send_threshold = float(params.get("send_button_threshold", 0.82))
        warning_threshold = float(params.get("warning_threshold", 0.82))

        rarity_threshold_overrides = params.get("rarity_template_thresholds")
        rarity_templates = self._build_template_specs(
            ctx,
            params.get("rarity_templates") or ["truck_orange", "truck_purple"],
            default_threshold=rarity_threshold,
            overrides=rarity_threshold_overrides,
        )

        icon_timeout = float(params.get("icon_timeout", 5.0))
        menu_timeout = float(params.get("menu_timeout", 5.0))
        reward_timeout = float(params.get("reward_timeout", 6.0))
        warning_timeout = float(params.get("warning_timeout", 3.0))

        tap_delay = float(params.get("tap_delay", 1.0))
        menu_delay = float(params.get("menu_delay", 2.0))
        slot_open_delay = float(params.get("slot_open_delay", 1.5))
        reroll_delay = float(params.get("reroll_delay", 1.0))
        send_delay = float(params.get("send_delay", 2.0))
        reward_delay = float(params.get("reward_delay", 1.0))
        back_delay = float(params.get("back_delay", 1.0))
        warning_delay = float(params.get("warning_delay", 1.0))
        rarity_check_timeout = float(params.get("rarity_check_timeout", 2.5))
        rarity_check_poll = float(params.get("rarity_check_poll", 0.4))
        sent_counter_spec = params.get("sent_counter_templates")
        sent_counter_threshold = float(params.get("sent_counter_threshold", 0.9))
        sent_counter_timeout = float(params.get("sent_counter_timeout", 3.0))
        sent_counter_poll = float(params.get("sent_counter_poll", 0.4))
        counter_templates = self._load_counter_templates(ctx, sent_counter_spec)

        reward_overlay_close_button = params.get(
            "reward_overlay_close_button", "close_popup"
        )
        reward_overlay_timeout = float(params.get("reward_overlay_timeout", 6.0))
        reward_overlay_poll = float(params.get("reward_overlay_poll", 0.5))
        reward_overlay_threshold = float(
            params.get("reward_overlay_threshold", reward_threshold)
        )
        reward_overlay_delay = float(
            params.get("reward_overlay_delay", reward_delay)
        )
        reward_overlay_use_brightness = bool(
            params.get("reward_overlay_use_brightness", True)
        )
        reward_overlay_dark_threshold = float(
            params.get("reward_overlay_dark_threshold", 0.35)
        )
        reward_overlay_fallback = _coord_from_param(
            params.get("reward_overlay_dismiss_tap")
            or params.get("overlay_dismiss_tap")
        )

        max_rerolls = max(0, int(params.get("max_rerolls", 5)))
        max_slots = max(1, int(params.get("max_concurrent_slots", 3)))

        if not self._tap_first_template(
            ctx,
            icon_paths,
            icon_threshold,
            icon_timeout,
            label="trucks-icon",
            delay=tap_delay,
        ):
            ctx.console.log("[info] Icono de camiones no detectado; se omite la tarea")
            return

        if not self._tap_first_template(
            ctx,
            menu_paths,
            menu_threshold,
            menu_timeout,
            label="trucks-menu",
            delay=menu_delay,
        ):
            ctx.console.log("[warning] No se pudo abrir el menú de envíos de camiones")
            return

        self._collect_rewards(
            ctx,
            reward_paths,
            reward_overlay_paths,
            reward_overlay_close_button,
            reward_threshold,
            reward_timeout,
            reward_overlay_timeout,
            reward_overlay_poll,
            reward_overlay_threshold,
            reward_overlay_delay,
            reward_overlay_use_brightness,
            reward_overlay_dark_threshold,
            reward_overlay_fallback,
            reward_delay,
            back_delay,
        )

        if collect_rewards_only:
            ctx.console.log("[info] Ejecución de camiones en modo solo-recompensas; no se enviarán nuevos camiones")
            self._tap_back(ctx, back_delay)
            return

        visual_count = self._detect_sent_counter(
            ctx,
            counter_templates,
            sent_counter_threshold,
            sent_counter_timeout,
            sent_counter_poll,
        )
        if visual_count is not None:
            ctx.console.log(
                f"[info] Contador visual detectado: {visual_count}/{daily_limit} camiones enviados hoy"
            )
            self._set_tracker_count(ctx, daily_task_name, visual_count)
            current_sent = visual_count
        else:
            if counter_templates:
                ctx.console.log(
                    "[warning] No se pudo detectar el contador visual de camiones; se usará el registro diario"
                )
            current_sent = tracker_count
        current_sent = max(0, min(daily_limit, int(current_sent)))

        if limit_enforced and current_sent >= daily_limit:
            ctx.console.log(
                "[info] Camiones diarios completos según el contador disponible; cerrando panel"
            )
            self._set_tracker_count(ctx, daily_task_name, current_sent)
            self._tap_back(ctx, back_delay)
            return

        dispatched = 0
        while dispatched < max_slots:
            if limit_enforced and current_sent >= daily_limit:
                ctx.console.log("[info] Se alcanzó el límite diario tras el último envío")
                self._set_tracker_count(ctx, daily_task_name, current_sent)
                break

            slot_coord = self._find_one_slot(ctx, empty_slot_paths, empty_slot_threshold)
            if slot_coord is None:
                ctx.console.log("No hay más ranuras disponibles para enviar camiones")
                break

            ctx.device.tap(slot_coord, label="truck-slot")
            if slot_open_delay > 0:
                ctx.device.sleep(slot_open_delay)

            success = self._prepare_and_send_truck(
                ctx,
                rarity_templates,
                rarity_check_timeout,
                rarity_check_poll,
                dice_paths,
                dice_threshold,
                send_button_paths,
                send_threshold,
                warning_paths,
                warning_cancel_paths,
                warning_threshold,
                warning_timeout,
                warning_delay,
                max_rerolls,
                reroll_delay,
                send_delay,
            )
            if not success:
                ctx.console.log("[warning] No se logró enviar el camión en esta ranura; reintentando luego")
                break

            dispatched += 1
            current_sent = self._sync_sent_counter(
                ctx,
                counter_templates,
                sent_counter_threshold,
                sent_counter_timeout,
                sent_counter_poll,
                fallback=current_sent + 1,
            )
            self._set_tracker_count(ctx, daily_task_name, current_sent)

            self._collect_rewards(
                ctx,
                reward_paths,
                reward_overlay_paths,
                reward_overlay_close_button,
                reward_threshold,
                reward_timeout,
                reward_overlay_timeout,
                reward_overlay_poll,
                reward_overlay_threshold,
                reward_overlay_delay,
                reward_overlay_use_brightness,
                reward_overlay_dark_threshold,
                reward_overlay_fallback,
                reward_delay,
                back_delay,
            )

        final_counter = self._detect_sent_counter(
            ctx,
            counter_templates,
            sent_counter_threshold,
            max(sent_counter_timeout, 4.0),
            sent_counter_poll,
        )
        if final_counter is not None and final_counter != current_sent:
            ctx.console.log(
                f"[info] Conteo final en el menú: {final_counter}/{daily_limit}; actualizando registro"
            )
            current_sent = max(0, min(daily_limit, final_counter))

        self._set_tracker_count(ctx, daily_task_name, current_sent)
        self._tap_back(ctx, back_delay)

    def _prepare_and_send_truck(
        self,
        ctx: TaskContext,
        rarity_templates: Sequence[TemplateSpec],
        rarity_check_timeout: float,
        rarity_check_poll: float,
        dice_paths: Sequence[Any],
        dice_threshold: float,
        send_button_paths: Sequence[Any],
        send_threshold: float,
        warning_paths: Sequence[Any],
        warning_cancel_paths: Sequence[Any],
        warning_threshold: float,
        warning_timeout: float,
        warning_delay: float,
        max_rerolls: int,
        reroll_delay: float,
        send_delay: float,
    ) -> bool:
        """Gestiona rerolls hasta encontrar la rareza deseada y pulsa el botón Send."""
        if not rarity_templates:
            ctx.console.log("[warning] No hay templates configurados para evaluar rarezas de camión")
            return False

        attempt = 0
        rarity_obtained = False
        while attempt <= max_rerolls:
            matched_rarity = self._wait_for_desired_rarity(
                ctx,
                rarity_templates,
                rarity_check_timeout if attempt == 0 else max(0.5, rarity_check_timeout / 2),
                rarity_check_poll,
            )
            if matched_rarity is not None:
                ctx.console.log(f"Rareza objetivo detectada con '{matched_rarity.name}'; enviando camión")
                rarity_obtained = True
                break
            if attempt == max_rerolls:
                ctx.console.log(
                    "[info] No se encontró rareza morada/naranja tras los rerolls; se descartará la ranura"
                )
                break
            if not self._tap_first_template(
                ctx,
                dice_paths,
                dice_threshold,
                timeout=3.0,
                label="truck-dice",
                delay=reroll_delay,
            ):
                return False
            if self._dismiss_reroll_warning(
                ctx,
                warning_paths,
                warning_cancel_paths,
                warning_threshold,
                warning_timeout,
                warning_delay,
            ):
                ctx.console.log("[info] Advertencia al relanzar detectada; conservando rareza actual")
                break
            attempt += 1

        if not rarity_obtained:
            self._tap_back(ctx, send_delay)
            return False

        if not self._tap_first_template(
            ctx,
            send_button_paths,
            send_threshold,
            timeout=4.0,
            label="truck-send",
            delay=send_delay,
        ):
            return False
        return True

    def _collect_rewards(
        self,
        ctx: TaskContext,
        reward_paths: Sequence[Any],
        overlay_paths: Sequence[Any],
        overlay_close_button: str | None,
        reward_threshold: float,
        reward_timeout: float,
        overlay_timeout: float,
        overlay_poll: float,
        overlay_threshold: float,
        overlay_delay: float,
        overlay_use_brightness: bool,
        overlay_dark_threshold: float,
        overlay_fallback: Coord | None,
        reward_delay: float,
        back_delay: float,
    ) -> None:
        """Busca cofres disponibles, los reclama y cierra overlays asociados."""
        if not reward_paths or not ctx.vision:
            return

        while True:
            matches = ctx.vision.find_all_templates(
                reward_paths,
                threshold=reward_threshold,
                max_results=3,
            )
            if not matches:
                break
            coords, matched = matches[0]
            ctx.console.log(f"Cofre detectado con '{matched.name}', reclamando")
            ctx.device.tap(coords, label="truck-reward")
            if reward_delay > 0:
                ctx.device.sleep(reward_delay)
            overlay_closed = False
            if overlay_paths:
                overlay_closed = dismiss_overlay_if_present(
                    ctx,
                    overlay_paths,
                    overlay_close_button,
                    timeout=overlay_timeout,
                    poll_interval=overlay_poll,
                    threshold=overlay_threshold,
                    delay=overlay_delay,
                    use_brightness=overlay_use_brightness,
                    brightness_threshold=overlay_dark_threshold,
                    fallback_tap=overlay_fallback,
                )
            if not overlay_closed:
                ctx.console.log(
                    "[warning] No se detectó overlay tras reclamar camiones; tocando coordenadas originales"
                )
                ctx.device.tap(coords, label="truck-reward-overlay-dismiss")
                if reward_delay > 0:
                    ctx.device.sleep(reward_delay)
            else:
                if reward_delay > 0:
                    ctx.device.sleep(reward_delay)
            # Salir del resumen mediante el botón real
            self._tap_back(ctx, back_delay)

    def _find_one_slot(
        self,
        ctx: TaskContext,
        slot_paths: Sequence[Any],
        threshold: float,
    ) -> Coord | None:
        """Devuelve la coordenada del primer slot vacío encontrado."""
        if not ctx.vision:
            return None
        matches = ctx.vision.find_all_templates(
            slot_paths,
            threshold=threshold,
            max_results=1,
        )
        if not matches:
            return None
        coords, _ = matches[0]
        return coords

    def _wait_for_desired_rarity(
        self,
        ctx: TaskContext,
        rarity_specs: Sequence[TemplateSpec],
        timeout: float,
        poll_interval: float,
    ) -> Path | None:
        """Escanea continuamente hasta detectar alguno de los templates de rareza meta."""
        if not ctx.vision or not rarity_specs:
            return None
        deadline = time.monotonic() + max(0.0, timeout)
        poll = max(0.1, poll_interval)
        while time.monotonic() <= deadline:
            for spec in rarity_specs:
                result = ctx.vision.find_any_template(spec.paths, threshold=spec.threshold)
                if result:
                    _, matched = result
                    return matched
            ctx.device.sleep(poll)
        return None

    def _tap_first_template(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Any],
        threshold: float,
        timeout: float,
        *,
        label: str,
        delay: float,
    ) -> bool:
        """Espera el primer template disponible y ejecuta el tap con delay opcional."""
        if not template_paths or not ctx.vision:
            return False
        result = ctx.vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            poll_interval=0.5,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if not result:
            ctx.console.log(f"[warning] Template para '{label}' no apareció a tiempo")
            return False
        coords, matched = result
        ctx.console.log(f"Template '{matched.name}' detectado para '{label}'")
        ctx.device.tap(coords, label=label)
        if delay > 0:
            ctx.device.sleep(delay)
        return True

    def _dismiss_reroll_warning(
        self,
        ctx: TaskContext,
        warning_paths: Sequence[Any],
        warning_cancel_paths: Sequence[Any],
        threshold: float,
        timeout: float,
        delay: float,
    ) -> bool:
        """Maneja la ventana de advertencia al rerollear, cancelando si es necesario."""
        if not ctx.vision or not warning_paths:
            return False
        result = ctx.vision.wait_for_any_template(
            warning_paths,
            timeout=timeout,
            poll_interval=0.2,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if not result:
            return False
        ctx.console.log("[info] Ventana de advertencia detectada; cancelando reroll")
        if warning_cancel_paths:
            tapped = self._tap_first_template(
                ctx,
                warning_cancel_paths,
                threshold,
                timeout=max(1.0, timeout),
                label="truck-warning-cancel",
                delay=delay,
            )
            if tapped:
                return True
        self._tap_back(ctx, delay)
        return True

    def _load_counter_templates(
        self,
        ctx: TaskContext,
        spec: Any,
    ) -> Dict[int, List[Any]]:
        """Convierte la configuración en un mapa {valor: [templates]} para el contador visual."""
        mapping: Dict[int, List[Any]] = {}
        if spec is None:
            return mapping
        if isinstance(spec, dict):
            entries = list(spec.items())
        elif isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
            entries = list(enumerate(spec))
        else:
            return mapping
        for raw_value, template_spec in entries:
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                continue
            paths = self._template_paths(ctx, template_spec)
            if paths:
                mapping[value] = paths
        return mapping

    def _build_template_specs(
        self,
        ctx: TaskContext,
        spec: Any,
        *,
        default_threshold: float,
        overrides: Any,
    ) -> List[TemplateSpec]:
        """Genera `TemplateSpec` por rareza aplicando thresholds individuales si existen."""
        names: List[str] = []
        if spec is None:
            return []
        if isinstance(spec, str):
            names = [spec]
        elif isinstance(spec, Sequence):
            for entry in spec:
                if isinstance(entry, str):
                    names.append(entry)
        else:
            return []

        thresholds: Dict[str, float] = {}
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                try:
                    thresholds[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue

        specs: List[TemplateSpec] = []
        for name in names:
            paths = self._template_paths(ctx, name)
            if not paths:
                continue
            threshold = thresholds.get(name, default_threshold)
            specs.append(TemplateSpec(name=name, paths=paths, threshold=threshold))
        return specs

    def _detect_sent_counter(
        self,
        ctx: TaskContext,
        counter_templates: Dict[int, List[Any]],
        threshold: float,
        timeout: float,
        poll_interval: float,
    ) -> int | None:
        """Lee el contador visual comprobando cada template asociado a un número."""
        if not counter_templates or not ctx.vision:
            return None
        deadline = time.monotonic() + max(0.2, timeout)
        poll = max(0.1, poll_interval)
        ordered = sorted(counter_templates.items(), key=lambda item: item[0])
        while time.monotonic() <= deadline:
            for value, paths in ordered:
                result = ctx.vision.find_any_template(paths, threshold=threshold)
                if result:
                    return value
            ctx.device.sleep(poll)
        return None

    def _sync_sent_counter(
        self,
        ctx: TaskContext,
        counter_templates: Dict[int, List[Any]],
        threshold: float,
        timeout: float,
        poll_interval: float,
        *,
        fallback: int,
    ) -> int:
        """Actualiza el conteo según el HUD y en caso de fallo usa el fallback provisto."""
        value = self._detect_sent_counter(
            ctx,
            counter_templates,
            threshold,
            timeout,
            poll_interval,
        )
        if value is None:
            return max(0, fallback)
        return max(0, value)

    def _tap_back(self, ctx: TaskContext, delay: float) -> None:
        """Cierra el panel principal usando el botón back detectado por template."""
        if not tap_back_button(ctx, label="trucks-back"):
            ctx.console.log("[warning] No se pudo detectar el botón 'back' en el panel de camiones")
            return
        if delay > 0:
            ctx.device.sleep(delay)

    def _set_tracker_count(
        self,
        ctx: TaskContext,
        task_name: str,
        count: int,
    ) -> None:
        """Sincroniza el contador del tracker con el valor indicado."""
        if not ctx.daily_tracker:
            return
        ctx.daily_tracker.set_count(ctx.farm.name, task_name, count)

    def _template_paths(self, ctx: TaskContext, template_spec: Any) -> List[Any]:
        """Resuelve nombres o listas mixtas a rutas físicas del layout, logueando faltantes."""
        if template_spec is None:
            return []
        names: List[str] = []
        if isinstance(template_spec, str):
            names = [template_spec]
        elif isinstance(template_spec, Sequence):
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
