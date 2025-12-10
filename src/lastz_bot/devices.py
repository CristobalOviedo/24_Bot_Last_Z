"""Abstracciones para controlar dispositivos/instancias vía ADB y BlueStacks."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from rich.console import Console

from .config import ADBConfig, BlueStacksConfig, InstanceConfig, LayoutConfig, Coord

IS_WINDOWS = sys.platform.startswith("win")


class DeviceRecoverableError(RuntimeError):
    """Errores de ADB que requieren reiniciar la rutina."""


RECOVERABLE_ADB_RETURN_CODES = {
    1,
    0xFFFFFF89,  # 4294967177 -> BlueStacks/HD-Adb intermittent failure while instance is closing
}


class DeviceCaptureError(RuntimeError):
    """Indica que no fue posible obtener una captura desde el dispositivo."""


@dataclass
class DeviceController:
    """Capa delgada sobre HD-Adb con logs y reintentos."""

    adb: ADBConfig
    device_port: int
    console: Console
    simulate: bool = False

    def __post_init__(self) -> None:
        self.serial = f"{self.adb.host}:{self.device_port}"
        self._adb_path = str(Path(self.adb.executable))
        self._debug_capture_callback: Callable[[str], None] | None = None

    def _run(self, args: Iterable[str], timeout: Optional[float] = None) -> None:
        cmd = [self._adb_path, "-s", self.serial, *args]
        if self.simulate:
            self.console.log(f"[simulate] {' '.join(cmd)}")
            return

        effective_timeout = timeout if timeout is not None else self.adb.command_timeout

        try:
            subprocess.run(cmd, timeout=effective_timeout, check=True)
            return
        except subprocess.TimeoutExpired:
            self.console.log(
                f"[warning] Comando ADB excedió {effective_timeout:.1f}s; reintentando tras reconectar"
            )
        except subprocess.CalledProcessError as exc:
            self.console.log(
                f"[warning] Comando ADB falló ({exc.returncode}); reintentando tras reconectar"
            )
        self._recover_connection()
        try:
            subprocess.run(cmd, timeout=effective_timeout, check=True)
            return
        except subprocess.TimeoutExpired as exc:
            raise DeviceRecoverableError(
                f"Comando ADB excedió {effective_timeout:.1f}s incluso tras reconectar"
            ) from exc
        except subprocess.CalledProcessError as exc:
            if exc.returncode in RECOVERABLE_ADB_RETURN_CODES:
                raise DeviceRecoverableError(
                    f"Comando ADB falló con código {exc.returncode}; posible cierre del juego"
                ) from exc
            raise

    def wait_for_device(self, timeout: float | None = None) -> None:
        """Conecta y espera hasta que el dispositivo responda a ADB."""
        self.console.log(f"Conectando a {self.serial}...")
        self._run(["connect", self.serial], timeout=self.adb.connect_timeout)
        self.console.log(f"Esperando dispositivo {self.serial}...")
        self._run(["wait-for-device"], timeout or self.adb.connect_timeout)

    def tap(self, coord: Coord, label: str = "") -> None:
        """Simula un toque en la coordenada indicada, registrándolo para debug."""
        suffix = f" ({label})" if label else ""
        self._record_action_debug(f"tap-{label or coord}")
        self.console.log(f"Tap en {coord}{suffix}")
        self._run(["shell", "input", "tap", str(coord[0]), str(coord[1])])

    def swipe(self, start: Coord, end: Coord, duration_ms: int = 300, label: str = "") -> None:
        """Arrastra entre dos puntos en pantalla usando ``input swipe``."""
        suffix = f" ({label})" if label else ""
        self._record_action_debug(f"swipe-{label or start}")
        self.console.log(f"Swipe {start}->{end}{suffix}")
        self._run(
            [
                "shell",
                "input",
                "swipe",
                str(start[0]),
                str(start[1]),
                str(end[0]),
                str(end[1]),
                str(duration_ms),
            ]
        )

    def sleep(self, seconds: float) -> None:
        """Pausa el flujo respetando modo simulación."""
        self.console.log(f"Esperando {seconds:.1f}s")
        if not self.simulate:
            time.sleep(seconds)

    def capture_screen(self) -> Optional[bytes]:
        """Obtiene un PNG del framebuffer y maneja reintentos básicos."""
        cmd = [self._adb_path, "-s", self.serial, "exec-out", "screencap -p"]
        if self.simulate:
            self.console.log(f"[simulate] {' '.join(cmd)}")
            return None
        attempts = 0
        max_attempts = 2
        effective_timeout = self.adb.command_timeout
        while attempts < max_attempts:
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    timeout=effective_timeout,
                    check=True,
                )
                return result.stdout
            except subprocess.TimeoutExpired:
                self.console.log(
                    f"[warning] Captura de pantalla excedió {effective_timeout:.1f}s (intento {attempts + 1}/{max_attempts})"
                )
            except subprocess.CalledProcessError as exc:
                self.console.log(
                    f"[warning] Captura de pantalla falló ({exc.returncode}); reintentando (intento {attempts + 1}/{max_attempts})"
                )
            except OSError as exc:
                self.console.log(
                    f"[warning] Error del sistema al capturar pantalla ({exc}); reintentando"
                )
            attempts += 1
            self._recover_connection()
        self.console.log("[error] No se pudo capturar pantalla tras múltiples intentos")
        raise DeviceCaptureError(
            f"Captura de pantalla fallida para {self.serial} tras {max_attempts} intentos"
        )

    def set_debug_capture(self, callback: Callable[[str], None] | None) -> None:
        """Registra un callback que recibirá etiquetas previas a cada acción."""
        self._debug_capture_callback = callback

    def _record_action_debug(self, reason: str) -> None:
        if not self._debug_capture_callback:
            return
        try:
            self._debug_capture_callback(reason)
        except Exception as exc:
            self.console.log(f"[warning] No se pudo registrar captura de debug ({reason}): {exc}")

    def _recover_connection(self) -> None:
        def raw_run(cmd, timeout=None):
            subprocess.run(cmd, check=True, timeout=timeout)

        try:
            self.console.log(f"Reintentando conexión con {self.serial}")
            raw_run([self._adb_path, "connect", self.serial], timeout=self.adb.connect_timeout)
        except subprocess.CalledProcessError:
            pass
        except subprocess.TimeoutExpired:
            self.console.log(
                f"[warning] Tiempo de espera agotado al reconectar con {self.serial}; se continuará"
            )
        time.sleep(0.5)
        try:
            raw_run([
                self._adb_path,
                "-s",
                self.serial,
                "wait-for-device",
            ], timeout=self.adb.connect_timeout)
        except subprocess.CalledProcessError:
            self.console.log(
                f"[warning] No se logró reconectar con {self.serial}; continuará el error"
            )
        except subprocess.TimeoutExpired:
            self.console.log(
                f"[warning] Esperando {self.serial} excedió {self.adb.connect_timeout:.1f}s; continuará el error"
            )


class BlueStacksInstanceManager:
    """Administra el ciclo de vida de una instancia BlueStacks via CLI."""

    def __init__(self, config: BlueStacksConfig | None, console: Console, simulate: bool = False) -> None:
        self.config = config
        self.console = console
        self.simulate = simulate

    @contextmanager
    def ensure_instance(self, farm: InstanceConfig) -> Iterator[None]:
        """Enciende la instancia antes de usarla y la cierra al finalizar."""
        if not self.config:
            yield
            return

        start_cmd = [
            self.config.player_path,
            "--instance",
            farm.instance,
            "--cmd",
            "launchApp",
        ]
        stop_cmd = [
            self.config.player_path,
            "--instance",
            farm.instance,
            "--cmd",
            "quit",
        ]

        display_name = farm.name or farm.instance
        self.console.log(f"Iniciando granja {display_name}")
        proc: subprocess.Popen[str] | None = None
        if self.simulate:
            self.console.log(f"[simulate] {' '.join(start_cmd)}")
        else:
            proc = subprocess.Popen(
                start_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if self.config.start_timeout > 0:
                time.sleep(self.config.start_timeout)

        try:
            yield
        finally:
            self.console.log(f"Cerrando granja {display_name}")
            if self.simulate:
                self.console.log(f"[simulate] {' '.join(stop_cmd)}")
            else:
                try:
                    subprocess.run(
                        stop_cmd,
                        timeout=self.config.shutdown_timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    self.console.log(
                        f"[warning] El comando de cierre tardó más de {self.config.shutdown_timeout}s"
                    )

                if proc and not self.simulate:
                    self.console.log("Forzando cierre inmediato de HD-Player")
                    player_image = Path(self.config.player_path).name if self.config else None
                    killed = False
                    if proc.poll() is None:
                        killed = self._kill_process_tree(proc.pid)
                    if not killed and player_image:
                        window_open = self._instance_window_exists(display_name, farm)
                        if window_open:
                            killed = self._kill_process_image(player_image)
                    if not killed:
                        self.console.log(
                            "[warning] No se pudo cerrar HD-Player automáticamente; verifica la instancia manualmente"
                        )

    def _kill_process_tree(self, pid: int) -> bool:
        """Intenta cerrar un proceso y sus hijos mediante ``taskkill``."""
        if pid <= 0:
            return False
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return False
        output = (result.stdout or "") + (result.stderr or "")
        message = output.strip()
        if message:
            self.console.log(message)
        return result.returncode == 0

    def _kill_process_image(self, image_name: str) -> bool:
        """Fuerza el cierre por nombre de imagen cuando el PID ya no existe."""
        if not image_name:
            return False
        try:
            result = subprocess.run(
                ["taskkill", "/IM", image_name, "/F", "/T"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return False
        output = (result.stdout or "") + (result.stderr or "")
        message = output.strip()
        if message:
            self.console.log(message)
        return result.returncode == 0

    def _instance_window_exists(self, display_name: str, farm: InstanceConfig) -> bool:
        """Verifica si hay una ventana visible asociada a la instancia (solo Windows)."""
        if self.simulate or not IS_WINDOWS:
            return False
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return False

        targets = set()
        if farm.instance:
            targets.add(farm.instance.lower())
        if farm.name:
            targets.add(farm.name.lower())
        targets.add(display_name.lower())
        targets = {target for target in targets if target}
        if not targets:
            return False

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        found = ctypes.c_bool(False)

        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.lower()
            if any(target in title for target in targets):
                found.value = True
                return False
            return True

        try:
            user32.EnumWindows(EnumWindowsProc(_callback), 0)
        except Exception as exc:  # pragma: no cover - Windows-specific safeguard
            self.console.log(
                f"[warning] No se pudo verificar la ventana de '{display_name}': {exc}"
            )
            return False

        return bool(found.value)


def resolve_button(layout: LayoutConfig, button: str) -> Coord:
    """Ayuda a resolver botones de layouts desde tareas utilitarias."""
    return layout.get(button)
