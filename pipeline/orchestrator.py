from __future__ import annotations
"""Orquestador de Conversión PDF ➔ CSV.

Coordina la ejecución de los extractores especializados:
- extractor.py (Presupuesto Analítico y Estructura Programática)
- extractor_ejecucion.py (Recursos, Gastos por Jurisdicción, Objeto y CAIF)
- extractor_normativa.py 
"""
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("PipelineOrquestador")


class PipelineOrquestador:
    def __init__(self, dir_procesados: Path | str = "datos_procesados"):
        self.dir_procesados = Path(dir_procesados)
        self.dir_procesados.mkdir(parents=True, exist_ok=True)
        self.dir_presupuestos_csv = self.dir_procesados / "presupuesto"
        self.dir_ejecucion_csv = self.dir_procesados / "ejecucion"
        self.dir_normativa_csv = self.dir_procesados / "normativa"

        for d in [self.dir_presupuestos_csv, self.dir_ejecucion_csv, self.dir_normativa_csv]:
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def calcular_sha256(ruta: Path) -> str:
        hasher = hashlib.sha256()
        with open(ruta, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def procesar_presupuesto_pdf(
        self, ruta_pdf: Path, forzar: bool = False, progreso: Callable[[int, int], None] | None = None
    ) -> dict[str, Any]:
        """Procesa el PDF de presupuesto proyectado usando extractor.py."""
        from extractor import procesar_presupuesto_completo, exportar_dataset

        ruta_pdf = Path(ruta_pdf)
        if not ruta_pdf.exists():
            raise FileNotFoundError(f"No existe el archivo {ruta_pdf}")

        sha256_pdf = self.calcular_sha256(ruta_pdf)
        reporte_path = self.dir_presupuestos_csv / "reporte_validacion.json"

      
        if not forzar and reporte_path.exists():
            try:
                rep = json.loads(reporte_path.read_text(encoding="utf-8"))
                if rep.get("documento_sha256") == sha256_pdf and rep.get("extraccion_sin_errores"):
                    logger.info(f"Presupuesto {ruta_pdf.name} ya procesado y validado. Omitiendo re-extracción.")
                    return rep
            except Exception:
                pass

        logger.info(f"Procesando {ruta_pdf.name} con extractor.py...")
        df = procesar_presupuesto_completo(ruta_pdf, progreso=progreso)
        reporte = exportar_dataset(df, directorio=self.dir_presupuestos_csv)
        logger.info(f"Presupuesto procesado: {reporte['celdas']} celdas generadas en {self.dir_presupuestos_csv}")
        return reporte

    def procesar_ejecucion_pdf(self, ruta_pdf: Path) -> list[Path]:
        """Procesa un PDF de ejecución con extractor_ejecucion.py."""
        from extractor_ejecucion import extraer_ejecucion_pdf

        ruta_pdf = Path(ruta_pdf)
        if not ruta_pdf.exists():
            raise FileNotFoundError(f"No existe el archivo {ruta_pdf}")

        logger.info(f"Procesando ejecución {ruta_pdf.name}...")
        archivos_generados = extraer_ejecucion_pdf(ruta_pdf, self.dir_ejecucion_csv)
        logger.info(f"Ejecución generada: {len(archivos_generados)} archivos en {self.dir_ejecucion_csv}")
        return archivos_generados

    def procesar_normativa_pdf(self, ruta_pdf: Path) -> list[Path]:
        """Procesa un PDF de ordenanza presupuestaria con extractor_normativa.py."""
        from extractor_normativa import procesar_normativa, exportar_normativa

        ruta_pdf = Path(ruta_pdf)
        if not ruta_pdf.exists():
            raise FileNotFoundError(f"No existe el archivo {ruta_pdf}")

        logger.info(f"Procesando normativa {ruta_pdf.name}...")
        try:
            tablas, metadatos = procesar_normativa(ruta_pdf)
            destino = self.dir_normativa_csv / metadatos["norma"].replace("/", "_")
            archivos = exportar_normativa(tablas, metadatos, destino)
            logger.info(f"Normativa {metadatos['norma']} exportada en {destino}")
            return archivos
        except Exception as exc:
            logger.warning(f"Normativa {ruta_pdf.name} omitida o sin perfil tabulado: {exc}")
            return []

    def procesar_presupuestos_jurisdicciones(self, anios: list[str] | None = None, forzar: bool = False) -> list[Path]:
        """Extrae el presupuesto aprobado por jurisdicciones para cada ejercicio de la serie multianual."""
        from pipeline.normalizer import NormalizadorPresupuesto
        base = Path(__file__).resolve().parent.parent
        normalizador = NormalizadorPresupuesto(base_dir=base)

        anios = anios or ["2025", "2024", "2023", "2022"]
        archivos_generados = []
        for anio in anios:
            csv_path = self.dir_presupuestos_csv / f"presupuesto_jurisdicciones_{anio}.csv"
            if not forzar and csv_path.exists() and csv_path.stat().st_size > 50:
                logger.info(f"Presupuesto por jurisdicciones {anio} ya disponible en CSV. Omitiendo.")
                archivos_generados.append(csv_path)
                continue

            logger.info(f"Extrayendo presupuesto {anio} por jurisdicciones desde PDF oficial...")
            res = normalizador.extraer_presupuesto_desde_pdf(anio)
            if res and csv_path.exists():
                archivos_generados.append(csv_path)
        return archivos_generados

    def orquestar_todo(
        self,
        ruta_presupuesto: Path | None = None,
        rutas_ejecucion: list[Path] | None = None,
        rutas_normativa: list[Path] | None = None,
        anios: list[str] | None = None,
        forzar: bool = False,
        progreso: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Ejecuta la orquestación sobre los archivos de la serie multianual (2022 en adelante)."""
        base = Path(__file__).resolve().parent.parent
        anios_objetivo = anios or ["2025", "2024", "2023", "2022"]
        resultado = {
            "presupuestos_jurisdicciones": [],
            "presupuesto_analitico": None,
            "ejecucion": [],
            "normativa": [],
        }

        # Presupuestos por Jurisdicciones (Serie multianual 2022-2025)
        if progreso:
            progreso("Extrayendo presupuestos por secretaría (2022-2025)", 1, 3)
        res_jur = self.procesar_presupuestos_jurisdicciones(anios=anios_objetivo, forzar=forzar)
        resultado["presupuestos_jurisdicciones"] = [str(p) for p in res_jur]

        # Presupuesto Analítico Detallado (2025 o especificado)
        pdf_pres_2025 = ruta_presupuesto or (base / "datos_crudos" / "presupuestos" / "Presupuesto 2025.pdf")
        if not pdf_pres_2025.exists():
            pdf_pres_2025 = base / "PRESUPUESTO_2025" / "Presupuesto 2025.pdf"
        if not pdf_pres_2025.exists():
            pdf_pres_2025 = base / "Presupuesto 2025.pdf"
        if pdf_pres_2025.exists():
            try:
                resultado["presupuesto_analitico"] = self.procesar_presupuesto_pdf(pdf_pres_2025, forzar=forzar)
            except Exception as e:
                logger.warning(f"No se pudo procesar analítico 2025 completo: {e}")

        # Informes de Ejecución (Serie multianual 2022-2025)
        if progreso:
            progreso("Extrayendo ejecuciones de recursos y gastos (2022-2025)", 2, 3)
        if rutas_ejecucion:
            pdfs_ejec = rutas_ejecucion
        else:
            candidatos_ejec = sorted((base / "datos_crudos" / "ejecucion").glob("*.pdf")) + sorted((base / "EJECUCION_GASTOS").glob("*.pdf"))
            vistos = set()
            pdfs_ejec = []
            for c in candidatos_ejec:
                if c.name not in vistos and c.exists():
                    vistos.add(c.name)
                    pdfs_ejec.append(c)

        for p in pdfs_ejec:
            if p.exists():
                generados = self.procesar_ejecucion_pdf(p)
                resultado["ejecucion"].extend([str(g) for g in generados])

        # Normativa Presupuestaria
        if progreso:
            progreso("Procesando normativas presupuestarias", 3, 3)
        if rutas_normativa:
            pdfs_norm = rutas_normativa
        else:
            candidatos_norm = sorted((base / "datos_crudos" / "normativa").glob("*.pdf")) + sorted((base / "PRESUPUESTO_2025").glob("normativa_*.pdf"))
            vistos_n = set()
            pdfs_norm = []
            for c in candidatos_norm:
                if c.name not in vistos_n and c.exists():
                    vistos_n.add(c.name)
                    pdfs_norm.append(c)

        for p in pdfs_norm:
            if p.exists():
                generados = self.procesar_normativa_pdf(p)
                resultado["normativa"].extend([str(g) for g in generados])

        return resultado
