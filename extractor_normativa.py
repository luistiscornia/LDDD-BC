"""Artículos, importes y anexos de las ordenanzas presupuestarias 10747/10748.

Los anexos tienen un perfil de coordenadas específico del PDF aprobado 2025.
Las modificaciones se exportan como disposiciones pendientes de vinculación:
no se aplican automáticamente al proyecto ni se confunden menciones con deltas.
"""
import argparse
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pdfplumber

from formato_numeros import exportar_csv

VERSION_EXTRACTOR_NORMATIVA = "1.0"
BASE = Path(__file__).resolve().parent
FUENTES = {
    "10747": ("239429", "APROBACION_PRESUPUESTO"),
    "10748": ("239430", "MODIFICACION_PRESUPUESTO"),
}
# Página, planilla, cortes entre concepto/columnas, rótulos, primer concepto.
# Coordenadas normalizadas para páginas A4 de los escaneos oficiales.
PERFILES = {
    8: ("1", [210, 300, 400, 480], ["Gastos corrientes", "Gastos de capital", "Aplicaciones financieras", "Total"], "ADMINISTRACIÓN GENERAL"),
    9: ("2", [220, 300, 390, 470], ["Administración Central", "Organismos Descentralizados", "Instituciones de Seguridad Social", "Total"], "ADMINISTRACIÓN GENERAL"),
    10: ("3", [190, 275, 365, 460], ["Administración Central", "Organismos Descentralizados", "Instituciones de Seguridad Social", "Total"], "ORIGEN MUNICIPAL"),
    11: ("4", [450], ["Importe"], "RECURSOS CORRIENTES"),
    12: ("4", [450], ["Importe"], None),
    13: ("5", [280, 370, 465], ["Recursos de Libre Disponibilidad", "Recursos con Afectación Específica", "Total"], "PERSONAL"),
    14: ("6", [240, 320, 420], ["SPVyH", "ILAR", "Total"], "PERSONAL"),
    15: ("7", [240, 340, 445], ["Recursos de Libre Disponibilidad", "Recursos con Afectación Específica", "Total"], "TOTAL DE RECURSOS"),
}
ARTICULO = re.compile(r"^(?:Artículo|Art\.)\s*(\d+)\s*[º°o]?\s*\.?\s*-\s*(.*)$", re.I)
IMPORTE = re.compile(r"\$\s*(\d{1,3}(?:\s*\.\s*\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)")
COL_ARTICULOS = ["id_articulo", "norma", "ejercicio", "documento", "documento_sha256", "fuente_url", "articulo", "pagina_desde", "pagina_hasta", "texto_original"]
COL_IMPORTES = COL_ARTICULOS[:-1] + ["indice_mencion", "monto_original", "monto", "monto_centavos", "rol", "texto_original"]
COL_DISPOSICIONES = COL_ARTICULOS + ["tipo_disposicion", "partidas_mencionadas", "programas_mencionados", "estado_vinculacion"]
COL_ANEXOS = ["id_celda", "norma", "ejercicio", "documento", "documento_sha256", "fuente_url", "pagina_pdf", "planilla", "fila", "concepto", "indice_columna", "columna_monto", "coordenada_y", "monto_original", "monto", "monto_centavos", "estado_monto", "texto_original"]
COL_INCIDENCIAS = ["tipo", "pagina_pdf", "id_celda", "detalle"]


def lineas_palabras(page):
    grupos = []
    for palabra in sorted(page.extract_words(x_tolerance=2, y_tolerance=3), key=lambda w: (w["top"], w["x0"])):
        if not grupos or palabra["top"] - grupos[-1][0]["top"] > 3:
            grupos.append([])
        grupos[-1].append(palabra)
    return [sorted(g, key=lambda w: w["x0"]) for g in grupos]


def monto_celda(raw):
    texto = re.sub(r"\s+", "", raw)
    if not texto or texto in {"-", "–", "—"}:
        return None, "AUSENTE"
    if not re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?", texto):
        return None, "REVISION"
    return Decimal(texto.replace(".", "").replace(",", ".")), "OK"


