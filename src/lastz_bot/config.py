"""Modelos de configuración y utilidades para rutinas, layouts y runtimes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


Coord = Tuple[int, int]
Region = Tuple[Tuple[float, float], Tuple[float, float]]


class TroopSlotConfig(BaseModel):
    """Define la coordenada y región de estado para un slot de tropas.

    Attributes:
        tap: Coordenada absoluta para tocar el slot.
        indicator_region: Región normalizada donde se detecta el texto/ícono de estado.
        label: Etiqueta opcional usada para logs al tocar el slot.
    """

    tap: Coord
    indicator_region: Region
    label: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "TroopSlotConfig":
        (y_start, y_end), (x_start, x_end) = self.indicator_region
        if not 0.0 <= y_start < y_end <= 1.0:
            raise ValueError(
                "indicator_region.y debe estar normalizado entre 0 y 1 y con y_start < y_end"
            )
        if not 0.0 <= x_start < x_end <= 1.0:
            raise ValueError(
                "indicator_region.x debe estar normalizado entre 0 y 1 y con x_start < x_end"
            )
        return self


class TroopStateLayoutConfig(BaseModel):
    """Agrupa slots e indicadores visuales usados para leer estados de tropas.

    Attributes:
        slots: Mapa de slots identificados por nombre.
        state_templates: Templates asociados a cada estado posible.
        detection_threshold: Valor mínimo de coincidencia para considerar un estado.
        debug_regions_enabled: Controla si se guardan los recortes usados por cada slot.
    """

    slots: Dict[str, TroopSlotConfig] = Field(default_factory=dict)
    state_templates: Dict[str, List[str]] = Field(default_factory=dict)
    detection_threshold: float = 0.85
    debug_regions_enabled: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "TroopStateLayoutConfig":
        cleaned: Dict[str, List[str]] = {}
        for key, value in self.state_templates.items():
            entries = [entry for entry in value if entry]
            if not entries:
                raise ValueError(
                    f"El estado de tropa '{key}' debe incluir al menos un template"
                )
            cleaned[key] = entries
        self.state_templates = cleaned
        if self.detection_threshold <= 0 or self.detection_threshold > 1:
            raise ValueError("detection_threshold debe estar entre 0 y 1")
        return self


class LayoutConfig(BaseModel):
    """Coordina botones y templates específicos de un layout del juego."""

    buttons: Dict[str, Coord] = Field(default_factory=dict)
    templates: Dict[str, List[str]] = Field(default_factory=dict)
    troop_state: TroopStateLayoutConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_buttons(cls, data: Any) -> Any:
        if isinstance(data, dict) and "buttons" in data:
            buttons: Dict[str, Coord] = {}

            def _collect_button_entries(payload: Dict[str, Any]) -> None:
                for key, value in payload.items():
                    if isinstance(value, dict):
                        _collect_button_entries(value)
                        continue
                    if not isinstance(value, (list, tuple)) or len(value) != 2:
                        raise ValueError(f"Button '{key}' must be a pair [x, y]")
                    if key in buttons:
                        raise ValueError(f"Button '{key}' is defined more than once")
                    buttons[key] = (int(value[0]), int(value[1]))

            _collect_button_entries(data["buttons"])
            data["buttons"] = buttons

        if isinstance(data, dict) and "templates" in data:
            templates: Dict[str, List[str]] = {}

            def _collect_template_entries(payload: Dict[str, Any]) -> None:
                for key, value in payload.items():
                    if isinstance(value, dict):
                        _collect_template_entries(value)
                        continue
                    if isinstance(value, str):
                        entries = [value]
                    elif isinstance(value, (list, tuple)):
                        entries = [str(item) for item in value if str(item).strip()]
                    else:
                        raise ValueError(
                            f"Template '{key}' must be a path string or list of strings"
                        )
                    if not entries:
                        raise ValueError(
                            f"Template '{key}' must include at least one path"
                        )
                    if key in templates:
                        raise ValueError(
                            f"Template '{key}' is defined more than once"
                        )
                    templates[key] = entries

            _collect_template_entries(data["templates"])
            data["templates"] = templates
        return data

    def get(self, button_name: str) -> Coord:
        """Devuelve la coordenada absoluta de un botón lógico."""
        try:
            return self.buttons[button_name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(
                f"Button '{button_name}' is not defined for this layout"
            ) from exc

    def template_paths(self, template_name: str) -> List[Path]:
        """Lista las rutas absolutas configuradas para un template."""
        try:
            raw_paths = self.templates[template_name]
        except KeyError as exc:
            raise KeyError(
                f"Template '{template_name}' is not defined for this layout"
            ) from exc

        resolved: List[Path] = []
        for raw_path in raw_paths:
            path = Path(raw_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            resolved.append(path)
        return resolved

    def template_path(self, template_name: str) -> Path:
        """Obtiene la primera ruta asociada a un template, útil para íconos únicos."""
        paths = self.template_paths(template_name)
        if not paths:
            raise KeyError(
                f"Template '{template_name}' does not include any paths"
            )
        return paths[0]

    def button(self, button_name: str) -> Coord:
        """Retrocompatibilidad con tareas que esperan layout.button()."""
        return self.get(button_name)


class TaskSpec(BaseModel):
    """Describe una tarea individual dentro de una rutina."""

    task: str
    params: Dict[str, Any] = Field(default_factory=dict)


class RoutineConfig(BaseModel):
    """Lista ordenada de tareas que el bot ejecutará para una instancia."""

    tasks: List[TaskSpec]


class InstanceConfig(BaseModel):
    """Parámetros por granja/instancia emulada."""

    name: str
    instance: str
    device_port: int
    routine: str
    layout: str | None = None
    alliance_help_limit: int = 0
    alliance_help_minutes: float = 0.0
    free_research_minutes: float = 0.0
    free_construction_minutes: float = 0.0
    construction_enabled: bool = False

    @model_validator(mode="after")
    def _validate_progress_modifiers(self) -> "InstanceConfig":
        self.alliance_help_limit = max(0, int(self.alliance_help_limit or 0))
        self.alliance_help_minutes = max(0.0, float(self.alliance_help_minutes or 0.0))
        self.free_research_minutes = max(0.0, float(self.free_research_minutes or 0.0))
        self.free_construction_minutes = max(
            0.0, float(self.free_construction_minutes or 0.0)
        )
        self.construction_enabled = bool(self.construction_enabled)
        return self


class ADBConfig(BaseModel):
    """Define endpoints y timeouts para comunicarse vía ADB/HD-Adb."""

    executable: str
    host: str = "127.0.0.1"
    connect_timeout: float = 20.0
    command_timeout: float = 20.0


class BlueStacksConfig(BaseModel):
    """Configura rutas y tiempos de arranque/cierre de BlueStacks."""

    player_path: str
    instance_cli: str | None = None
    start_timeout: float = 60.0
    shutdown_timeout: float = 15.0


class DailyTaskTrackingConfig(BaseModel):
    """Gestiona los registros diarios usados para limitar tareas por día."""

    enabled: bool = True
    storage_path: str = "state/daily_tasks.json"
    reset_hour_local: int = 23
    tracked_tasks: Dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_tasks(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_tasks = data.get("tracked_tasks")
        if raw_tasks is None:
            data["tracked_tasks"] = {}
            return data
        if isinstance(raw_tasks, list):
            data["tracked_tasks"] = {
                str(task).strip(): 1 for task in raw_tasks if str(task).strip()
            }
        elif isinstance(raw_tasks, dict):
            cleaned: Dict[str, int] = {}
            for key, value in raw_tasks.items():
                name = str(key).strip()
                if not name:
                    continue
                try:
                    limit = int(value)
                except (TypeError, ValueError):
                    limit = 1
                cleaned[name] = max(1, limit)
            data["tracked_tasks"] = cleaned
        else:
            data["tracked_tasks"] = {}
        return data

    @model_validator(mode="after")
    def _validate(self) -> "DailyTaskTrackingConfig":
        if not 0 <= self.reset_hour_local <= 23:
            raise ValueError("reset_hour_local debe estar entre 0 y 23")
        if not self.tracked_tasks:
            raise ValueError(
                "tracked_tasks no puede estar vacío cuando daily tracking está habilitado"
            )
        self.tracked_tasks = {name: max(1, int(limit)) for name, limit in self.tracked_tasks.items()}
        return self


class BotConfig(BaseModel):
    """Configuración raíz del bot, uniendo dispositivos, layouts y rutinas."""

    adb: ADBConfig
    instances: List[InstanceConfig]
    routines: Dict[str, RoutineConfig]
    layouts: Dict[str, LayoutConfig] = Field(default_factory=dict)
    default_layout: str | None = None
    bluestacks: BlueStacksConfig | None = None
    tesseract_cmd: str | None = None
    instance_warmup_seconds: float = 5.0
    timings: Dict[str, float] = Field(default_factory=dict)
    task_defaults: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    daily_tracking: DailyTaskTrackingConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _collect_timings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        existing_timings = data.get("timings")
        timings: Dict[str, float] = {}
        if isinstance(existing_timings, dict):
            for key, value in existing_timings.items():
                try:
                    timings[key] = float(value)
                except (TypeError, ValueError):
                    continue
        known_fields = set(cls.model_fields)
        known_fields.add("timings")
        for key, value in list(data.items()):
            if key in known_fields:
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                if key not in timings:
                    timings[key] = float(value)
        if timings:
            data["timings"] = timings
        return data

    def routine_for(self, routine_id: str) -> RoutineConfig:
        """Obtiene una rutina por clave asegurando que exista."""
        try:
            return self.routines[routine_id]
        except KeyError as exc:
            raise KeyError(f"Routine '{routine_id}' is not defined in config") from exc

    def layout_for(self, instance: InstanceConfig) -> LayoutConfig:
        """Resuelve el layout efectivo para una instancia dada."""
        layout_key = instance.layout or self.default_layout
        if not layout_key:
            raise KeyError(
                f"No layout specified for instance '{instance.name}' and no default layout set"
            )
        try:
            return self.layouts[layout_key]
        except KeyError as exc:
            raise KeyError(
                f"Layout '{layout_key}' referenced by '{instance.name}' does not exist"
            ) from exc

    def timing(self, key: str, default: float) -> float:
        """Devuelve un timing configurado o el valor por defecto."""
        value = self.timings.get(key, default)
        return float(value)

    def task_defaults_for(self, task_name: str) -> Dict[str, Any]:
        """Fusiona defaults comunes con los específicos de una tarea."""
        combined: Dict[str, Any] = {}
        common_defaults = self.task_defaults.get("__common__", {})
        if common_defaults:
            combined.update(common_defaults)
        task_specific = self.task_defaults.get(task_name, {})
        if task_specific:
            combined.update(task_specific)
        return dict(combined)


def load_config(path: str | Path) -> BotConfig:
    """Carga y valida un archivo YAML de configuración.

    Args:
        path: Ruta al archivo YAML.

    Returns:
        BotConfig: Instancia validada lista para usar.

    Raises:
        SystemExit: Si el esquema es inválido según los modelos de Pydantic.
    """
    cfg_path = Path(path)
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        return BotConfig.model_validate(raw)
    except ValidationError as exc:  # pragma: no cover - provided for runtime usage
        raise SystemExit(f"Invalid configuration file: {exc}")
