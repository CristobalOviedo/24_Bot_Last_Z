# Guía de comentarios y docstrings

Esta guía resume cómo documentar módulos, clases y funciones dentro del bot. Aplica a todo el código nuevo o modificado.

## Principios generales

1. **Español + formato Google.** Todos los docstrings deben estar en español y seguir el formato Google (Resumen → Args → Returns → Raises → etc.).
2. **Contexto sobre el juego.** Explica supuestos de UI, plantillas y dependencias con otros módulos. Si la función depende de un template, botón o layout específico, menciónalo.
3. **Sin ruido.** Documenta la intención, no cada línea de código. Evita frases obvias ("Suma 1 al contador").
4. **Consistencia en el tono.** Usa indicativo, frases cortas y verbos en presente ("Abre el panel", "Retorna la coordenada").
5. **ASCII por defecto.** Sólo usa caracteres fuera de ASCII cuando ya existan en el archivo (por ejemplo, tildes en textos españoles). Nunca introduzcas emoji ni símbolos especiales.

## Docstrings obligatorios

| Elemento          | Requiere docstring | Detalles clave |
|-------------------|--------------------|----------------|
| Módulos           | Sí                 | Breve resumen del objetivo del archivo; opcionalmente menciona dependencias críticas (ADB, Vision, etc.). |
| Clases            | Sí                 | Qué rol cumple, cómo se usa. Para configuraciones (`Config`) describe qué agrupa. |
| Funciones/métodos | Sí                 | Describe intención, supuestos (layouts, thresholds), side effects (tap, logs, tracker). |
| Helpers privados  | Sí si no es obvio  | Documenta utilidades complejas: overlays, detección de tropas, contadores, etc. |
| Propiedades       | Opcional           | Sólo si hacen algo más complejo que exponer un valor. |

## Patrón de docstring

```
"""Resumen en una línea.

Opcionalmente, una segunda línea con más contexto.

Args:
    nombre (tipo): Descripción.
Returns:
    tipo: Descripción.
Raises:
    Excepción: Causa.
"""
```

Notas:
- Omite secciones vacías (no agregues `Raises` si no lanza excepciones).
- Usa comillas triples dobles (`"""`).
- Para valores opcionales usa anotaciones `Tipo | None` y explícitalo en la descripción.
- Cierra la descripción con punto.

## Comentarios en línea

- **Cuándo:** Usa comentarios `#` sólo para justificar decisiones no evidentes (timeouts, offsets, heurísticas). No describas código trivial.
- **Dónde:** Colócalos encima del bloque que justifican y deja una línea en blanco entre el comentario y el docstring más cercano.
- **Formato:** Frases cortas en español. Ejemplo: `# Reintenta porque el botón March aparece con lag tras el overlay.`

## Casos específicos

### Templates y visiones
- Indica qué templates se esperan (`"Interactúa con templates del grupo 'fury-attack'"`).
- Si hay fallbacks por brillo/overlay, explica por qué y cuándo se usan.
- Cuando uses `wait_for_any_template`, documenta el `timeout` y el impacto ("bloquea hasta 5 s").

### Navegación y taps
- Menciona botones requeridos (`world_button`, `sede_button`).
- Si se presiona `key_back` o `tap_back_button`, documenta el objetivo ("regresa a la ciudad tras cerrar overlay").

### Trackers y límites diarios
- Explica qué flags o contadores se actualizan (`daily_tracker.record_progress`).
- Cuando una función sincronice contadores visuales con el tracker, documenta que prioriza el HUD y luego el fallback.

### Tareas con reintentos
- Siempre menciona la política de reintentos: cuántos, qué provoca un `Abort/Retry`, qué logging se espera.
- Si la función modifica estado interno (p.ej. `_missing_templates`), indica por qué.

## Checklist antes de abrir un PR interno

- [ ] El archivo tiene docstring de módulo.
- [ ] Cada clase y método añadido/modificado incluye docstring actualizado.
- [ ] Todos los parámetros nuevos aparecen en `Args` (con tipos y unidades/formatos cuando aplica).
- [ ] Se describen side effects relevantes (taps, sleep, logs, trackers).
- [ ] Comentarios en línea sólo justifican decisiones no obvias.

Sigue estos lineamientos para mantener una documentación clara y homogénea en el bot. Siempre que dudes, busca ejemplos recientes (p.ej. `tasks/rally_boomer.py`, `tasks/gather_cycle.py`).