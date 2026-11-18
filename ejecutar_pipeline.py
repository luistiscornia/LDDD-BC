#!/usr/bin/env python3
"""Punto de entrada unificado para el Pipeline de Datos Presupuestarios de Rosario.

Uso:
  python3 ejecutar_pipeline.py --todo         # Ejecuta scraper, orquestador y normalizador
  python3 ejecutar_pipeline.py --scraper      # Solo descarga archivos desde portales oficiales
  python3 ejecutar_pipeline.py --extraer      # Solo ejecuta conversión de PDFs a CSVs
  python3 ejecutar_pipeline.py --normalizar   # Solo genera los datasets maestros consolidados
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from pipeline.scraper import RosarioScraper
from pipeline.orchestrator import PipelineOrquestador
from pipeline.normalizer import NormalizadorPresupuesto

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("PipelinePrincipal")


def main():
    parser = argparse.ArgumentParser(description="Pipeline de Ingesta y Normalización Presupuestaria de Rosario")
    parser.add_argument("--todo", action="store_true", help="Ejecutar todas las fases (scraping -> extracción -> normalización)")
    parser.add_argument("--scraper", action="store_true", help="Ejecutar solo el motor de scraping y descarga")
    parser.add_argument("--extraer", action="store_true", help="Ejecutar solo la conversión de PDFs a CSVs")
    parser.add_argument("--normalizar", action="store_true", help="Ejecutar solo el cruce y normalización final")
    parser.add_argument("--forzar", action="store_true", help="Forzar re-extracción de PDFs aunque ya existan CSVs idénticos")

    args = parser.parse_args()

    # Si no se pasa ningún argumento, ejecutar todo por defecto
    if not (args.todo or args.scraper or args.extraer or args.normalizar):
        args.todo = True

    base = Path(__file__).resolve().parent

    print("======================================================================")
    print("🏛️  SISTEMA DE AUDITORÍA Y BILLETERA CIUDADANA - MUNICIPALIDAD DE ROSARIO")
    print("======================================================================\n")

    # 1. Scraping
    if args.todo or args.scraper:
        print("📥 [FASE 1/3] Ejecutando Scraping desde Fuentes Oficiales (Serie 2022-2025)...")
        scraper = RosarioScraper(directorio_base=base / "datos_crudos")
        res_scraper = scraper.ejecutar_ingesta_completa(anio_minimo=2022)
        print(f"   ➔ Presupuestos escaneados : {len(res_scraper['presupuestos'])}")
        print(f"   ➔ Informes de ejecución   : {len(res_scraper['ejecucion'])}")
        print(f"   ➔ Normativas clave        : {len(res_scraper['normativa'])}")
        print(f"   ➔ Resumen: {res_scraper['resumen']['descargados']} descargados, {res_scraper['resumen']['omitidos']} omitidos por idénticos, {res_scraper['resumen']['errores']} errores.\n")

    # 2. Extracción PDF -> CSV
    if args.todo or args.extraer:
        print("⚙️  [FASE 2/3] Ejecutando Orquestador de Extracción PDF ➔ CSV (Serie 2022-2025)...")
        orquestador = PipelineOrquestador(dir_procesados=base / "datos_procesados")
        res_orq = orquestador.orquestar_todo(anios=["2025", "2024", "2023", "2022"], forzar=args.forzar)
        print(f"   ➔ Presupuestos por jurisdicción : {len(res_orq['presupuestos_jurisdicciones'])} archivos CSV")
        print(f"   ➔ Ejecución generada : {len(res_orq['ejecucion'])} archivos CSV")
        print(f"   ➔ Normativa generada : {len(res_orq['normativa'])} archivos CSV\n")

    # 3. Normalización y Cruce Maestro!! ;)
    if args.todo or args.normalizar:
        print("🧹 [FASE 3/3] Ejecutando Normalización y Cruce Maestro...")
        normalizador = NormalizadorPresupuesto(base_dir=base)
        ejercicios = normalizador.ejercicios_disponibles()
        print(f"   ➔ Ejercicios fiscales detectados: {', '.join(ejercicios)}")
        for ej in ejercicios:
            res_norm = normalizador.generar_billetera_ciudadana_master(destino_dir=base / "salida", ejercicio=ej)
            print(f"\n✅ RESULTADOS CONSOLIDADOS (EJERCICIO {res_norm['ejercicio']}):")
            print(f"   • Presupuesto Proyectado Total : $ {res_norm['presupuesto_total_pesos']:,.2f}")
            print(f"   • Gasto Devengado Real         : $ {res_norm['gasto_total_devengado_pesos']:,.2f}")
            print(f"   • Tasa Global de Ejecución     : {res_norm['porcentaje_ejecucion_global']:.2f} %")
            print(f"   • Gasto Anual por Rosarino     : $ {res_norm['gasto_total_por_habitante']:,.2f}")
            print(f"   • Áreas / Secretarías cruce    : {res_norm['areas_analizadas']}")
            print(f"   📂 Generados: {Path(res_norm['archivo_jurisdicciones']).name}")

    print("\n🎯 Pipeline completado con éxito.")


if __name__ == "__main__":
    main()
