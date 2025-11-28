# Templates

 Coloca aquí las capturas recortadas que el bot usará para validar estados y localizar botones. Recomendaciones:
 
 - Usa `adb exec-out screencap -p > archivo.png` para capturar la pantalla de la instancia.
 - Recorta la zona exacta del elemento (por ejemplo, la "X" del popup) en formato PNG sin escalado.
 - Nombra los archivos y referencia su ruta relativa en `config/farms.yaml`, por ejemplo:
   ```yaml
   layouts:
     "540p":
       templates:
         world_button: assets/templates/world_button.png
         popup_close: assets/templates/popup_close.png
   ```
- Mantén la misma resolución/DPI entre la captura y la instancia donde se usará.
- Para flujos con varias capturas nuevas (por ejemplo `claim_daily_quests` o `daily_arena`), agrúpalas en una carpeta dedicada dentro de `assets/templates/` y deja un `README.md` indicando qué requiere cada tarea (ver `assets/templates/daily_quests/README.md` y `assets/templates/arena/README.md`).
