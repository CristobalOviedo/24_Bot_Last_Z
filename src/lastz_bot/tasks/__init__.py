"""Registro centralizado de tareas disponibles."""

from __future__ import annotations

from .base import TaskRegistry
from .close_popups import ClosePopupsTask
from .mail import CollectMailTask
from .vip import CollectVIPTask
from .map import AttackBoomerTask, GatherResourcesTask
from .gather_cycle import GatherCycleTask
from .fury_lord import FuryLordTask
from .rewards import ClaimQuickRewardsTask, ClaimRssExplorationTask
from .daily_quests import ClaimDailyQuestsTask
from .arena import DailyArenaTask
from .caravan import CaravanTask
from .trucks import TrucksTask
from .heal_troops import HealTroopsTask
from .bounty_missions import BountyMissionsTask
from .radar_quests import RadarQuestsTask
from .rally_boomer import RallyBoomerTask
from .investigation import InvestigationTask


def build_registry() -> TaskRegistry:
    """Instancia y registra todas las tareas soportadas por el bot."""
    registry = TaskRegistry()
    registry.register(ClosePopupsTask())
    registry.register(CollectMailTask())
    registry.register(CollectVIPTask())
    registry.register(AttackBoomerTask())
    registry.register(GatherResourcesTask())
    registry.register(GatherCycleTask())
    registry.register(RallyBoomerTask())
    registry.register(FuryLordTask())
    registry.register(ClaimQuickRewardsTask())
    registry.register(ClaimRssExplorationTask())
    registry.register(CaravanTask())
    registry.register(TrucksTask())
    registry.register(HealTroopsTask())
    registry.register(BountyMissionsTask())
    registry.register(RadarQuestsTask())
    registry.register(DailyArenaTask())
    registry.register(ClaimDailyQuestsTask())
    registry.register(InvestigationTask())
    return registry
