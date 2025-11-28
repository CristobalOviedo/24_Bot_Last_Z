"""Punto de entrada CLI para lanzar rutinas del bot Last Z."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config import load_config
from .runner import RoutineRunner, RunnerOptions


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Construye y parsea los argumentos permitidos por la CLI."""
    parser = argparse.ArgumentParser(description="Last Z farm automation runner")
    parser.add_argument("--config", default="config/farms.yaml", help="Ruta del YAML principal")
    parser.add_argument("--routine", help="Sobrescribe la rutina configurada", default=None)
    parser.add_argument("--batch-start", type=int, default=0, help="Índice inicial de la lista de granjas")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Número de granjas a procesar (por defecto todas)",
    )
    parser.add_argument("--farms", help="Lista de granjas separadas por coma", default=None)
    parser.add_argument("--simulate", action="store_true", help="No envía comandos reales a ADB/BlueStacks")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Itera sobre el conjunto seleccionado de granjas de forma continua",
    )
    parser.add_argument(
        "--loop-start",
        type=int,
        default=1,
        help="Índice (1-based) desde el que comenzará el ciclo permanente de granjas",
    )
    parser.add_argument(
        "--task",
        help="Ejecuta únicamente la tarea indicada ignorando el resto de la rutina",
        default=None,
    )

    args = parser.parse_args(argv)
    if args.task and args.routine:
        parser.error("No puedes pasar --task junto con --routine")
    return args

def parse_farm_order(farm_arg: str | None, config) -> Sequence[str] | None:
    """Convierte una cadena con índices/nombres de granjas en una tupla ordenada."""
    if not farm_arg:
        return None
    # Permite usar índices o nombres, mezcla ambos si es necesario
    names = []
    for item in farm_arg.split(","):
        item = item.strip()
        if item.isdigit():
            idx = int(item)
            # 1-based index, ajusta si tu YAML usa 0-based
            if 1 <= idx <= len(config.instances):
                names.append(config.instances[idx-1].name)
        else:
            names.append(item)
    return tuple(names)

def main(argv: Sequence[str] | None = None) -> None:
    """Carga la configuración y ejecuta la rutina seleccionada."""
    args = parse_args(argv)
    config_path = Path(args.config)
    config = load_config(config_path)

    farm_names = parse_farm_order(args.farms, config)

    options = RunnerOptions(
        routine_override=args.routine,
        batch_start=args.batch_start,
        batch_size=args.batch_size,
        farm_names=farm_names,
        simulate=args.simulate,
        loop=args.loop,
        loop_start_index=args.loop_start,
        single_task=args.task,
    )

    runner = RoutineRunner(config, options)
    runner.run()


if __name__ == "__main__":  # pragma: no cover
    main()