def extraer_anexo(page, pagina, meta):
    planilla, cortes, columnas, inicio = PERFILES[pagina]
    registros = []
    activo = inicio is None
    fila = 0
    ultima = []
    for words in lineas_palabras(page):
        y = min(w["top"] for w in words)
        texto = " ".join(w["text"] for w in words)
        if not activo:
            if inicio not in texto:
                continue
            activo = True
        if pagina == 12 and y > 170:
            break
        concepto = " ".join(w["text"] for w in words if w["x0"] / page.width * 595.44 < cortes[0])
        valores = []
        for j, corte in enumerate(cortes):
            limite = cortes[j + 1] if j + 1 < len(cortes) else 595.44
            valores.append(" ".join(w["text"] for w in words if corte <= w["x0"] / page.width * 595.44 < limite))
        if not concepto:
            continue
        if not any(valores):
            # Línea de continuación de una etiqueta larga, no otra partida.
            if ultima and y - ultima[0]["coordenada_y"] <= 12:
                for r in ultima:
                    r["concepto"] += " " + concepto
                    r["texto_original"] += "\n" + texto
                continue
        fila += 1
        ultima = []
        for j, (columna, raw) in enumerate(zip(columnas, valores), 1):
            monto, estado = monto_celda(raw)
            registro = dict(meta, id_celda=f"{meta['documento_sha256']}:{pagina}:{fila}:{j}", pagina_pdf=pagina,
                            planilla=planilla, fila=fila, concepto=concepto, indice_columna=j,
                            columna_monto=columna, coordenada_y=round(y, 2), monto_original=raw,
                            monto=monto, monto_centavos=int(monto * 100) if monto is not None else None,
                            estado_monto=estado, texto_original=texto)
            registros.append(registro)
            ultima.append(registro)
        if concepto == "TOTAL" and planilla != "7":
            break
    if not registros:
        raise ValueError(f"No se reconoció el cuerpo de la planilla {planilla}, página {pagina}.")
    return registros


def controles_anexos(registros):
    incidencias = []
    def aviso(tipo, r, detalle):
        incidencias.append(dict(tipo=tipo, pagina_pdf=r["pagina_pdf"], id_celda=r["id_celda"], detalle=detalle))
    for r in registros:
        if r["estado_monto"] == "REVISION":
            aviso("IMPORTE_ILEGIBLE", r, r["monto_original"])
        if r["concepto"] == "TOTAL" and r["estado_monto"] == "AUSENTE":
            aviso("TOTAL_AUSENTE_EN_CAPA_TEXTO", r, r["columna_monto"])
    filas = {}
    for r in registros:
        filas.setdefault((r["pagina_pdf"], r["fila"]), []).append(r)
    for celdas in filas.values():
        if len(celdas) > 1 and celdas[-1]["columna_monto"] == "Total":
            total = celdas[-1]
            if total["estado_monto"] == "OK" and all(c["estado_monto"] != "REVISION" for c in celdas[:-1]):
                diferencia = total["monto_centavos"] - sum(c["monto_centavos"] or 0 for c in celdas[:-1])
                if diferencia:
                    aviso("TOTAL_DISTINTO_COMPONENTES", total, f"Diferencia en centavos: {diferencia}")
    for pagina in [8, 9, 13, 14]:
        por_columna = {}
        for r in registros:
            if r["pagina_pdf"] == pagina:
                por_columna.setdefault(r["indice_columna"], []).append(r)
        for celdas in por_columna.values():
            totales = [r for r in celdas if r["concepto"] == "TOTAL"]
            detalle = [r for r in celdas if r["concepto"] != "TOTAL"]
            if len(totales) == 1 and totales[0]["estado_monto"] == "OK" and all(r["estado_monto"] != "REVISION" for r in detalle):
                diferencia = totales[0]["monto_centavos"] - sum(r["monto_centavos"] or 0 for r in detalle)
                if diferencia:
                    aviso("TOTAL_DISTINTO_FILAS", totales[0], f"Diferencia en centavos: {diferencia}")
    return incidencias


