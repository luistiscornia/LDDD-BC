"""Controles de estructura y conciliaci¨®n exacta, sin cambiar los datos del PDF."""
from pathlib import Path
import argparse
from decimal import Decimal, InvalidOperation

import pandas as pd
from formato_numeros import numero_decimal

COLUMNAS_INCIDENCIAS = ["severidad", "tipo_incidencia", "pagina_pdf", "id_registro", "indice_columna", "monto_original", "diferencia_centavos", "texto_original"]


def auditar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    incidencias_extraccion = df.attrs.get("incidencias_extraccion", [])
    df = df.copy(deep=False)
    # pandas propaga attrs en cada grupo; la cobertura completa no debe copiarse
    # miles de veces durante la auditor¨ªa de filas.
    df.attrs = {}
    requeridas = {"id_registro", "id_tabla", "indice_columna", "monto_centavos", "estado_monto", "documento_sha256", "pagina_pdf", "dimension_columna"}
    faltantes = requeridas.difference(df.columns)
    if faltantes:
        raise ValueError("El CSV no usa el esquema actual; faltan columnas: " + ", ".join(sorted(faltantes)))
    incidencias = []
    def registrar(tipo, severidad="ERROR", **datos):
        incidencias.append(dict(severidad=severidad, tipo_incidencia=tipo, **datos))
    for i in incidencias_extraccion:
        datos = dict(i)
        registrar(datos.pop("tipo_incidencia"), **datos)
    for r in df.loc[df.duplicated(["id_registro", "indice_columna"], keep=False)].itertuples():
        registrar("CELDA_DUPLICADA", pagina_pdf=r.pagina_pdf, id_registro=r.id_registro, indice_columna=r.indice_columna)
    for r in df.loc[df.estado_monto.eq("ERROR")].itertuples():
        if not any(i.get("tipo_incidencia") == "MONTO_NO_INTERPRETABLE" and i.get("pagina_pdf") == r.pagina_pdf and i.get("indice_columna") == r.indice_columna and i.get("texto_original") == r.texto_original for i in incidencias):
            registrar("MONTO_NO_INTERPRETABLE", pagina_pdf=r.pagina_pdf, id_registro=r.id_registro, indice_columna=r.indice_columna, monto_original=r.monto_original, texto_original=r.texto_original)
    for r in df.itertuples():
        tiene = not pd.isna(r.monto_centavos)
        if (r.estado_monto == "OK") != tiene:
            registrar("ESTADO_MONTO_INCONSISTENTE", pagina_pdf=r.pagina_pdf, id_registro=r.id_registro, indice_columna=r.indice_columna)
        if r.estado_monto not in {"OK", "AUSENTE", "ERROR", "SIN_DATO"}:
            registrar("ESTADO_MONTO_DESCONOCIDO", pagina_pdf=r.pagina_pdf, id_registro=r.id_registro)
        if tiene:
            try:
                coherente = numero_decimal(r.monto) * 100 == int(r.monto_centavos)
            except (InvalidOperation, ValueError, TypeError):
                coherente = False
            if not coherente:
                registrar("MONTO_Y_CENTAVOS_DIFIEREN", pagina_pdf=r.pagina_pdf, id_registro=r.id_registro, indice_columna=r.indice_columna)
        if not str(r.encabezado_columna).strip():
            registrar("ENCABEZADO_AUSENTE", pagina_pdf=r.pagina_pdf, id_registro=r.id_registro)
    filas = {}
    celdas_por_clave = {}
    hijos = {}
    for r in df.itertuples():
        filas.setdefault(r.id_registro, []).append(r)
        celdas_por_clave[(r.id_registro, r.indice_columna)] = r
        if r.id_padre:
            hijos.setdefault((r.id_padre, r.indice_columna), []).append(r)
    for rid, celdas in filas.items():
        primera = celdas[0]
        esperadas = len(primera.encabezado_columnas.split(" | "))
        if {r.indice_columna for r in celdas} != set(range(1, esperadas + 1)):
            registrar("COLUMNAS_INCOMPLETAS", pagina_pdf=primera.pagina_pdf, id_registro=rid)
        total = [r for r in celdas if r.dimension_columna == "TOTAL"]
        componentes = [r for r in celdas if r.dimension_columna != "TOTAL"]
        # En los cuadros presupuestarios las columnas son una partici¨®n.
        # Los vac¨ªos no se imputan: solo se contrastan los aportes impresos.
        if len(total) == 1 and componentes and primera.tipo_balance != "REMUNERACIONES":
            if total[0].estado_monto == "OK" and not any(r.estado_monto in {"ERROR", "SIN_DATO"} for r in componentes):
                diferencia = int(total[0].monto_centavos) - sum(int(r.monto_centavos) for r in componentes if r.estado_monto == "OK")
                if diferencia:
                    registrar("TOTAL_DISTINTO_COMPONENTES", "REVISION", pagina_pdf=primera.pagina_pdf, id_registro=rid, diferencia_centavos=diferencia, texto_original=primera.texto_original)
    for clave, componentes in hijos.items():
        padre = celdas_por_clave.get(clave)
        if padre is None:
            registrar("PADRE_INEXISTENTE", pagina_pdf=componentes[0].pagina_pdf, id_registro=componentes[0].id_registro)
            continue
        if any(r.id_tabla != padre.id_tabla for r in componentes):
            registrar("PADRE_DE_OTRA_TABLA", pagina_pdf=padre.pagina_pdf, id_registro=padre.id_registro)
        if padre.estado_monto == "OK" and any(r.estado_monto == "OK" for r in componentes) and not any(r.estado_monto in {"ERROR", "SIN_DATO"} for r in componentes):
            diferencia = int(padre.monto_centavos) - sum(int(r.monto_centavos) for r in componentes if r.estado_monto == "OK")
            if diferencia:
                registrar("SUBTOTAL_DISTINTO_HIJOS", "REVISION", pagina_pdf=padre.pagina_pdf, id_registro=padre.id_registro, indice_columna=padre.indice_columna, diferencia_centavos=diferencia, texto_original=padre.texto_original)
    return pd.DataFrame(incidencias, columns=COLUMNAS_INCIDENCIAS).drop_duplicates().reset_index(drop=True)


def auditar_dataset(csv_path="salida/presupuesto_consolidado_total.csv", incidencias_path=None):
    df = pd.read_csv(csv_path, dtype={"id_registro": str, "id_tabla": str, "monto_centavos": "Int64", "monto": str, "hoja": str, "clasificador": str, "clasificador_original": str}, keep_default_na=False, na_values={"monto_centavos": [""]})
    incidencias = auditar_dataframe(df)
    destino = Path(incidencias_path) if incidencias_path else Path(csv_path).with_name("incidencias_auditoria.csv")
    incidencias.to_csv(destino, index=False)
    print(f"Filas contables: {df.id_registro.nunique()}; celdas: {len(df)}")
    print(f"Errores: {incidencias.severidad.eq('ERROR').sum()}; diferencias a revisar: {incidencias.severidad.eq('REVISION').sum()}")
    return incidencias


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", default="salida/presupuesto_consolidado_total.csv")
    args = parser.parse_args()
    auditar_dataset(args.csv)
