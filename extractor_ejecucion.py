import os
import re
import sys
from pathlib import Path
import pdfplumber
import pandas as pd
from decimal import Decimal, InvalidOperation
from formato_numeros import exportar_csv

VERSION_EXTRACTOR_EJECUCION = "2.2"


def limpiar_texto(txt: str) -> str:
    """Limpia saltos de línea y espacios repetidos."""
    if not txt:
        return ""
    return re.sub(r"\s+", " ", str(txt)).strip()


def parse_numero(val_str: str) -> Decimal:
    """Convierte cadenas argentinas a Decimal, conservando los centavos."""
    if not val_str:
        return Decimal(0)
    val = str(val_str).strip()
    val = re.sub(r"[^\d,\.-]", "", val)
    if not val:
        return Decimal(0)

    # Formato con coma decimal
    if "," in val:
        val = val.replace(".", "").replace(",", ".")
    else:
        # Puntos como separadores de miles
        if val.count(".") > 1:
            val = val.replace(".", "")
        elif val.count(".") == 1 and len(val.split(".")[-1]) == 3:
            val = val.replace(".", "")

    try:
        return Decimal(val)
    except InvalidOperation:
        return Decimal(0)


def filas_tabla(table):
    """Repartir importes que pdfplumber reúne en una celda de varias filas."""
    filas = [list(row) if row else [] for row in table]
    i = 0
    while i < len(filas):
        row = filas[i]
        if len(row) < 2 or not row[1]:
            i += 1
            continue
        partes = str(row[1]).splitlines()
        if len(partes) <= 1:
            i += 1
            continue
        if not all(re.fullmatch(r"[\d.,+\-]+", p.strip()) for p in partes):
            i += 1
            continue
        # También puede estar fusionada la columna de conceptos: en 2025,
        # Derechos y Contribuciones de mejoras comparten una celda.
        conceptos = str(row[0] or "").splitlines()
        fin = i + 1
        while len(conceptos) < len(partes) and fin < len(filas):
            siguiente = filas[fin]
            if len(siguiente) < 2 or siguiente[1] or not siguiente[0]:
                break
            conceptos.extend(str(siguiente[0]).splitlines())
            fin += 1
        if len(conceptos) != len(partes) or not all(c.strip() for c in conceptos):
            raise ValueError("Una celda contiene varios importes sin filas correspondientes; requiere revisión del PDF.")
        filas[i:fin] = [[concepto.strip(), importe.strip()] for concepto, importe in zip(conceptos, partes)]
        i += len(partes)
    return filas


def filas_caif(texto):
    """Leer el cuadro CAIF sin bordes que extract_tables no reconoce.

    Se restringe al bloque posterior a TOTALES y a importes argentinos
    con dos decimales. Se conservan resultados negativos y ceros.
    """
    activo = False
    filas = []
    for linea in texto.splitlines():
        if "TOTALES" in linea.upper():
            activo = True
            continue
        if not activo:
            continue
        if linea.strip().startswith("Secretaría de Hacienda"):
            break
        encontrado = re.fullmatch(r"(.+?)\s+(-?\d+(?:\.\d{3})*,\d{2})", linea.strip())
        if encontrado:
            filas.append([limpiar_texto(encontrado.group(1)), encontrado.group(2)])
        elif linea.strip():
            raise ValueError(f"No se pudo interpretar una fila CAIF: {linea}")
    if not filas:
        raise ValueError("Se detectó la cuenta CAIF, pero no se pudieron recuperar sus importes.")
    return filas


