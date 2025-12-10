"""Herramientas de vision por computadora para encontrar templates y medir brillo."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from rich.console import Console

from .devices import DeviceController
from .debug import DebugReporter, get_debug_reporter


@dataclass
class VisionHelper:
    device: DeviceController
    console: Console
    farm_name: str | None = None
    debug_reporter: DebugReporter | None = None

    """Encapsula capturas y busquedas de templates asociadas a un dispositivo.

    Se apoya en OpenCV para comparar imagenes y graba capturas de depuracion
    cuando existen metadatos suficientes (nombre de granja y reporter activo).
    """

    def capture(self) -> Optional[np.ndarray]:
        """Captura la pantalla actual del dispositivo como imagen BGR.

        Returns:
            Optional[np.ndarray]: Matriz BGR OpenCV o ``None`` si el dispositivo
            no entrego buffer.
        """
        buffer = self.device.capture_screen()
        if buffer is None:
            return None
        array = np.frombuffer(buffer, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_COLOR)
        return image

    def capture_for_debug(self, reason: str) -> Optional[np.ndarray]:
        """Captura y opcionalmente registra una imagen de depuracion.

        Args:
            reason (str): Texto que explica por que se guardo la captura.

        Returns:
            Optional[np.ndarray]: Imagen capturada o ``None`` si no hubo buffer.
        """
        image = self.capture()
        if image is not None:
            self._record_debug_frame(image, reason)
        return image

    def find_template(
        self,
        template_path: Path,
        threshold: float = 0.85,
        save_debug: bool = False,
    ) -> Optional[Tuple[int, int]]:
        """Busca un template individual y devuelve su centro si aparece.

        Args:
            template_path (Path): Ruta absoluta al template BGR.
            threshold (float, optional): Coincidencia minima de OpenCV.
            save_debug (bool, optional): Si ``True`` escribe imagen con bounding box.

        Returns:
            Optional[Tuple[int, int]]: Coordenadas (x, y) del centro o ``None``.
        """
        result = self.find_any_template(
            [template_path], threshold=threshold, save_debug=save_debug
        )
        if result is None:
            return None
        coords, _ = result
        return coords

    def find_any_template(
        self,
        template_paths: Sequence[Path],
        threshold: float = 0.85,
        save_debug: bool = False,
    ) -> Optional[Tuple[Tuple[int, int], Path]]:
        """Busca el primer template que haga match sobre una captura.

        Args:
            template_paths (Sequence[Path]): Lista de rutas a examinar.
            threshold (float, optional): Coincidencia minima.
            save_debug (bool, optional): Si ``True`` persiste imagen con rectangulo.

        Returns:
            Optional[Tuple[Tuple[int, int], Path]]: Par con coordenadas y template
            que coincidio; ``None`` si ninguno supero el umbral.
        """
        paths = list(template_paths)
        if not paths:
            return None

        screenshot = self.capture()
        if screenshot is None:
            return None

        for template_path in paths:
            if not template_path.exists():
                self.console.log(
                    f"[warning] Template no encontrado: {template_path}"
                )
                continue

            template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
            if template is None:
                self.console.log(
                    f"[warning] No se pudo leer template {template_path}"
                )
                continue

            result = cv2.matchTemplate(
                screenshot, template, cv2.TM_CCOEFF_NORMED
            )
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val < threshold:
                continue

            h, w = template.shape[:2]
            center = (int(max_loc[0] + w / 2), int(max_loc[1] + h / 2))
            self._record_debug_frame(screenshot, f"find-{template_path.stem}")
            if save_debug:
                debug_img = screenshot.copy()
                cv2.rectangle(
                    debug_img,
                    max_loc,
                    (max_loc[0] + w, max_loc[1] + h),
                    (0, 255, 0),
                    2,
                )
                out_path = Path(".vision-debug")
                out_path.mkdir(exist_ok=True)
                filename = f"debug_{template_path.stem}_{int(time.time())}.png"
                cv2.imwrite(str(out_path / filename), debug_img)
            return center, template_path

        return None

    def find_all_templates(
        self,
        template_paths: Sequence[Path],
        threshold: float = 0.85,
        max_results: int = 5,
    ) -> List[Tuple[Tuple[int, int], Path]]:
        """Encuentra multiples coincidencias por template en una sola captura.

        Args:
            template_paths (Sequence[Path]): Templates a revisar.
            threshold (float, optional): Valor minimo de respuesta normalizada.
            max_results (int, optional): Limite de coincidencias acumuladas.

        Returns:
            List[Tuple[Tuple[int, int], Path]]: Lista de centros con ruta de template.
        """
        paths = list(template_paths)
        if not paths or max_results <= 0:
            return []

        screenshot = self.capture()
        if screenshot is None:
            return []

        matches: List[Tuple[Tuple[int, int], Path]] = []
        for template_path in paths:
            if not template_path.exists():
                self.console.log(
                    f"[warning] Template no encontrado: {template_path}"
                )
                continue

            template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
            if template is None:
                self.console.log(
                    f"[warning] No se pudo leer template {template_path}"
                )
                continue

            h, w = template.shape[:2]
            result = cv2.matchTemplate(
                screenshot, template, cv2.TM_CCOEFF_NORMED
            )
            local_matches = self._consume_matches(
                result, w, h, threshold, max_results - len(matches)
            )
            if local_matches:
                self._record_debug_frame(
                    screenshot,
                    f"findall-{template_path.stem}",
                )
            for center in local_matches:
                matches.append((center, template_path))
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break
        return matches

    def best_template_score(
        self,
        template_paths: Sequence[Path],
        image: Optional[np.ndarray] = None,
    ) -> Optional[Tuple[Path, float]]:
        """Calcula el puntaje máximo de coincidencia entre varios templates.

        Args:
            template_paths (Sequence[Path]): Plantillas a evaluar.
            image (Optional[np.ndarray], optional): Captura BGR reutilizable.

        Returns:
            Optional[Tuple[Path, float]]: Template con mayor score y su valor.
        """

        paths = list(template_paths)
        if not paths:
            return None
        screenshot = image if image is not None else self.capture()
        if screenshot is None:
            return None
        best_path: Optional[Path] = None
        best_score = float("-inf")
        for template_path in paths:
            if not template_path.exists():
                self.console.log(f"[warning] Template no encontrado: {template_path}")
                continue
            template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
            if template is None:
                self.console.log(f"[warning] No se pudo leer template {template_path}")
                continue
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > best_score:
                best_score = float(max_val)
                best_path = template_path
        if best_path is None:
            return None
        return best_path, best_score

    @staticmethod
    def _consume_matches(
        result_map: np.ndarray,
        width: int,
        height: int,
        threshold: float,
        max_results: int,
    ) -> List[Tuple[int, int]]:
        """Consume valores maximos del mapa de respuestas evitando solapes.

        Args:
            result_map (np.ndarray): Resultado de ``cv2.matchTemplate``.
            width (int): Ancho del template asociado.
            height (int): Alto del template asociado.
            threshold (float): Minimo aceptado para considerar match.
            max_results (int): Maximo numero de resultados a retornar.

        Returns:
            List[Tuple[int, int]]: Centros de cada deteccion sin solape.
        """
        matches: List[Tuple[int, int]] = []
        working = result_map.copy()
        while len(matches) < max_results:
            _, max_val, _, max_loc = cv2.minMaxLoc(working)
            if max_val < threshold:
                break
            center = (int(max_loc[0] + width / 2), int(max_loc[1] + height / 2))
            matches.append(center)
            cv2.rectangle(
                working,
                max_loc,
                (max_loc[0] + width, max_loc[1] + height),
                -1,
                thickness=-1,
            )
        return matches

    def average_brightness(
        self,
        region: tuple[tuple[float, float], tuple[float, float]] | None = None,
    ) -> Optional[float]:
        """Devuelve el brillo promedio normalizado (0-1) de la captura.

        Args:
            region (tuple[tuple[float, float], tuple[float, float]] | None):
                Coordenadas relativas ((y1, y2), (x1, x2)). ``None`` usa toda la imagen.

        Returns:
            Optional[float]: Brillo promedio en escala 0-1 o ``None`` si no hubo captura.
        """

        screenshot = self.capture()
        if screenshot is None:
            return None

        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        if region:
            (y_start, y_end), (x_start, x_end) = region
            y1 = max(int(h * y_start), 0)
            y2 = min(int(h * y_end), h)
            x1 = max(int(w * x_start), 0)
            x2 = min(int(w * x_end), w)
            if y2 > y1 and x2 > x1:
                gray = gray[y1:y2, x1:x2]

        brightness = float(gray.mean()) / 255.0
        return brightness

    def _record_debug_frame(self, image: np.ndarray, reason: str | None = None) -> None:
        """Entrega la captura al reporter si hay contexto para depurar.

        Args:
            image (np.ndarray): Captura BGR que se desea almacenar.
            reason (str | None): Etiqueta opcional para el registro.
        """
        reporter = self.debug_reporter or get_debug_reporter()
        if not reporter or not self.farm_name or image is None:
            return
        reporter.record_screenshot(self.farm_name, image, reason)

    def wait_for_dim_screen(
        self,
        threshold: float,
        timeout: float,
        poll_interval: float = 0.5,
        region: tuple[tuple[float, float], tuple[float, float]] | None = None,
    ) -> bool:
        """Espera hasta que el brillo promedio caiga bajo un umbral.

        Args:
            threshold (float): Brillo maximo permitido (0-1).
            timeout (float): Segundos maximos de espera.
            poll_interval (float, optional): Pausa entre mediciones.
            region (tuple[tuple[float, float], tuple[float, float]] | None):
                Area relativa a evaluar.

        Returns:
            bool: ``True`` si el brillo bajo del umbral antes del timeout, ``False`` en caso contrario.
        """

        start = time.monotonic()
        while time.monotonic() - start <= timeout:
            brightness = self.average_brightness(region=region)
            if brightness is None:
                return False
            if brightness < threshold:
                self.console.log(
                    f"Brillo detectado {brightness:.2f} < {threshold:.2f}; overlay presente"
                )
                return True
            time.sleep(poll_interval)
        return False

    def wait_for_template(
        self,
        template_path: Path,
        timeout: float,
        poll_interval: float = 2.0,
        threshold: float = 0.85,
        raise_on_timeout: bool = True,
    ) -> Optional[Tuple[int, int]]:
        """Bloquea hasta que aparezca un template especifico o se agote el tiempo.

        Args:
            template_path (Path): Ruta al template unico esperado.
            timeout (float): Segundos maximos de espera.
            poll_interval (float, optional): Pausa entre capturas.
            threshold (float, optional): Coincidencia minima.
            raise_on_timeout (bool, optional): Si ``True`` lanza ``TimeoutError``.

        Returns:
            Optional[Tuple[int, int]]: Coordenadas del centro o ``None`` si no se encontro.

        Raises:
            TimeoutError: Cuando se agota el tiempo y ``raise_on_timeout`` es ``True``.
        """
        result = self.wait_for_any_template(
            [template_path],
            timeout=timeout,
            poll_interval=poll_interval,
            threshold=threshold,
            raise_on_timeout=raise_on_timeout,
        )
        if result is None:
            return None
        coords, _ = result
        return coords

    def wait_for_any_template(
        self,
        template_paths: Sequence[Path],
        timeout: float,
        poll_interval: float = 2.0,
        threshold: float = 0.85,
        raise_on_timeout: bool = True,
    ) -> Optional[Tuple[Tuple[int, int], Path]]:
        """Espera hasta que alguno de los templates aparezca.

        Args:
            template_paths (Sequence[Path]): Coleccion de plantillas candidatas.
            timeout (float): Segundos maximos de espera.
            poll_interval (float, optional): Pausa entre intentos.
            threshold (float, optional): Coincidencia minima por template.
            raise_on_timeout (bool, optional): Controla si se lanza ``TimeoutError``.

        Returns:
            Optional[Tuple[Tuple[int, int], Path]]: Coordenadas y template que coincidieron
            o ``None`` si se agoto el tiempo sin excepcion.

        Raises:
            TimeoutError: Cuando no aparece ningun template y ``raise_on_timeout`` es ``True``.
        """
        paths = list(template_paths)
        if not paths:
            return None

        start = time.monotonic()
        while time.monotonic() - start <= timeout:
            coords = self.find_any_template(paths, threshold=threshold)
            if coords:
                return coords
            time.sleep(poll_interval)
        if raise_on_timeout:
            raise TimeoutError(
                f"Templates {[path.name for path in paths]} no aparecieron en {timeout}s"
            )
        return None
