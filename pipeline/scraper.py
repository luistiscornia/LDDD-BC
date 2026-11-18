from __future__ import annotations
"""Motor de Scraping e Ingesta Automatizada.

Rastrea y descarga de manera transparente:
1 PDFs de Presupuesto Anual (Proyectos de Presupuesto).
2 PDFs de Informes de Ejecución de Recursos y Gastos (Datos Abiertos).
3 PDFs de Normativa Presupuestaria (Ordenanzas Aprobatorias y Modificatorias).
"""
import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RosarioScraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


class RosarioScraper:
    URL_PRESUPUESTO = "https://www.rosario.gob.ar/inicio/index.php/presupuesto"
    URL_DATOS_PRESUPUESTO = "https://datos.rosario.gob.ar/ciudadania-y-estado/presupuesto"
    URL_NORMATIVA_BASE = "https://www.rosario.gob.ar/normativa/verArchivo?tipo=pdf&id="

    # Identificadores de ordenanzas conocidas de 2025 y años clave
    ORDENANZAS_CLAVE = {
        "10747_2024": {"id": "239429", "norma": "Ordenanza 10.747/2024", "ejercicio": "2025", "tipo": "APROBACION"},
        "10748_2024": {"id": "239430", "norma": "Ordenanza 10.748/2024", "ejercicio": "2025", "tipo": "MODIFICACION"},
        "10871_2025": {"id": "255397", "norma": "Ordenanza 10.871/2025", "ejercicio": "2026", "tipo": "APROBACION"},
        "10873_2025": {"id": "255402", "norma": "Ordenanza 10.873/2025", "ejercicio": "2026", "tipo": "MODIFICACION"},
    }

    def __init__(self, directorio_base: Path | str = "datos_crudos"):
        self.directorio_base = Path(directorio_base)
        self.dir_presupuestos = self.directorio_base / "presupuestos"
        self.dir_ejecucion = self.directorio_base / "ejecucion"
        self.dir_normativa = self.directorio_base / "normativa"

        for d in [self.dir_presupuestos, self.dir_ejecucion, self.dir_normativa]:
            d.mkdir(parents=True, exist_ok=True)

    def _fetch_html(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")

    @staticmethod
    def calcular_sha256(ruta: Path) -> str:
        hasher = hashlib.sha256()
        with open(ruta, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def descargar_archivo(self, url: str, destino_path: Path) -> dict[str, Any]:
        """Descarga un archivo con control de idempotencia y cálculo de SHA-256."""
        resultado = {
            "url": url,
            "destino": str(destino_path),
            "nombre": destino_path.name,
            "estado": "PENDIENTE",
            "bytes": 0,
            "sha256": None,
            "error": None,
        }

        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as resp:
                contenido = resp.read()
                nuevo_hash = hashlib.sha256(contenido).hexdigest()

                if destino_path.exists():
                    hash_existente = self.calcular_sha256(destino_path)
                    if hash_existente == nuevo_hash:
                        resultado["estado"] = "OMITIDO_IDENTICO"
                        resultado["bytes"] = len(contenido)
                        resultado["sha256"] = hash_existente
                        logger.info(f"Archivo idéntico ya presente: {destino_path.name}")
                        return resultado

                destino_path.write_bytes(contenido)
                resultado["estado"] = "DESCARGADO"
                resultado["bytes"] = len(contenido)
                resultado["sha256"] = nuevo_hash
                logger.info(f"Descargado con éxito: {destino_path.name} ({len(contenido):,} bytes)")

        except Exception as exc:
            resultado["estado"] = "ERROR"
            resultado["error"] = str(exc)
            logger.error(f"Fallo al descargar {url}: {exc}")

        return resultado

    def escanear_presupuestos(self, anio_minimo: int = 2022) -> list[dict[str, str]]:
        """Extrae enlaces de proyectos de presupuestos de la web principal para la serie multianual."""
        items = []
        try:
            html = self._fetch_html(self.URL_PRESUPUESTO)
            # Buscar links a PDFs de presupuestos
            patron = re.compile(r'href="([^"]*(?:Presupuesto|presupuesto)[^"]*\.pdf)"', re.IGNORECASE)
            enlaces = set(patron.findall(html))

            for link in enlaces:
                # En Drupal, los archivos estáticos en /sites/ se sirven sin /index.php/
                link_corregido = link.replace("/index.php/sites/", "/sites/")
                url_completa = urllib.parse.urljoin("https://www.rosario.gob.ar", link_corregido)
                nombre = urllib.parse.unquote(url_completa.split("/")[-1])
                match_anio = re.search(r"20\d{2}", nombre)
                ejercicio = match_anio.group(0) if match_anio else "DESCONOCIDO"

                if ejercicio != "DESCONOCIDO" and int(ejercicio) < anio_minimo:
                    continue

                items.append({
                    "tipo": "PRESUPUESTO_PROYECTO",
                    "ejercicio": ejercicio,
                    "url": url_completa,
                    "nombre_sugerido": nombre,
                })
        except Exception as exc:
            logger.error(f"Error escaneando presupuestos: {exc}")

        return sorted(items, key=lambda x: x["ejercicio"], reverse=True)

    def escanear_ejecucion(self, anio_minimo: int = 2022) -> list[dict[str, str]]:
        """Extrae enlaces de ejecución presupuestaria del portal de datos para la serie multianual."""
        items = []
        try:
            html = self._fetch_html(self.URL_DATOS_PRESUPUESTO)
            patron = re.compile(r'href="([^"]*(?:Recursos%20y%20Gastos|Recursos\s+y\s+Gastos)[^"]*\.pdf)"', re.IGNORECASE)
            enlaces = set(patron.findall(html))

            for link in enlaces:
                url_completa = urllib.parse.urljoin("https://datos.rosario.gob.ar", link)
                nombre = urllib.parse.unquote(url_completa.split("/")[-1])
                match_anio = re.search(r"20\d{2}", nombre)
                ejercicio = match_anio.group(0) if match_anio else "DESCONOCIDO"

                if ejercicio != "DESCONOCIDO" and int(ejercicio) < anio_minimo:
                    continue

                items.append({
                    "tipo": "EJECUCION_PRESUPUESTARIA",
                    "ejercicio": ejercicio,
                    "url": url_completa,
                    "nombre_sugerido": nombre,
                })
        except Exception as exc:
            logger.error(f"Error escaneando ejecución: {exc}")

        return sorted(items, key=lambda x: x["ejercicio"], reverse=True)

    def ejecutar_ingesta_completa(
        self, anio_minimo: int = 2022, progreso: Callable[[str, int, int], None] | None = None
    ) -> dict[str, Any]:
        """Ejecuta el ciclo de scraping y descarga de las 3 patas para la serie desde anio_minimo."""
        inicio = datetime.now(timezone.utc).isoformat()
        manifiesto = {
            "fecha_ingesta": inicio,
            "presupuestos": [],
            "ejecucion": [],
            "normativa": [],
            "resumen": {"descargados": 0, "omitidos": 0, "errores": 0},
        }

        # Pata Presupuestos (desde anio_minimo)
        logger.info(f"Escaneando presupuestos anuales (>= {anio_minimo})...")
        pres_items = self.escanear_presupuestos(anio_minimo=anio_minimo)
        for idx, item in enumerate(pres_items):
            if progreso:
                progreso("Descargando presupuestos", idx + 1, len(pres_items))
            destino = self.dir_presupuestos / item["nombre_sugerido"]
            res = self.descargar_archivo(item["url"], destino)
            res["ejercicio"] = item["ejercicio"]
            manifiesto["presupuestos"].append(res)

        # Pata Ejecución (desde anio_minimo)
        logger.info(f"Escaneando informes de ejecución (>= {anio_minimo})...")
        ejec_items = self.escanear_ejecucion(anio_minimo=anio_minimo)
        for idx, item in enumerate(ejec_items):
            if progreso:
                progreso("Descargando ejecución", idx + 1, len(ejec_items))
            destino = self.dir_ejecucion / item["nombre_sugerido"]
            res = self.descargar_archivo(item["url"], destino)
            res["ejercicio"] = item["ejercicio"]
            manifiesto["ejecucion"].append(res)

        # Pata Normativa Clave
        logger.info("Descargando normativa aprobatoria y modificatoria...")
        norm_keys = list(self.ORDENANZAS_CLAVE.items())
        for idx, (clave, info) in enumerate(norm_keys):
            if progreso:
                progreso("Descargando normativa", idx + 1, len(norm_keys))
            url = f"{self.URL_NORMATIVA_BASE}{info['id']}"
            nombre_archivo = f"normativa_{info['id']}_{clave}.pdf"
            destino = self.dir_normativa / nombre_archivo
            res = self.descargar_archivo(url, destino)
            res["ejercicio"] = info["ejercicio"]
            res["norma"] = info["norma"]
            res["tipo_norma"] = info["tipo"]
            manifiesto["normativa"].append(res)

        # Totales
        todos = manifiesto["presupuestos"] + manifiesto["ejecucion"] + manifiesto["normativa"]
        for r in todos:
            if r["estado"] == "DESCARGADO":
                manifiesto["resumen"]["descargados"] += 1
            elif r["estado"] == "OMITIDO_IDENTICO":
                manifiesto["resumen"]["omitidos"] += 1
            elif r["estado"] == "ERROR":
                manifiesto["resumen"]["errores"] += 1

        ruta_manifiesto = self.directorio_base / "manifiesto_ingesta.json"
        ruta_manifiesto.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Ingesta finalizada. Manifiesto guardado en: {ruta_manifiesto}")

        return manifiesto


if __name__ == "__main__":
    scraper = RosarioScraper()
    print("Iniciando scraper de Rosario...")
    res = scraper.ejecutar_ingesta_completa()
    print("\nResumen de Ingesta:")
    print(json.dumps(res["resumen"], indent=2))
