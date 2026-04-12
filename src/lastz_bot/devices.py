"""Abstracciones para controlar dispositivos/instancias vía ADB y BlueStacks."""

from __future__ import annotations

import ctypes
import re
import threading
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from ctypes import wintypes

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
    stop_event: threading.Event | None = None

    def __post_init__(self) -> None:
        self.serial = f"{self.adb.host}:{self.device_port}"
        self._adb_path = str(Path(self.adb.executable))
        self._debug_capture_callback: Callable[[str], None] | None = None

    def _check_stop(self) -> None:
        if self.stop_event and self.stop_event.is_set():
            raise DeviceRecoverableError("Detención solicitada")

    def _run(self, args: Iterable[str], timeout: Optional[float] = None) -> None:
        cmd = [self._adb_path, "-s", self.serial, *args]
        self._check_stop()
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

    def _run_with_output(
        self,
        args: Iterable[str],
        *,
        timeout: Optional[float] = None,
        check: bool = True,
    ) -> tuple[str, int]:
        """Ejecuta ADB y devuelve stdout+stderr, con reintento básico."""

        cmd = [self._adb_path, "-s", self.serial, *args]
        if self.simulate:
            self.console.log(f"[simulate] {' '.join(cmd)}")
            return "", 0

        effective_timeout = timeout if timeout is not None else self.adb.command_timeout

        def _exec() -> subprocess.CompletedProcess[str]:
            self._check_stop()
            return subprocess.run(
                cmd,
                timeout=effective_timeout,
                check=check,
                capture_output=True,
                text=True,
            )

        try:
            result = _exec()
            output = (result.stdout or "") + (result.stderr or "")
            return output, result.returncode
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
            result = _exec()
            output = (result.stdout or "") + (result.stderr or "")
            return output, result.returncode
        except subprocess.TimeoutExpired as exc:
            raise DeviceRecoverableError(
                f"Comando ADB excedió {effective_timeout:.1f}s incluso tras reconectar"
            ) from exc
        except subprocess.CalledProcessError as exc:
            if check and exc.returncode in RECOVERABLE_ADB_RETURN_CODES:
                raise DeviceRecoverableError(
                    f"Comando ADB falló con código {exc.returncode}; posible cierre del juego"
                ) from exc
            output = (exc.stdout or "") + (exc.stderr or "")
            return output, exc.returncode

    def wait_for_device(self, timeout: float | None = None) -> None:
        """Conecta y espera hasta que el dispositivo responda a ADB, con reintentos."""
        attempts = 0
        max_attempts = 3
        effective_timeout = timeout or self.adb.connect_timeout
        while True:
            self._check_stop()
            attempts += 1
            self.console.log(f"Conectando a {self.serial}... (intento {attempts}/{max_attempts})")
            try:
                self._run(["connect", self.serial], timeout=self.adb.connect_timeout)
                self.console.log(f"Esperando dispositivo {self.serial}...")
                self._run(["wait-for-device"], effective_timeout)
                return
            except DeviceRecoverableError as exc:
                if attempts >= max_attempts:
                    raise
                self.console.log(f"[warning] {exc}; reintento tras 2s")
                # Reiniciar servidor ADB y dar tiempo a que levante la instancia.
                try:
                    subprocess.run([self._adb_path, "start-server"], check=True, timeout=self.adb.connect_timeout)
                except Exception:
                    pass
                self._check_stop()
                time.sleep(2)

    def tap(self, coord: Coord, label: str = "") -> None:
        """Simula un toque en la coordenada indicada, registrándolo para debug."""
        suffix = f" ({label})" if label else ""
        self._record_action_debug(f"tap-{label or coord}")
        self.console.log(f"Tap en {coord}{suffix}")
        self._run(["shell", "input", "tap", str(coord[0]), str(coord[1])])

    def press_home(self) -> None:
        """Envía KEYCODE_HOME para minimizar la app sin matar la instancia."""

        self._record_action_debug("home")
        self.console.log("Presionando HOME")
        self._run(["shell", "input", "keyevent", "3"])

    def force_stop_app(self, package_name: str) -> bool:
        """Cierra la app de forma controlada con ``am force-stop``."""

        if not package_name:
            return False
        self.console.log(f"Forzando detención de {package_name}")
        output, code = self._run_with_output(
            ["shell", "am", "force-stop", package_name],
            check=False,
        )
        if code == 0:
            return True
        self.console.log(
            f"[warning] No se pudo forzar detención de {package_name} (code {code}): {output.strip() or 'sin salida'}"
        )
        return False

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
        self._check_stop()
        if not self.simulate:
            time.sleep(seconds)

    def capture_screen(self) -> Optional[bytes]:
        """Obtiene un PNG del framebuffer y maneja reintentos básicos."""
        cmd = [self._adb_path, "-s", self.serial, "exec-out", "screencap -p"]
        self._check_stop()
        if self.simulate:
            self.console.log(f"[simulate] {' '.join(cmd)}")
            return None
        attempts = 0
        max_attempts = 2
        effective_timeout = self.adb.command_timeout
        while attempts < max_attempts:
            try:
                self._check_stop()
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
            self._check_stop()
        self.console.log("[error] No se pudo capturar pantalla tras múltiples intentos")
        raise DeviceCaptureError(
            f"Captura de pantalla fallida para {self.serial} tras {max_attempts} intentos"
        )

    def package_installed(self, package_name: str) -> bool:
        """Verifica si un paquete existe consultando ``pm path``."""

        output, code = self._run_with_output(
            ["shell", "pm", "path", package_name],
            check=False,
        )
        if code != 0:
            return False
        return "package:" in output

    def install_apk(self, apk_path: Path, *, replace: bool = True) -> bool:
        """Instala un APK si existe localmente, opcionalmente reemplazando."""

        if not apk_path.exists():
            self.console.log(f"[warning] APK no encontrado en {apk_path}")
            return False
        args = ["install"]
        if replace:
            args.append("-r")
        args.append(str(apk_path))
        self.console.log(f"Instalando APK {apk_path.name} en {self.serial}")
        long_timeout = max(self.adb.command_timeout, 300.0)
        output, code = self._run_with_output(args, check=False, timeout=long_timeout)
        if code == 0:
            self.console.log("[info] Instalación completada")
            return True
        self.console.log(
            f"[warning] Instalación falló (code {code}); salida: {output.strip() or 'sin salida'}"
        )
        return False

    def _cleanup_install_staging(self) -> None:
        """Limpia restos de sesiones de instalación para liberar espacio temporal."""

        cleanup_cmds = [
            ["shell", "rm", "-rf", "/data/app/vmdl*"],
            ["shell", "rm", "-rf", "/data/local/tmp/*.apk"],
            ["shell", "rm", "-rf", "/data/app/*tmp*"]
        ]
        for cmd in cleanup_cmds:
            try:
                self._run(cmd, timeout=self.adb.command_timeout)
            except Exception as exc:  # best-effort; no bloqueo si falla
                self.console.log(
                    f"[warning] No se pudo limpiar staging de instalación ({' '.join(cmd)}): {exc}"
                )

    def _log_storage_usage(self) -> None:
        """Muestra uso de espacio en /data y /sdcard para diagnósticos."""

        try:
            output, _ = self._run_with_output(
                ["shell", "df", "-h", "/data", "/sdcard"], check=False
            )
            text = output.strip()
            if text:
                self.console.log("[info] Uso de espacio (df):\n" + text)
        except Exception as exc:
            self.console.log(f"[warning] No se pudo obtener df: {exc}")

    def _has_min_free_space(self, min_gb: float = 2.0) -> bool:
        """Devuelve True si /data tiene al menos ``min_gb`` libres (best-effort)."""

        try:
            output, _ = self._run_with_output(
                ["shell", "df", "/data"], check=False
            )
            for line in output.splitlines():
                if "/data" in line and any(ch.isdigit() for ch in line):
                    parts = [p for p in line.split() if p]
                    if len(parts) >= 4:
                        avail = parts[3]
                        # Expect formats like "1.4G" or "950M"
                        if avail.lower().endswith("g"):
                            value = float(avail[:-1])
                        elif avail.lower().endswith("m"):
                            value = float(avail[:-1]) / 1024.0
                        else:
                            value = float(avail)
                        return value >= min_gb
        except Exception:
            return True  # No bloquear si no podemos medir
        return False

    def _aggressive_cleanup_for_install(self, package: str | None = None) -> None:
        """Libera espacio eliminando staging, cachés y obb del juego (best-effort)."""

        self.console.log("[info] Limpiando staging y cachés para recuperar espacio")
        cleanup_cmds = [
            ["shell", "pm", "trim-caches", "2048M"],
            ["shell", "rm", "-rf", "/data/app/vmdl*"],
            ["shell", "rm", "-rf", "/data/local/tmp/*.apk"],
            ["shell", "rm", "-rf", "/data/app/*tmp*"],
            ["shell", "rm", "-rf", "/sdcard/Download/*"],
            ["shell", "rm", "-rf", "/sdcard/DCIM/Camera/*"],
            ["shell", "rm", "-rf", "/sdcard/Pictures/*"],
            ["shell", "rm", "-rf", "/sdcard/Movies/*"],
            ["shell", "rm", "-rf", "/sdcard/Android/obb/*"],
            ["shell", "rm", "-rf", "/sdcard/tencent"],
        ]
        if package:
            cleanup_cmds.extend(
                [
                    ["shell", "rm", "-rf", f"/data/data/{package}/cache"],
                    ["shell", "rm", "-rf", f"/data/data/{package}"],
                    ["shell", "rm", "-rf", f"/sdcard/Android/data/{package}/cache"],
                    ["shell", "rm", "-rf", f"/sdcard/Android/data/{package}"],
                    ["shell", "rm", "-rf", f"/sdcard/Android/obb/{package}"],
                    ["shell", "rm", "-rf", f"/sdcard/Android/data/{package}/files/LocalImages"],
                    ["shell", "rm", "-rf", f"/sdcard/Android/data/{package}/files/AssetBundles"],
                    ["shell", "rm", "-rf", f"/data/app/*{package}*"],
                ]
            )
        for cmd in cleanup_cmds:
            try:
                self._run(cmd, timeout=self.adb.command_timeout)
            except Exception as exc:
                self.console.log(
                    f"[warning] No se pudo ejecutar {' '.join(cmd)}: {exc}"
                )
        self._log_storage_usage()

    def _log_top_consumers(self, limit: int = 12) -> None:
        """Muestra los directorios que más ocupan para ayudar a depurar espacio."""

        # Incluimos /data/app (visible sin root) y probamos /data/data con y sin su.
        queries = [
            ("/sdcard", "du -m /sdcard | sort -rn | head -n {limit}"),
            ("/sdcard/Android", "du -m /sdcard/Android | sort -rn | head -n {limit}"),
            (
                "/data",
                "du -m /data 2>/dev/null | sort -rn | head -n {limit} || su 0 du -m /data | sort -rn | head -n {limit}",
            ),
            (
                "/data/app",
                "du -m /data/app 2>/dev/null | sort -rn | head -n {limit} || su 0 du -m /data/app | sort -rn | head -n {limit}",
            ),
            (
                "/data/data",
                "du -m /data/data 2>/dev/null | sort -rn | head -n {limit} || su 0 du -m /data/data | sort -rn | head -n {limit}",
            ),
        ]
        for label, cmd in queries:
            try:
                output, _ = self._run_with_output(
                    ["shell", cmd.format(limit=limit)], check=False
                )
                text = output.strip()
                if text:
                    self.console.log(f"[info] Top espacio {label}:\n{text}")
                else:
                    self.console.log(
                        f"[warning] No se obtuvo salida para top de {label} (posibles permisos)"
                    )
            except Exception as exc:
                self.console.log(
                    f"[warning] No se pudo obtener top de {label}: {exc}"
                )

    def install_apk_bundle(self, apk_dir: Path, package: str | None = None) -> bool:
        """Instala un conjunto de APKs (base + splits) usando install-multiple."""

        if not apk_dir.exists() or not apk_dir.is_dir():
            self.console.log(f"[warning] Directorio de APKs no encontrado: {apk_dir}")
            return False
        apk_files = sorted(
            [p for p in apk_dir.iterdir() if p.suffix.lower() == ".apk"],
            key=lambda p: ("base" not in p.name.lower(), p.name.lower()),
        )
        if not apk_files:
            self.console.log(f"[warning] No hay APKs en {apk_dir} para instalar")
            return False
        if not self._has_min_free_space():
            self.console.log(
                "[warning] Menos de 2GB libres en /data; se intentará limpieza antes de instalar el bundle"
            )
            self._aggressive_cleanup_for_install(package)
            if not self._has_min_free_space():
                self.console.log(
                    "[warning] Sigue habiendo menos de 2GB libres en /data tras limpieza; libera almacenamiento en la instancia"
                )
                self._log_top_consumers()
                return False
        args = ["install-multiple", "-r", *[str(p) for p in apk_files]]
        self.console.log(f"Instalando bundle APK ({len(apk_files)} archivos) desde {apk_dir} en {self.serial}")
        long_timeout = max(self.adb.command_timeout, 300.0)
        output, code = self._run_with_output(args, check=False, timeout=long_timeout)
        if code == 0:
            self.console.log("[info] Instalación múltiple completada")
            return True

        output_text = output.strip()
        self.console.log(
            f"[warning] Instalación múltiple falló (code {code}); salida: {output_text or 'sin salida'}"
        )
        lowered = output_text.lower()
        if "not enough space" in lowered or "create session" in lowered:
            self.console.log("[info] Intentando limpiar staging de instalación y reintentar")
            self._cleanup_install_staging()
            self._aggressive_cleanup_for_install(package)
            retry_output, retry_code = self._run_with_output(args, check=False, timeout=long_timeout)
            if retry_code == 0:
                self.console.log("[info] Instalación múltiple completada tras limpieza de staging")
                return True
            self.console.log(
                f"[warning] Reintento tras limpieza falló (code {retry_code}); salida: {retry_output.strip() or 'sin salida'}"
            )
            if "not enough space" in retry_output.lower():
                self._log_storage_usage()
                self._log_top_consumers()
                self.console.log(
                    "[warning] El dispositivo reporta falta de espacio; libera almacenamiento en la instancia antes de reintentar"
                )
        return False

    def extract_apk(self, package: str, dest_path: Path) -> bool:
        """Extrae el APK instalado de un paquete y lo guarda en dest_path."""

        if not package:
            return False
        long_timeout = max(self.adb.command_timeout, 300.0)
        try:
            output, code = self._run_with_output(
                ["shell", "pm", "path", package], check=False, timeout=long_timeout
            )
            if code != 0 or "package:" not in output:
                self.console.log(
                    f"[warning] No se pudo obtener ruta del APK para {package}: {output.strip() or code}"
                )
                return False
            # Seleccionar preferentemente el base.apk; si no existe, usar la primera ruta
            paths: list[str] = []
            for line in output.strip().splitlines():
                if "package:" in line:
                    candidate = line.split("package:", 1)[1].strip()
                    if candidate:
                        paths.append(candidate)
            if not paths:
                self.console.log(
                    f"[warning] No se encontró ruta remota en la salida de pm path para {package}"
                )
                return False
            base_path = next((p for p in paths if "base.apk" in p.lower()), None)
            remote_path = base_path or paths[0]
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            self.console.log(
                f"[info] Extrayendo APK de {package} desde {remote_path} a {dest_path}"
            )
            self._run(["pull", remote_path, str(dest_path)], timeout=long_timeout)
            if dest_path.exists():
                self.console.log(f"[info] APK respaldada en {dest_path}")
                return True
            self.console.log(
                f"[warning] El archivo no se creó tras el pull: {dest_path}"
            )
            return False
        except DeviceRecoverableError:
            raise
        except Exception as exc:
            self.console.log(
                f"[warning] Falló la extracción del APK de {package}: {exc}"
            )
            return False

    def extract_apk_bundle(self, package: str, dest_dir: Path) -> list[Path]:
        """Extrae todos los APKs (base + splits) de un paquete a un directorio."""

        extracted: list[Path] = []
        if not package:
            return extracted

        long_timeout = max(self.adb.command_timeout, 300.0)
        try:
            output, code = self._run_with_output(
                ["shell", "pm", "path", package], check=False, timeout=long_timeout
            )
            if code != 0 or "package:" not in output:
                self.console.log(
                    f"[warning] No se pudo obtener rutas de APK para {package}: {output.strip() or code}"
                )
                return extracted
            paths: list[str] = []
            for line in output.strip().splitlines():
                if "package:" in line:
                    candidate = line.split("package:", 1)[1].strip()
                    if candidate:
                        paths.append(candidate)
            if not paths:
                self.console.log(
                    f"[warning] No se encontraron rutas remotas para {package}"
                )
                return extracted
            dest_dir.mkdir(parents=True, exist_ok=True)
            self.console.log(
                f"[info] Extrayendo bundle APK de {package} ({len(paths)} archivos) a {dest_dir}"
            )
            for remote_path in paths:
                name = Path(remote_path).name
                local_path = dest_dir / name
                try:
                    self._run(["pull", remote_path, str(local_path)], timeout=long_timeout)
                    if local_path.exists():
                        extracted.append(local_path)
                except DeviceRecoverableError:
                    raise
                except Exception as exc:
                    self.console.log(
                        f"[warning] Falló al extraer {remote_path}: {exc}"
                    )
            return extracted
        except DeviceRecoverableError:
            raise
        except Exception as exc:
            self.console.log(
                f"[warning] Falló la extracción del bundle de {package}: {exc}"
            )
            return extracted

    def get_installed_version(self, package: str) -> tuple[str | None, str | None]:
        """Obtiene (versionName, versionCode) del paquete instalado via dumpsys."""

        long_timeout = max(self.adb.command_timeout, 60.0)
        try:
            output, code = self._run_with_output(
                ["shell", "dumpsys", "package", package], check=False, timeout=long_timeout
            )
        except DeviceRecoverableError:
            raise
        except Exception as exc:
            self.console.log(f"[warning] No se pudo consultar versión instalada de {package}: {exc}")
            return None, None

        if code != 0:
            self.console.log(
                f"[warning] dumpsys package devolvió código {code} para {package}; no se puede leer versión"
            )
            return None, None

        version_name = None
        version_code = None
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("versionName="):
                version_name = line.split("=", 1)[1].strip() or None
            elif line.startswith("versionCode="):
                match = re.match(r"versionCode=(\d+)", line)
                if match:
                    version_code = match.group(1)
        return version_name, version_code

    def _resolve_aapt(self) -> str | None:
        for candidate in ("aapt2", "aapt"):
            path = shutil.which(candidate)
            if path:
                return path
        return None

    def get_apk_version(self, apk_path: Path) -> tuple[str | None, str | None]:
        """Lee (versionName, versionCode) de un APK local usando aapt/aapt2 si está disponible."""

        if not apk_path.exists():
            self.console.log(f"[warning] APK no encontrado para leer versión: {apk_path}")
            return None, None

        aapt = self._resolve_aapt()
        if not aapt:
            self.console.log("[info] No se encontró aapt/aapt2 en PATH; se omitirá comparación de versión local")
            return None, None

        try:
            result = subprocess.run(
                [aapt, "dump", "badging", str(apk_path)],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
        except Exception as exc:
            self.console.log(f"[warning] No se pudo leer versión del APK {apk_path}: {exc}")
            return None, None

        version_name = None
        version_code = None
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            match = re.search(
                r"versionName='([^']+)'", line
            )
            if match:
                version_name = match.group(1)
            match = re.search(r"versionCode='?(\d+)'?", line)
            if match:
                version_code = match.group(1)
            break
        return version_name, version_code

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
            """Ejecuta la logica de `raw_run` dentro del flujo del bot.

            Args:
                cmd: Parametro usado por la rutina.
                timeout: Parametro usado por la rutina.
            Returns:
                object: Resultado de la operacion.
            """
            subprocess.run(cmd, check=True, timeout=timeout)

        try:
            # Reinicia el servidor ADB para evitar puertos en estado rechazado
            raw_run([self._adb_path, "start-server"], timeout=self.adb.connect_timeout)
        except Exception:
            pass

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
        self._skip_shutdown = False

    def request_skip_shutdown(self, reason: str = "") -> None:
        """Marca que no se debe cerrar la instancia en la salida actual."""

        self._skip_shutdown = True
        if reason:
            self.console.log(
                f"[warning] Se evitará cerrar la instancia por: {reason}"
            )

    def force_shutdown_instance(self, farm: InstanceConfig) -> None:
        """Intenta cerrar la instancia aunque no se haya usado el contexto."""

        if not self.config:
            return

        display_name = farm.name or farm.instance
        stop_cmd = [
            self.config.player_path,
            "--instance",
            farm.instance,
            "--cmd",
            "quit",
        ]

        player_image = Path(self.config.player_path).name if self.config else None
        self.console.log(f"[info] Forzando cierre de la instancia {display_name}")
        if self.simulate:
            self.console.log(f"[simulate] {' '.join(stop_cmd)}")
            return

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

        if player_image and self._instance_window_exists(display_name, farm):
            killed = self._kill_process_image(player_image)
            if not killed:
                self.console.log(
                    "[warning] No se pudo cerrar HD-Player automáticamente; verifica la instancia manualmente"
                )

    @contextmanager
    def ensure_instance(
        self,
        farm: InstanceConfig,
        before_shutdown: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        """Enciende la instancia antes de usarla y la cierra al finalizar.

        ``before_shutdown`` se ejecuta justo antes de cerrar la instancia para
        permitir apagar el juego de forma ordenada.
        """
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
        window_exists = self._instance_window_exists(display_name, farm)
        proc: subprocess.Popen[str] | None = None
        if window_exists:
            self.console.log(
                f"[info] Instancia {display_name} ya está abierta; se reutilizará la ventana"
            )
        else:
            self.console.log(
                f"Iniciando granja {display_name} con comando '{' '.join(start_cmd)}'"
            )
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
                self._position_instance_window(display_name, farm)
        self.console.log(
            f"[debug] ensure_instance: ventana {'detectada' if window_exists else 'iniciada'}; start_timeout={self.config.start_timeout}s"
        )

        try:
            yield
        finally:
            if self._skip_shutdown:
                self.console.log(
                    "[warning] Se omite el cierre de la instancia para evitar corrupción (pantalla de carga/actualización)"
                )
                self._skip_shutdown = False
                return

            if before_shutdown:
                try:
                    before_shutdown()
                except Exception as exc:
                    self.console.log(
                        f"[warning] No se pudo cerrar el juego antes de cerrar la instancia: {exc}"
                    )

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

                if not self.simulate:
                    self.console.log("Forzando cierre inmediato de HD-Player")
                    player_image = Path(self.config.player_path).name if self.config else None
                    killed = False
                    if proc and proc.poll() is None:
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
        return bool(self._find_instance_hwnd(display_name, farm))

    def _find_instance_hwnd(
        self, display_name: str, farm: InstanceConfig
    ) -> int | None:
        """Devuelve el handle de la ventana de la instancia, si está visible."""
        if self.simulate or not IS_WINDOWS:
            return None
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return None

        targets = self._build_window_target_tokens(display_name, farm)
        if not targets:
            return None

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        found_hwnd = ctypes.c_void_p()
        matched_title = ctypes.create_unicode_buffer(512)

        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            title_text = ""
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title_text = buffer.value
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            class_name = class_buf.value

            norm_title = self._normalize_title(title_text)
            norm_class = self._normalize_title(class_name)
            if any(token in norm_title or token in norm_class for token in targets):
                # Evitar falsos positivos: solo aceptar clases conocidas de BlueStacks/HD-Player
                banned_classes = {"cabinetwclass", "applicationframewindow"}
                if norm_class in banned_classes:
                    return True

                allowed_class_tokens = {"hdplayer", "bluestacks", "bsplayer", "qwindow"}
                if any(token in norm_class for token in allowed_class_tokens):
                    found_hwnd.value = hwnd
                    matched_title.value = title_text or class_name
                    return False

                # Clase desconocida con título coincidente: ignorar para evitar capturar nuestra propia terminal
                return True
            return True

        try:
            user32.EnumWindows(EnumWindowsProc(_callback), 0)
        except Exception as exc:  # pragma: no cover - Windows-specific safeguard
            self.console.log(
                f"[warning] No se pudo verificar la ventana de '{display_name}': {exc}"
            )
            return None

        if found_hwnd.value:
            self.console.log(
                f"[debug] Ventana encontrada para {display_name}: '{matched_title.value}' (hwnd={found_hwnd.value})"
            )
        return found_hwnd.value or None

    def _position_instance_window(
        self,
        display_name: str,
        farm: InstanceConfig,
        *,
        target_height: int = 600,
    ) -> None:
        """Coloca la ventana en la esquina superior derecha con altura fija."""
        if self.simulate or not IS_WINDOWS:
            return
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return

        hwnd = self._find_instance_hwnd(display_name, farm)
        if not hwnd:
            self.console.log(
                f"[warning] No se encontró ventana visible para {display_name}; títulos visibles: {self._debug_visible_windows(limit=8)}"
            )
            return

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return

        # Ajustar a relación 540x900 ~ 0.6: ancho fijo 360 y alto 600
        width = 360
        height = max(200, int(target_height))
        screen_width = user32.GetSystemMetrics(0)
        margen = 20
        x = max(0, screen_width - width - margen)  # margen derecho de 20px
        y = margen  # margen superior de 20px
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040
        moved = user32.SetWindowPos(hwnd, None, x, y, width, height, SWP_NOZORDER | SWP_SHOWWINDOW)
        if moved:
            self.console.log(
                f"Ventana de {display_name} posicionada en esquina superior derecha (alto {height})"
            )
        else:
            self.console.log(
                f"[warning] SetWindowPos falló para {display_name}; hwnd={hwnd}, x={x}, y={y}, w={width}, h={height}"
            )

    def _build_window_target_tokens(self, display_name: str, farm: InstanceConfig) -> set[str]:
        tokens: set[str] = set()

        def _add(value: str | None) -> None:
            if not value:
                return
            tokens.add(value.lower())
            norm = self._normalize_title(value)
            if norm:
                tokens.add(norm)
                # Para nombres con cero a la izquierda en el sufijo numérico (Farm-00, Farm-01), agrega variante sin el cero extra
                import re

                match = re.match(r"^(.*?)(\d+)$", norm)
                if match:
                    prefix, digits = match.groups()
                    trimmed = digits.lstrip("0") or "0"
                    variant = f"{prefix}{trimmed}"
                    if variant != norm:
                        tokens.add(variant)

        _add(farm.instance)
        _add(farm.name)
        _add(display_name)
        _add(display_name.replace("last z", "lastz") if "last z" in display_name.lower() else display_name)
        tokens.add("hdplayer")  # clase típica de BlueStacks
        tokens = {token for token in tokens if token}
        return tokens

    def _normalize_title(self, value: str | None) -> str:
        if not value:
            return ""
        return "".join(ch.lower() for ch in value if ch.isalnum())

    def _debug_visible_windows(self, *, limit: int = 10) -> str:
        if self.simulate or not IS_WINDOWS:
            return "n/a"
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return "n/a"

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        titles: list[str] = []

        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if title:
                titles.append(title)
            return len(titles) < limit

        try:
            user32.EnumWindows(EnumWindowsProc(_callback), 0)
        except Exception:
            return "error"

        return "; ".join(titles)


def resolve_button(layout: LayoutConfig, button: str) -> Coord:
    """Ayuda a resolver botones de layouts desde tareas utilitarias."""
    return layout.get(button)