def extraer_ejecucion_pdf(pdf_path: Path, output_dir: Path, progreso=None):
    """Procesa el PDF de Ejecución de Recursos y Gastos y genera CSVs estructurados."""
    archivo = Path(pdf_path).resolve()
    if not archivo.is_file():
        print(f"\n❌ Error: No se encontró el archivo en:\n   {archivo}")
        return []

    out_path = Path(output_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    recursos_rubro = []
    gastos_objeto = []
    gastos_jurisdiccion = []
    caif_registros = []
    ejercicio = 2024

    with pdfplumber.open(archivo) as pdf:
        # 1. Detectar ejercicio
        for p in pdf.pages[:2]:
            t = p.extract_text() or ""
            m_ej = re.search(r"Ejercicio\s+(\d{4})", t, re.IGNORECASE)
            if m_ej:
                ejercicio = int(m_ej.group(1))
                break

        print(f"📖 Leyendo: {archivo.name}")
        print(f"📅 Ejercicio detectado: {ejercicio}")

        for num_pag, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = page.extract_tables()

            # RECURSOS POR RUBRO
            if "Recursos ejecutados" in text and "Rubro" in text:
                for table in tables:
                    for row in filas_tabla(table):
                        if not row or len(row) < 2:
                            continue
                        col0 = limpiar_texto(row[0])
                        col1 = limpiar_texto(row[1])
                        if not col0 or "Rubro" in col0 or col0.lower() == "total":
                            continue
                        monto = parse_numero(col1)
                        if monto > 0:
                            recursos_rubro.append({
                                "ejercicio": ejercicio,
                                "rubro": col0,
                                "monto_pesos": monto
                            })

            # GASTOS POR OBJETO (INCISOS)
            if "Gastos ejecutados" in text and "Objeto" in text:
                for table in tables:
                    for row in filas_tabla(table):
                        if not row or len(row) < 2:
                            continue
                        col0 = limpiar_texto(row[0])
                        col1 = limpiar_texto(row[1])
                        if not col0 or "Objeto" in col0 or col0.lower() == "total":
                            continue
                        monto = parse_numero(col1)
                        if monto > 0:
                            gastos_objeto.append({
                                "ejercicio": ejercicio,
                                "objeto_inciso": col0,
                                "gasto_devengado_pesos": monto
                            })

            # GASTOS POR JURISDICCIÓN
            if "Gastos ejecutados por jurisdicción" in text:
                lineas = [ln.strip() for ln in text.split("\n") if ln.strip()]
                juris_nombres = []
                montos = []

                for linea in lineas:
                    if any(h in linea.lower() for h in ["gastos ejecutados", "ejercicio", "rosario", "secretaría de hacienda", "municipalidad"]):
                        continue
                    if re.match(r"^\d+[\.,]?\d*$", linea):
                        montos.append(parse_numero(linea))
                    elif re.search(r"[A-ZÁÉÍÓÚÑ]{3,}", linea):
                        juris_nombres.append(linea)

                if len(juris_nombres) == len(montos) and len(juris_nombres) > 0:
                    for j_nom, m_val in zip(juris_nombres, montos):
                        gastos_jurisdiccion.append({
                            "ejercicio": ejercicio,
                            "jurisdiccion": j_nom,
                            "monto_millones_ar": m_val,
                            "monto_pesos_estimado": m_val * 1_000_000
                        })
                else:
                    for linea in lineas:
                        m = re.match(r"^(.*?)\s+(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+)$", linea)
                        if m:
                            nombre = m.group(1).strip()
                            valor = parse_numero(m.group(2))
                            if len(nombre) > 3 and not any(h in nombre.lower() for h in ["gastos ejecutados", "ejercicio", "página", "rosario", "municipalidad"]):
                                gastos_jurisdiccion.append({
                                    "ejercicio": ejercicio,
                                    "jurisdiccion": nombre,
                                    "monto_millones_ar": valor,
                                    "monto_pesos_estimado": valor * 1_000_000
                                })

            # CUENTA CAIF
            if "Cuenta de Ahorro" in text or "CAIF" in text:
                for concepto, importe in filas_caif(text):
                    caif_registros.append({
                        "ejercicio": ejercicio,
                        "concepto": concepto,
                        "monto_pesos": parse_numero(importe),
                    })

            if progreso is not None:
                progreso(num_pag, len(pdf.pages))

    archivos_salida = []

    if gastos_objeto:
        df_obj = pd.DataFrame(gastos_objeto)
        p = out_path / f"ejecucion_gastos_objeto_{ejercicio}.csv"
        exportar_csv(df_obj, p, encoding="utf-8-sig")
        archivos_salida.append(p)

    if gastos_jurisdiccion:
        df_jur = pd.DataFrame(gastos_jurisdiccion).drop_duplicates(subset=["jurisdiccion"])
        p = out_path / f"ejecucion_gastos_jurisdiccion_{ejercicio}.csv"
        exportar_csv(df_jur, p, encoding="utf-8-sig")
        archivos_salida.append(p)

    if recursos_rubro:
        df_rec = pd.DataFrame(recursos_rubro)
        p = out_path / f"ejecucion_recursos_rubro_{ejercicio}.csv"
        exportar_csv(df_rec, p, encoding="utf-8-sig")
        archivos_salida.append(p)

    if caif_registros:
        df_caif = pd.DataFrame(caif_registros)
        p = out_path / f"ejecucion_caif_{ejercicio}.csv"
        exportar_csv(df_caif, p, encoding="utf-8-sig")
        archivos_salida.append(p)

    return archivos_salida


if __name__ == "__main__":
    directorio_base = Path(__file__).resolve().parent

    # Ruta por defecto configurada directamente a tu subcarpeta EJECUCION_GASTOS
    ruta_default_pdf = directorio_base / "EJECUCION_GASTOS" / "Recursos y Gastos Año 2024.pdf"
    ruta_default_salida = directorio_base / "datos_ejecucion"

    # Si se pasa por parámetro usa el parámetro; si no, toma la ruta por defecto
    archivo_in = Path(sys.argv[1]) if len(sys.argv) > 1 else ruta_default_pdf
    destino_in = Path(sys.argv[2]) if len(sys.argv) > 2 else ruta_default_salida

    print(f"Iniciando extracción...")
    print(f"PDF origen : {archivo_in}")
    print(f"Directorio : {destino_in}")

    salidas = extraer_ejecucion_pdf(archivo_in, destino_in)

    if salidas:
        print(f"\n✅ Extracción finalizada. Archivos generados ({len(salidas)}):")
        for s in salidas:
            print(f" ➔ {s.name}")
    else:
        print("\n⚠️ No se pudieron generar archivos. Revisa si el archivo existe en la ruta indicada.")
