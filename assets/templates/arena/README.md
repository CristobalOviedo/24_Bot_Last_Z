# Arena Templates

Requeridos para la tarea `daily_arena`:

1. `arena_icon.png`: ícono de la arena en el HUD principal (al tocarlo entras directo al modo Plata).
2. `arena_challenge_button.png`: botón "Desafío" dentro del menú de arena.
3. `arena_attack_button.png`: ícono/botón con el símbolo de ataque que aparece junto a cada oponente (captura uno de los botones de la lista, el script buscará el más inferior en la pantalla).
4. `arena_combat_button.png`: botón "Combate" que inicia la pelea al entrar a la pantalla de duelo.
5. `arena_skip_button.png`: botón "Skip" que aparece durante la animación del combate (opcional, pero recomendado para saltar la animación).

Si en el futuro se requiere un botón intermedio (por ejemplo, otro modo de arena), añade su plantilla y referencia en `config/farms.yaml` bajo `task_defaults.daily_arena.mode_templates`.

Usa el mismo proceso de captura (`adb exec-out screencap -p > archivo.png`, recorte sin escalado) y verifica que las rutas configuradas en `config/farms.yaml` coincidan con estos nombres.
