# Bounty mission templates

Captura cada uno de estos elementos desde la interfaz del juego y colócalos en esta carpeta:

- `bounty_icon.png`: icono del menú de bounty missions en la ciudad.
- `bounty_menu_header.png`: cabecera del panel una vez abierto (sirve para validar que está cargado).
- `bounty_go_button.png`: botón "Go"/"Ir" que aparece en cada misión disponible.
- `bounty_quick_deploy.png`: botón "Despliegue rápido" dentro del detalle de la misión.
- `bounty_send_button.png`: botón para confirmar el envío tras el despliegue.
- `bounty_claim_button.png` *(opcional)*: botón "Reclamar" que aparece sobre la lista cuando hay recompensas pendientes.
- `bounty_no_missions.png` *(opcional)*: etiqueta o mensaje que indique que no hay más misiones activas. El bot también puede deducirlo cuando deja de detectar botones "Go".
- `bounty_hero_busy.png` *(opcional)*: ventana/aviso que aparece cuando no hay héroes disponibles para la misión. Si no existe, la tarea detectará el caso cuando el panel de despliegue siga abierto tras intentar enviar.
- `bounty_mission_badge.png` *(opcional)*: distintivo que acompaña a cada misión activa (se usa como respaldo para ubicar filas). Si no lo capturas, basta con los botones "Go".

Si no cuentas con alguno de estos recortes, deja vacíos los campos correspondientes en `config/farms.yaml` (por defecto ya están vacíos) y el bot usará heurísticas basadas en la presencia/ausencia de los botones "Go" o del panel de "Despliegue rápido".