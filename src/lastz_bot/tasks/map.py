from __future__ import annotations

from typing import Iterable

from ..devices import resolve_button
from .base import TaskContext
from .utils import tap_back_button


class AttackBoomerTask:
    name = "attack_boomer"

    def run(self, ctx: TaskContext, params):
        attempts = int(params.get("attempts", 3))
        map_button = params.get("map_button", "map")
        search_button = params.get("search_button", "map_search")
        boomer_option = params.get("boomer_option", "map_boomer_option")
        search_confirm = params.get("search_confirm", "map_search_confirm")
        rally_button = params.get("rally_button", "map_rally")
        march_button = params.get("march_button", "map_march")
        delay = float(params.get("delay", 0.5))

        ctx.console.log("Buscando Boomer/Zombi gigante")
        ctx.device.tap(resolve_button(ctx.layout, map_button), label="map")
        ctx.device.sleep(delay)
        ctx.device.tap(resolve_button(ctx.layout, search_button), label="search")
        ctx.device.sleep(delay)
        ctx.device.tap(resolve_button(ctx.layout, boomer_option), label="boomer-option")
        ctx.device.sleep(delay)
        ctx.device.tap(resolve_button(ctx.layout, search_confirm), label="search-confirm")
        ctx.device.sleep(delay)

        for idx in range(attempts):
            ctx.console.log(f"Lanzando rally #{idx + 1}")
            ctx.device.tap(resolve_button(ctx.layout, rally_button), label="rally")
            ctx.device.sleep(delay)
            ctx.device.tap(resolve_button(ctx.layout, march_button), label="march")
            ctx.device.sleep(delay)

        if not tap_back_button(ctx, label="attack-boomer-exit"):
            ctx.console.log("[warning] No se detectó el botón 'back' tras los ataques a Boomer")


class GatherResourcesTask:
    name = "gather_resources"

    def run(self, ctx: TaskContext, params):
        map_button = params.get("map_button", "map")
        search_button = params.get("search_button", "map_search")
        resource_tab = params.get("resource_tab", "map_resource_tab")
        resource_buttons = params.get("resource_buttons", {
            "food": "map_resource_food",
            "wood": "map_resource_wood",
        })
        level_buttons = params.get("level_buttons", {})
        search_confirm = params.get("search_confirm", "map_search_confirm")
        gather_button = params.get("gather_button", "map_gather")
        march_button = params.get("march_button", "map_march")
        resource_types: Iterable[str] = params.get("resource_types", ("food", "wood"))
        level_priority: Iterable[int] = params.get("level_priority", (6, 5, 4))
        delay = float(params.get("delay", 0.5))

        for resource in resource_types:
            ctx.console.log(f"Buscando recurso {resource}")
            ctx.device.tap(resolve_button(ctx.layout, map_button), label="map")
            ctx.device.sleep(delay)
            ctx.device.tap(resolve_button(ctx.layout, search_button), label="search")
            ctx.device.sleep(delay)
            ctx.device.tap(resolve_button(ctx.layout, resource_tab), label="resource-tab")
            ctx.device.sleep(0.3)

            resource_button = resource_buttons.get(resource)
            if resource_button:
                ctx.device.tap(resolve_button(ctx.layout, resource_button), label=f"resource-{resource}")
                ctx.device.sleep(0.3)

            for level in level_priority:
                button_name = level_buttons.get(str(level))
                if not button_name:
                    continue
                ctx.device.tap(resolve_button(ctx.layout, button_name), label=f"level-{level}")
                ctx.device.sleep(0.2)
                break

            ctx.device.tap(resolve_button(ctx.layout, search_confirm), label="search-confirm")
            ctx.device.sleep(delay)
            ctx.device.tap(resolve_button(ctx.layout, gather_button), label="gather")
            ctx.device.sleep(delay)
            ctx.device.tap(resolve_button(ctx.layout, march_button), label="march")
            ctx.device.sleep(delay)

        if not tap_back_button(ctx, label="gather-exit"):
            ctx.console.log("[warning] No se detectó el botón 'back' tras la recolección")
