# Changelog

Todos los cambios relevantes de este proyecto se registran en este archivo.

## 2026-04-12 - Automatizacion, templates y estandarizacion de docstrings

### Added
- Nuevas tareas: `alliance_donation` y `treasure_excavation`.
- Nuevos recursos de templates en `assets/templates/alliance`, `assets/templates/events` y `assets/templates/treasure`.
- Variantes adicionales de templates para boomer, bounty, investigation, radar y truck.
- Script `launcher_main.cmd`.

### Changed
- Estandarizacion de docstrings/comentarios en modulos y funciones publicas para cumplir la guia interna.
- Mejoras en flujos de tareas y orquestacion en `runner`, `daily_tracker` y modulos de tareas.
- Ajustes de configuracion en `config/farms.yaml` y soporte de nuevos templates.
- Actualizaciones de templates existentes para mejorar reconocimiento visual.

### Removed
- Eliminadas copias redundantes de templates de investigacion (`research_node_7 - copia (...)`).