def procesar_normativa(pdf_path, nombre_documento=None, progreso=None):
    path = Path(pdf_path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pdfplumber.open(path) as pdf:
        paginas = [p.extract_text() or "" for p in pdf.pages]
        cabecera = paginas[0]
        match = re.search(r"N[º°]\s*(10\.74[78])", cabecera)
        if not match:
            raise ValueError("El extractor admite las ordenanzas presupuestarias 10.747 y 10.748 de 2024. Este PDF no corresponde a esas normas.")
        numero = match.group(1).replace(".", "")
        identificador, naturaleza = FUENTES[numero]
        meta = dict(norma=f"{numero}/2024", ejercicio=2025, documento=nombre_documento or path.name,
                    documento_sha256=sha, fuente_url=f"https://www.rosario.gob.ar/normativa/ver/visualExterna.do?accion=verNormativa&idNormativa={identificador}")
        articulos = []
        actual = None
        celdas = []
        cobertura = []
        for i, (page, texto) in enumerate(zip(pdf.pages, paginas), 1):
            anexo = numero == "10747" and i in PERFILES
            if anexo:
                celdas.extend(extraer_anexo(page, i, meta))
            else:
                for linea in texto.splitlines():
                    if re.match(r"Sala de Sesiones|Sa .*Se .*ones|SESION DEL|Expte\.", linea, re.I):
                        actual = None
                    m = ARTICULO.match(linea.strip())
                    if m:
                        numero_articulo = int(m.group(1))
                        actual = dict(meta, id_articulo=f"{sha}:art:{numero_articulo}", articulo=numero_articulo,
                                      pagina_desde=i, pagina_hasta=i, texto_original=linea)
                        articulos.append(actual)
                    elif actual is not None:
                        actual["texto_original"] += "\n" + linea
                        actual["pagina_hasta"] = i
            cobertura.append(dict(pagina_pdf=i, estado="ANEXO" if anexo else "TEXTO" if texto else "SIN_TEXTO_EXTRAIBLE", texto_original=texto))
            if progreso:
                progreso(i, len(pdf.pages))
    esperados = 16 if numero == "10747" else 11
    if [a["articulo"] for a in articulos] != list(range(1, esperados + 1)):
        raise ValueError("La secuencia de artículos está incompleta o duplicada; revisar el PDF.")
    importes = []
    disposiciones = []
    for articulo in articulos:
        texto = articulo["texto_original"]
        for i, m in enumerate(IMPORTE.finditer(texto), 1):
            monto, estado = monto_celda(m.group(1))
            if estado != "OK":
                raise ValueError(f"Importe ilegible en artículo {articulo['articulo']}")
            importes.append(dict(articulo, indice_mencion=i, monto_original=m.group(1), monto=monto,
                                 monto_centavos=int(monto * 100), rol="MENCION_MONETARIA_NO_DELTA"))
        if numero == "10748" and articulo["articulo"] < 11:
            tipo = "REASIGNACION_COMPENSADA" if "compénsese" in texto.lower() else "CAMBIO_DENOMINACION" if "denominación" in texto.lower() else "ASIGNACION_A_REVISAR"
            disposiciones.append(dict(articulo, tipo_disposicion=tipo,
                                      partidas_mencionadas=" | ".join(dict.fromkeys(re.findall(r"\b\d{2}(?:\.\d{2}){3}\b", texto))),
                                      programas_mencionados=" | ".join(dict.fromkeys(re.findall(r"Programa\s+(\d+)\b", texto, re.I))),
                                      estado_vinculacion="PENDIENTE_REVISION_Y_VINCULACION"))
    incidencias = controles_anexos(celdas)
    tablas = {
        "articulos": pd.DataFrame(articulos, columns=COL_ARTICULOS),
        "importes_articulos": pd.DataFrame(importes, columns=COL_IMPORTES),
        "disposiciones": pd.DataFrame(disposiciones, columns=COL_DISPOSICIONES),
        "anexos": pd.DataFrame(celdas, columns=COL_ANEXOS),
        "cobertura": pd.DataFrame(cobertura),
        "incidencias": pd.DataFrame(incidencias, columns=COL_INCIDENCIAS),
    }
    for nombre in ["importes_articulos", "anexos"]:
        tablas[nombre]["monto_centavos"] = pd.array(tablas[nombre].monto_centavos, dtype="Int64")
    metadatos = dict(meta, version_extractor=VERSION_EXTRACTOR_NORMATIVA, naturaleza_documento=naturaleza,
                     fecha_sancion="2024-12-19", fecha_promulgacion="2024-12-26", fecha_publicacion="2024-12-30",
                     origen_fechas="Ficha oficial de normativa, verificada el 2026-10-02", incidencias=len(incidencias),
                     apto_aplicacion_automatica=False,
                     advertencia="No sumar menciones monetarias ni cuadros repetidos. Las disposiciones requieren vinculación; las celdas REVISION no tienen importe imputado.")
    return tablas, metadatos


def exportar_normativa(tablas, metadatos, destino):
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    archivos = []
    prefijo = metadatos["norma"].replace("/", "_")
    for nombre, tabla in tablas.items():
        path = destino / f"normativa_{prefijo}_{nombre}.csv"
        exportar_csv(tabla, path, encoding="utf-8-sig")
        archivos.append(path)
    path = destino / f"normativa_{prefijo}_metadatos.json"
    path.write_text(json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8")
    archivos.append(path)
    return archivos


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="?", type=Path, help="PDF de una ordenanza; por defecto procesa los dos complementarios")
    parser.add_argument("--salida", type=Path, default=BASE / "datos_normativa")
    args = parser.parse_args()
    pdfs = [args.pdf] if args.pdf else sorted((BASE / "PRESUPUESTO_2025").glob("normativa_*.pdf"))
    if not pdfs:
        parser.error("No se encontraron PDF complementarios.")
    for pdf in pdfs:
        tablas, metadatos = procesar_normativa(pdf)
        exportar_normativa(tablas, metadatos, args.salida / metadatos["norma"].replace("/", "_"))
        print(f"{pdf.name}: {len(tablas['articulos'])} artículos, {len(tablas['anexos'])} celdas de anexos; {metadatos['incidencias']} incidencias para revisión.")
