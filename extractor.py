from __future__ import annotations
"""Extracción reproducible de los cuadros monetarios del presupuesto de Rosario.

No mezcla los cuadros analíticos, programáticos, operaciones IMPS y remuneraciones.

La cobertura y los controles viajan en DataFrame.attrs y se exportan con el CSV.
"""
import argparse
import hashlib
import json
import re
from statistics import median
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import pdfplumber

from schema import RegistroPresupuesto, VERSION_ESQUEMA
from formato_numeros import exportar_csv

VERSION_EXTRACTOR = "2.1"
CODIGO_RE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})+|\d{1,10})$")
MONTO_TOKEN_RE = re.compile(r"^(?:[+\-−]?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|[+\-−]?\d+(?:,\d{1,2})?|\([\d.,]+\)|[-–—])$")
ETIQUETA_RE = re.compile(r"^(Act\.\s*(?:Central|Específica|Común)|Proy\.\s*Específico|Programa|Subprograma|Proyecto|Actividad|Obra|Categoría)\s+(\d{1,2})\b", re.I)
CAMPOS_CONTEXTO = {
    "jurisdiccion": r"JURISDICCI[ÓO]N", "programa": "PROGRAMA",
    "subprograma": "SUBPROGRAMA", "proyecto": "PROYECTO", "actividad": "ACTIVIDAD",
    "obra": "OBRA", "fuente_financiamiento": r"FUENTE DE FINANCIAMIENTO|Fuente",
    "tipo_fuente": "TIPO DE FUENTE", "recurso_especifico": "RECURSO ESPECIFICO",
}


def limpiar_monto(valor: str) -> Decimal | None:
    raw = valor.strip().replace(" ", "").replace("−", "-")
    if not raw or raw in {"-", "–", "—"}:
        return None
    if not MONTO_TOKEN_RE.fullmatch(raw):
        return None
    negativo = raw.startswith("(") and raw.endswith(")")
    if negativo:
        raw = raw[1:-1]
    try:
        monto = Decimal(raw.replace(".", "").replace(",", "."))
        return (-monto if negativo else monto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _codigo_normalizado(codigo: str) -> str:
    if not codigo:
        return ""
    if "." in codigo:
        partes = codigo.split(".")
    elif len(codigo) in {5, 9}:
        partes = [codigo[:1]] + [codigo[i:i + 2] for i in range(1, len(codigo), 2)]
    elif len(codigo) in {6, 8, 10}:
        partes = [codigo[i:i + 2] for i in range(0, len(codigo), 2)]
    else:
        partes = [codigo]
    return ".".join(parte.zfill(2) for parte in partes)


def _partes_codigo(codigo: str) -> tuple[int, ...]:
    partes = tuple(int(p) for p in _codigo_normalizado(codigo).split(".")) if codigo else ()
    while partes and partes[-1] == 0:
        partes = partes[:-1]
    return partes


def calcular_nivel(codigo: str) -> int:
    return len(_partes_codigo(codigo))


def extraer_jurisdiccion(texto: str) -> str:
    match = re.search(r"^JURISDICCI[ÓO]N\s*:\s*(.+)$", texto, re.I | re.M)
    return match.group(1).strip() if match else ""


def _sha256_archivo(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _hash(valor) -> str:
    return hashlib.sha256(json.dumps(valor, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _agrupar_palabras(words: list[dict]) -> list[dict]:
    lineas = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not lineas or word["top"] - lineas[-1]["top"] > 2.5:
            lineas.append({"top": word["top"], "words": []})
        lineas[-1]["words"].append(word)
    for linea in lineas:
        linea["words"].sort(key=lambda w: w["x0"])
        linea["text"] = " ".join(w["text"] for w in linea["words"])
    return lineas


def _grupos_horizontales(words, brecha=6):
    grupos = []
    for w in words:
        if not grupos or w["x0"] - grupos[-1][-1]["x1"] > brecha:
            grupos.append([])
        grupos[-1].append(w)
    return grupos


def _fragmentos_encabezado(word, anclas):
    """Separar títulos que pdfminer entrega pegados a través de dos columnas."""
    chars = word.get("chars")
    destino = next((i for i, x in enumerate(anclas) if word["x1"] <= x + 1.8), len(anclas) - 1)
    izquierda = anclas[destino - 1] + 1.8 if destino else float("-inf")
    if word["x0"] >= izquierda:
        return [(destino, word["text"])]
    if not chars:
        return [(destino, word["text"])]
    fragmentos = []
    for char in sorted(chars, key=lambda c: c["x0"]):
        centro = (char["x0"] + char["x1"]) / 2
        k = next((i for i, x in enumerate(anclas) if centro <= x + 0.5), len(anclas) - 1)
        if not fragmentos or fragmentos[-1][0] != k:
            fragmentos.append([k, ""])
        fragmentos[-1][1] += char["text"]
    return fragmentos


def _perfil(lineas):
    texto = "\n".join(l["text"] for l in lineas)
    operacion = "PRESUPUESTO DE OPERACIONES" in texto.upper()
    remuneraciones = "ESCALA DE REMUNERACIONES" in texto.upper()
    for i, l in enumerate(lineas):
        if i > 25:
            break
        words = l["words"]
        etiquetas = [w["text"].upper() for w in words]
        if "TOTAL" not in etiquetas or "GENERAL" in etiquetas:
            continue
        if remuneraciones and "CARGO" in etiquetas:
            return i, "REMUNERACIONES"
        if operacion and "LIBRE" in etiquetas:
            return i, "OPERACIONES_IMPS"
        if re.search(r"Hoja\s*:", texto, re.I) and re.search(r"CONCEPTO|CLASIFICADOR|DESCRIPCI[ÓO]N", l["text"], re.I):
            return i, "PRESUPUESTO"
    return None, ""


def _columnas(lineas, header_i, perfil):
    header = lineas[header_i]
    words = header["words"]
    if perfil == "OPERACIONES_IMPS":
        inicio = next(i for i, w in enumerate(words) if w["text"].upper() == "LIBRE")
    elif perfil == "REMUNERACIONES":
        inicio = next(i for i, w in enumerate(words) if w["text"].upper() == "CARGO") + 1
    else:
        inicio = next(i for i, w in enumerate(words) if w["text"].upper() == "TOTAL")
    grupos = _grupos_horizontales(words[inicio:])
    if not grupos:
        raise ValueError("Encabezado sin columnas monetarias")
    anclas = [g[-1]["x1"] for g in grupos]
    columnas = [{"x": x, "nombre": " ".join(w["text"] for w in grupo)} for x, grupo in zip(anclas, grupos)]
    # El encabezado multinivel sigue inmediatamente debajo (6 a 10,5 puntos).
    for l in lineas[header_i + 1:]:
        if l["top"] - header["top"] > 25:
            break
        if _inicio_fila(l, perfil):
            break
        for w in l["words"]:
            if w["x0"] < grupos[0][0]["x0"] - 20 or not re.search(r"[A-Za-zÁÉÍÓÚáéíóú]", w["text"]):
                continue
            for k, fragmento in _fragmentos_encabezado(w, anclas):
                columnas[k]["nombre"] += " " + fragmento
    # Etiqueta alineada a la derecha; una frontera por celda evita desplazar huecos.
    for col in columnas:
        col["nombre"] = " ".join(col["nombre"].split())
        col["dimension"] = "MONTO"
        col["codigo_dimension"] = ""
        m = re.match(r"(JUR|FINALIDAD)\s+(\d+)\b", col["nombre"])
        if m:
            col["dimension"] = "JURISDICCION" if m[1] == "JUR" else "FINALIDAD"
            col["codigo_dimension"] = m[2].zfill(2)
        elif col["nombre"].upper().startswith("TOTAL"):
            col["dimension"] = "TOTAL"
        elif "RECURSOS" in col["nombre"].upper() or perfil == "OPERACIONES_IMPS" and "TOTAL" not in col["nombre"].upper():
            col["dimension"] = "FUENTE_FINANCIAMIENTO"
    # En operaciones y salarios los títulos no comparten exactamente la
    # alineación de los números. Ajustar con el extremo derecho observado,
    # manteniendo las columnas vacías definidas por el encabezado!!!
    posiciones = [[] for _ in columnas]
    for linea in lineas[header_i + 1:]:
        if linea["top"] < header["top"] + 15:
            continue
        for w in reversed(linea["words"]):
            if not MONTO_TOKEN_RE.fullmatch(w["text"]):
                break
            k = min(range(len(anclas)), key=lambda i: abs(w["x1"] - anclas[i]))
            distancia = min((abs(x - anclas[k]) for i, x in enumerate(anclas) if i != k), default=100)
            if abs(w["x1"] - anclas[k]) < distancia / 2:
                posiciones[k].append(w["x1"])
    for col, valores in zip(columnas, posiciones):
        if valores:
            col["x"] = median(valores)
    return columnas


def _inicio_fila(linea, perfil):
    t = linea["text"]
    if re.match(r"^TOTAL\s+(?:GENERAL|DE RECURSOS)\b", t, re.I):
        return "", "TOTAL", 0
    if perfil in {"OPERACIONES_IMPS", "REMUNERACIONES"}:
        return None
    m = ETIQUETA_RE.match(t)
    if m:
        return m[2], m[1].upper(), m.end()
    first = linea["words"][0]["text"] if linea["words"] else ""
    if CODIGO_RE.fullmatch(first):
        return first, "CODIGO", len(first)
    return None


def _metadatos(lineas, header_i, perfil, ejercicio, naturaleza):
    encabezado = "\n".join(l["text"] for l in lineas[:header_i])
    texto = "\n".join(l["text"] for l in lineas)
    balance = "REMUNERACIONES" if perfil == "REMUNERACIONES" else "RECURSOS" if re.search(r"PRESUPUESTO (?:DE RECURSOS|DE OPERACIONES - RECURSOS)", encabezado, re.I) else "GASTOS"
    titulos = [re.sub(r"\s*Hoja\s*:\s*\d+", "", l["text"], flags=re.I).strip()
               for l in lineas[:header_i]
               if re.match(r"ESTADO POR|ESTRUCTURA PROGRAMATICA|PLAN DE TRABAJOS|CLASIFICACION|GASTO DESTINADO", l["text"], re.I)]
    title = " | ".join(titulos) or perfil
    caracter = ""
    m = re.search(r"^(?:CONSOLIDADO\s+|Caracter\s*:\s*)?(ADMINISTRACION CENTRAL|ORGANISMOS DESCENTRALIZADOS|INSTITUCIONES DE SEGURIDAD SOCIAL)\s*$", encabezado, re.I | re.M)
    if m:
        caracter = m[1].upper()
    else:
        m = re.search(r"^Caracter\s*:\s*(.+)$", encabezado, re.I | re.M)
        if m:
            caracter = m[1]
    if perfil == "OPERACIONES_IMPS":
        caracter = "INSTITUCIONES DE SEGURIDAD SOCIAL"
    family = "PROGRAMATICO" if "PROGRAMATICA" in title and "OBJETO" not in title else "ECONOMICO" if re.search(r"ECON[ÓO]MIC[OA]", title, re.I) else "OBJETO" if re.search(r"OBJETO|TRABAJOS PUBLICOS", title) else "FINALIDAD" if re.search(r"FINALIDADES|x FINALIDAD", title, re.I) else "RUBRO" if "RUBRO" in title.upper() else "PROCEDENCIA" if "PROCEDENCIA" in title.upper() else "FUENTE_FINANCIAMIENTO" if balance == "RECURSOS" else "INSTITUCIONAL"
    meta = dict(ejercicio=ejercicio, naturaleza_documento=naturaleza, tipo_balance=balance,
                tipo_cuadro=title, familia_clasificador=family, caracter_institucional=caracter)
    for field, pattern in CAMPOS_CONTEXTO.items():
        match = re.search(r"^(?:" + pattern + r")\s*:\s*(.+)$", encabezado, re.I | re.M)
        meta[field] = match[1].strip() if match else ""
    if perfil == "OPERACIONES_IMPS":
        meta["jurisdiccion"] = "INSTITUTO MUNICIPAL DE PREVISION SOCIAL"
    meta["jurisdiccion_codigo"] = _codigo_jurisdiccion(meta["jurisdiccion"])
    hoja = re.search(r"Hoja\s*:\s*(\d+)", texto, re.I)
    return meta, hoja[1] if hoja else ""


def _codigo_jurisdiccion(nombre):
    m = re.match(r"(\d{1,2})\b", nombre)
    return m[1].zfill(2) if m else ""


def _extraer_tabla(lineas, header_i, perfil, columnas):
    filas, incidencias = [], []
    numeric_words = []
    for l in lineas[header_i + 1:]:
        if _inicio_fila(l, perfil) or perfil in {"OPERACIONES_IMPS", "REMUNERACIONES"}:
            for w in reversed(l["words"]):
                if not MONTO_TOKEN_RE.fullmatch(w["text"]):
                    break
                numeric_words.append(w)
    ancho = columnas[1]["x"] - columnas[0]["x"] if len(columnas) > 1 else 100
    # Usar los límites reales de los números; las descripciones a veces pueden ser muy largas (revisar!!!)
    candidatos = [w["x0"] for w in numeric_words if abs(w["x1"] - columnas[0]["x"]) < 3]
    izquierda = min(candidatos) - 1 if candidatos else columnas[0]["x"] - ancho + 1
    header_y = lineas[header_i]["top"]
    for l in lineas[header_i + 1:]:
        if l["top"] <= header_y + 15:
            continue
        if not re.search(r"[A-Za-zÁÉÍÓÚáéíóú\d]", l["text"]):
            continue
        inicio = _inicio_fila(l, perfil)
        montos = [w for w in l["words"] if w["x1"] >= columnas[0]["x"] - 2
                  and re.fullmatch(r"[\d.,()+\-−–—]+", w["text"])]
        palabras_descripcion = [w for w in l["words"] if w not in montos]
        if inicio is None and perfil in {"OPERACIONES_IMPS", "REMUNERACIONES"}:
            desc_words = palabras_descripcion
            if desc_words:
                # Encabezados de escalafón o grupos sin importes no son hechos monetarios.
                has_amount = bool(montos)
                if has_amount or l["text"].startswith("TOTAL GENERAL"):
                    inicio = ("", "SIN_JERARQUIA", 0)
        if inicio is None:
            if not filas:
                continue
            # Renglones adicionales de una descripción o de un importe partido.
            extras = [w for w in palabras_descripcion if re.search(r"[A-Za-zÁÉÍÓÚáéíóú]", w["text"])]
            numeric = montos
            if extras and l["top"] - filas[-1]["ultima_y"] <= 15:
                filas[-1]["descripcion"] += " " + " ".join(w["text"] for w in extras)
            elif not numeric:
                continue
            if l["top"] - filas[-1]["ultima_y"] > 15:
                incidencias.append({"tipo_incidencia": "LINEA_NO_ASIGNADA", "texto_original": l["text"]})
                continue
            fila = filas[-1]
        else:
            codigo, clase, longitud = inicio
            desc = " ".join(w["text"] for w in palabras_descripcion)
            if longitud:
                # Quita código o etiqueta, conservando literalmente el concepto restante.
                desc = desc[longitud:].strip()
            fila = {"codigo_original": codigo, "clase": clase, "descripcion": desc,
                    "top": l["top"], "ultima_y": l["top"], "texto_original": "", "celdas": [[] for _ in columnas]}
            filas.append(fila)
        fila["texto_original"] += (" | " if fila["texto_original"] else "") + l["text"]
        fila["ultima_y"] = l["top"]
        for w in montos:
            # Fronteras por extremo derecho, no por orden de los tokens presentes.
            k = next((i for i, c in enumerate(columnas) if w["x1"] <= c["x"] + 2), None)
            if k is None:
                incidencias.append({"tipo_incidencia": "TOKEN_FUERA_DE_COLUMNA", "texto_original": l["text"]})
                continue
            fila["celdas"][k].append(w["text"])
    return filas, incidencias


def _jerarquia(df, paths):
    """Resolver padres dentro de la misma vista y contexto, incluyendo continuaciones."""
    if df.empty:
        return df
    rows = df.drop_duplicates("id_registro")
    children = {}
    for r in rows.itertuples():
        path = paths[r.id_registro]
        if path:
            children.setdefault(r.id_tabla, {})[path] = r.id_registro
    parents, tipos = {}, {}
    totales = {}
    for r in rows[rows.tipo_fila == "TOTAL"].itertuples():
        totales.setdefault(r.id_tabla, []).append(r.id_registro)
    for r in rows.itertuples():
        path = paths[r.id_registro]
        mapping = children.get(r.id_tabla, {})
        parent = next((mapping[path[:n]] for n in range(len(path) - 1, 0, -1) if path[:n] in mapping), "")
        generales = totales.get(r.id_tabla, [])
        if not parent and path and len(generales) == 1 and generales[0] != r.id_registro:
            parent = generales[0]
        parents[r.id_registro] = parent
        if r.tipo_fila == "TOTAL":
            tipos[r.id_registro] = "TOTAL"
        elif not path:
            tipos[r.id_registro] = "SIN_JERARQUIA"
        else:
            tipos[r.id_registro] = "PARTIDA"
    for parent in parents.values():
        if parent and tipos[parent] != "TOTAL":
            tipos[parent] = "SUBTOTAL"
    df["id_padre"] = df.id_registro.map(parents)
    df["tipo_fila"] = df.id_registro.map(tipos)
    df["es_total"] = df.tipo_fila.isin(["TOTAL", "SUBTOTAL"])
    codes = rows.set_index("id_registro").clasificador.to_dict()
    df["codigo_padre"] = df.id_padre.map(codes).fillna("")
    return df


def procesar_paginas(paginas, documento, documento_sha256, progreso=None):
    registros, cobertura, incidencias, paths = [], [], [], {}
    first_text = "\n".join(p["texto"] for p in paginas[:4])
    match = re.search(r"EJERCICIO\s+(20\d{2})|PRESUPUESTO\s+(20\d{2})", first_text, re.I)
    if not match:
        raise ValueError("No se pudo identificar el ejercicio fiscal en el PDF")
    ejercicio = int(next(g for g in match.groups() if g))
    naturaleza = "PROYECTO_DE_PRESUPUESTO" if "PROYECTO DE PRESUPUESTO" in first_text.upper() else "PRESUPUESTO"
    estado_programatico = {}
    raiz_mixta = ()
    grupo_anterior = None
    for p in paginas:
        n, lineas, texto = p["pagina"], p["lineas"], p["texto"]
        header_i, perfil = _perfil(lineas)
        cobertura_p = {"pagina_pdf": n, "estado": "EXCLUIDA", "motivo": "TEXTO_O_SEPARADOR", "filas": 0, "columnas": 0, "celdas": 0}
        if header_i is None:
            if not texto.strip():
                cobertura_p["motivo"] = "SIN_TEXTO_EXTRAIBLE"
            elif "PLANTA DE PERSONAL" in texto.upper():
                cobertura_p["motivo"] = "PLANTA_DE_PERSONAL_CANTIDADES_NO_MONETARIAS"
            elif re.search(r"Hoja\s*:", texto, re.I):
                cobertura_p.update(estado="ERROR", motivo="TABLA_SIN_PERFIL")
                incidencias.append({"pagina_pdf": n, "tipo_incidencia": "TABLA_SIN_PERFIL", "texto_original": texto[:300]})
            cobertura.append(cobertura_p)
            continue
        columnas = _columnas(lineas, header_i, perfil)
        meta, hoja = _metadatos(lineas, header_i, perfil, ejercicio, naturaleza)
        filas, problemas = _extraer_tabla(lineas, header_i, perfil, columnas)
        incidencias.extend(dict(problema, pagina_pdf=n) for problema in problemas)
        # Control independiente de cobertura: cada token monetario impreso
        # debe aparecer una vez en las celdas, aunque la fila no se reconozca.
        patron_importe = r"[+\-−]?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"
        esperados = Counter(w["text"] for l in lineas[header_i + 1:] for w in l["words"]
                           if re.fullmatch(patron_importe, w["text"]))
        recuperados = Counter(fragmento for fila in filas for celda in fila["celdas"] for fragmento in celda
                              if re.fullmatch(patron_importe, fragmento))
        for tipo, diferencias in [("IMPORTE_OMITIDO", esperados - recuperados), ("IMPORTE_REPETIDO", recuperados - esperados)]:
            for raw, cantidad in diferencias.items():
                incidencias.append({"pagina_pdf": n, "tipo_incidencia": tipo, "monto_original": raw,
                                    "texto_original": f"{cantidad} ocurrencias sin correspondencia entre PDF y celdas"})
        grupo = _hash([documento_sha256, meta, [c["nombre"] for c in columnas]])
        programatico = meta["familia_clasificador"] == "PROGRAMATICO"
        if grupo != grupo_anterior:
            estado_programatico = {}
            raiz_mixta = ()
        grupo_anterior = grupo
        for fila in filas:
            row_meta = dict(meta)
            clase = fila["clase"]
            original = fila["codigo_original"]
            codigo = _codigo_normalizado(original) if clase == "CODIGO" else original
            path = tuple(str(i) for i in _partes_codigo(original)) if clase == "CODIGO" else ()
            mixto = meta["familia_clasificador"] in {"OBJETO", "FINALIDAD"} and "Cod.Institucional" in lineas[header_i]["text"]
            if mixto and clase == "CODIGO":
                if "." in original:
                    raiz_mixta = path
                    row_meta["familia_clasificador"] = "INSTITUCIONAL"
                else:
                    path = raiz_mixta + (meta["familia_clasificador"] + ":" + codigo,)
            if programatico:
                if clase == "CODIGO":
                    estado_programatico = {}
                    partes = _partes_codigo(original)
                    if len(partes) == 2:
                        estado_programatico["jurisdiccion"] = f"{str(partes[1]).zfill(2)} {fila['descripcion']}"
                    estado_programatico["_raiz"] = tuple(str(i) for i in partes)
                    path = estado_programatico["_raiz"]
                else:
                    clave = "programa" if clase in {"PROGRAMA", "CATEGORÍA", "ACT. CENTRAL", "ACT. COMÚN"} else "subprograma" if clase == "SUBPROGRAMA" else "proyecto" if clase in {"PROYECTO", "PROY. ESPECÍFICO"} else "obra" if clase == "OBRA" else "actividad"
                    niveles = ["programa", "subprograma", "proyecto", "actividad", "obra"]
                    for campo in niveles[niveles.index(clave):]:
                        estado_programatico.pop(campo, None)
                        estado_programatico.get("_codigos", {}).pop(campo, None)
                    estado_programatico[clave] = f"{clase} {original} {fila['descripcion']}"
                    etiqueta = clase.replace(".", "").replace(" ", "_")
                    estado_programatico.setdefault("_codigos", {})[clave] = f"{etiqueta}_{original.zfill(2)}"
                    path = estado_programatico.get("_raiz", ()) + tuple(f"{k}:{estado_programatico['_codigos'][k]}" for k in niveles if k in estado_programatico)
                    if not estado_programatico.get("_raiz"):
                        incidencias.append({"pagina_pdf": n, "tipo_incidencia": "CONTEXTO_PROGRAMATICO_AUSENTE", "texto_original": fila["texto_original"]})
                    codigo = "/".join(path)
                row_meta.update({k: v for k, v in estado_programatico.items() if not k.startswith("_")})
                row_meta["jurisdiccion_codigo"] = _codigo_jurisdiccion(row_meta["jurisdiccion"])
            elif meta["familia_clasificador"] == "INSTITUCIONAL" and re.search(r"JURISDICCION(?:ES)?", meta["tipo_cuadro"]):
                partes = _partes_codigo(original)
                if len(partes) == 2:
                    row_meta["jurisdiccion_codigo"] = str(partes[1]).zfill(2)
                    row_meta["jurisdiccion"] = fila["descripcion"]
            tipo = "TOTAL" if clase == "TOTAL" or clase == "CODIGO" and (not _partes_codigo(original) or mixto and "." in original and len(_partes_codigo(original)) == 1) else "PARTIDA" if path else "SIN_JERARQUIA"
            if programatico and clase == "CODIGO" and len(path) == 1:
                tipo = "TOTAL"
            registro_id = _hash([documento_sha256, n, fila["top"], original, fila["descripcion"]])
            paths[registro_id] = path
            for k, col in enumerate(columnas):
                fragments = fila["celdas"][k]
                raw = "".join(fragments)
                monto = limpiar_monto(raw)
                estado = "AUSENTE" if not raw else "SIN_DATO" if raw in {"-", "–", "—"} else "OK" if monto is not None else "ERROR"
                if estado == "ERROR":
                    incidencias.append({"pagina_pdf": n, "tipo_incidencia": "MONTO_NO_INTERPRETABLE", "texto_original": fila["texto_original"], "indice_columna": k + 1, "monto_original": raw})
                registro = RegistroPresupuesto(
                    id_registro=registro_id, id_tabla=grupo, documento=documento,
                    documento_sha256=documento_sha256, pagina_pdf=n, hoja=hoja,
                    **row_meta, clasificador=codigo, clasificador_original=original,
                    descripcion=fila["descripcion"], nivel_jerarquico=len(path) if programatico else calcular_nivel(original),
                    tipo_fila=tipo, es_total=tipo == "TOTAL", indice_columna=k + 1,
                    columna_monto=f"MONTO_{k + 1:02d}", encabezado_columna=col["nombre"],
                    dimension_columna=col["dimension"], codigo_dimension_columna=col["codigo_dimension"],
                    encabezado_columnas=" | ".join(c["nombre"] for c in columnas),
                    coordenada_x=col["x"], coordenada_y=fila["top"],
                    monto_original=raw, monto=monto, monto_centavos=int(monto * 100) if monto is not None else None,
                    estado_monto=estado, texto_original=fila["texto_original"],
                )
                registros.append(registro.model_dump())
        cobertura_p.update(estado="EXTRAIDA" if filas else "ERROR", motivo=perfil if filas else "TABLA_SIN_FILAS", filas=len(filas), columnas=len(columnas), celdas=len(filas) * len(columnas),
                           importes_pdf=sum(esperados.values()), importes_recuperados=sum(recuperados.values()))
        cobertura.append(cobertura_p)
        if not filas:
            incidencias.append({"pagina_pdf": n, "tipo_incidencia": "TABLA_SIN_FILAS", "texto_original": texto[:300]})
        if progreso:
            progreso(n, len(paginas))
    df = pd.DataFrame(registros, columns=list(RegistroPresupuesto.model_fields))
    if df.empty:
        raise ValueError("No se encontraron cuadros monetarios compatibles. Revisá el formato o si el PDF requiere OCR.")
    df = _jerarquia(df, paths)
    # Mantener enteros y nulos: pandas no debe convertir centavos a float.
    df["monto_centavos"] = pd.array(df["monto_centavos"], dtype="Int64")
    df.attrs.update(cobertura=cobertura, incidencias_extraccion=incidencias,
                    version_extractor=VERSION_EXTRACTOR, version_esquema=VERSION_ESQUEMA,
                    ejercicio=ejercicio, naturaleza_documento=naturaleza)
    return df


def procesar_presupuesto_completo(pdf_path: str, nombre_documento=None, progreso=None):
    ruta = Path(pdf_path)
    paginas = []
    with pdfplumber.open(ruta) as pdf:
        total = len(pdf.pages)
        for n, page in enumerate(pdf.pages, 1):
            # 0,5 puntos separa los importes vecinos de los cuadros apaisados.
            lineas = _agrupar_palabras(page.extract_words(x_tolerance=0.5, y_tolerance=2, return_chars=True))
            # Solo se necesitan caracteres de los encabezados; evitar retener
            # metadatos de cada dígito de las 1.038 páginas en memoria.
            header_i, _ = _perfil(lineas)
            header_y = lineas[header_i]["top"] if header_i is not None else -100
            for linea in lineas:
                for word in linea["words"]:
                    if not header_y <= linea["top"] <= header_y + 25:
                        word.pop("chars", None)
            paginas.append({"pagina": n, "lineas": lineas, "texto": "\n".join(l["text"] for l in lineas)})
            page.close()
            if progreso:
                progreso(n, total)
    return procesar_paginas(paginas, nombre_documento or ruta.name, _sha256_archivo(ruta))


def exportar_dataset(df, directorio="salida"):
    from auditor import auditar_dataframe
    destino = Path(directorio)
    destino.mkdir(parents=True, exist_ok=True)
    incidencias = auditar_dataframe(df)
    for filename, datos in {
        "presupuesto_consolidado_total.csv": df,
        "presupuesto_partidas_detalle.csv": df[df.tipo_fila == "PARTIDA"],
        "presupuesto_resumen_incisos.csv": df[(df.familia_clasificador == "OBJETO") & (df.nivel_jerarquico == 1)],
        "presupuesto_remuneraciones.csv": df[df.tipo_balance == "REMUNERACIONES"],
    }.items():
        exportar_csv(datos, destino / filename, encoding="utf-8")
    pd.DataFrame(df.attrs["cobertura"]).to_csv(destino / "cobertura_paginas.csv", index=False)
    incidencias.to_csv(destino / "incidencias_auditoria.csv", index=False)
    reporte = {
        "version_esquema": VERSION_ESQUEMA, "version_extractor": VERSION_EXTRACTOR,
        "formato_importes": "1.234.567,89",
        "ejercicio": df.attrs["ejercicio"], "naturaleza_documento": df.attrs["naturaleza_documento"],
        "documento": df.documento.iloc[0] if not df.empty else "",
        "documento_sha256": df.documento_sha256.iloc[0] if not df.empty else "",
        "paginas": len(df.attrs["cobertura"]), "filas": df.id_registro.nunique(), "celdas": len(df),
        "estados_monto": df.estado_monto.value_counts().to_dict(),
        "cobertura": pd.Series([p["estado"] for p in df.attrs["cobertura"]]).value_counts().to_dict(),
        "incidencias": incidencias.tipo_incidencia.value_counts().to_dict(),
        "importes_pdf_verificados": sum(p.get("importes_pdf", 0) for p in df.attrs["cobertura"]),
        "importes_recuperados_verificados": sum(p.get("importes_recuperados", 0) for p in df.attrs["cobertura"]),
        "extraccion_sin_errores": not incidencias.severidad.eq("ERROR").any(),
        "nota": "Los cuadros son vistas diferentes; no sumar todo el CSV. Los nulos del PDF no se imputan. Las discrepancias del documento se conservan.",
    }
    (destino / "reporte_validacion.json").write_text(json.dumps(reporte, ensure_ascii=False, indent=2, default=int) + "\n")
    return reporte


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="?", default="Presupuesto 2025.pdf")
    parser.add_argument("--salida", default="salida")
    args = parser.parse_args()
    df = procesar_presupuesto_completo(args.pdf)
    reporte = exportar_dataset(df, args.salida)
    print(json.dumps(reporte, ensure_ascii=False, indent=2, default=int))
    raise SystemExit(0 if reporte["extraccion_sin_errores"] else 2)
