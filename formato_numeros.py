"""Formato compartido por las interfaces y las exportaciones."""
from decimal import Decimal, ROUND_HALF_UP, localcontext

import pandas as pd


def numero_decimal(valor):
    """Leer números internos y montos argentinos sin pasar por float."""
    if valor is None or pd.isna(valor) or valor == "":
        return None
    if isinstance(valor, Decimal):
        return valor
    texto = str(valor).strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return Decimal(texto)


def formato_argentino(valor, decimales=2):
    numero = numero_decimal(valor)
    if numero is None:
        return ""
    with localcontext() as contexto:
        contexto.prec = max(28, len(numero.as_tuple().digits) + 2, numero.adjusted() + 4)
        numero = numero.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    return format(numero, f",.{decimales}f").translate(str.maketrans({",": ".", ".": ","}))


def formato_compacto(valor, decimales=1):
    """Importe abreviado para tarjetas: 1,2 mil millones / 35,4 millones / 12,3 mil."""
    numero = numero_decimal(valor)
    if numero is None:
        return ""
    absoluto = abs(numero)
    for limite, etiqueta in ((Decimal("1e12"), "billones"), (Decimal("1e9"), "mil millones"),
                             (Decimal("1e6"), "millones"), (Decimal("1e3"), "mil")):
        if absoluto >= limite:
            return f"{formato_argentino(numero / limite, decimales)} {etiqueta}"
    return formato_argentino(numero, 0)


def formato_entero(valor):
    numero = numero_decimal(valor)
    return "" if numero is None else format(int(numero), ",").replace(",", ".")


def columnas_importes(df):
    return [c for c in df.columns if c == "monto" or c.endswith("_pesos")
            or c == "monto_pesos_estimado" or c == "monto_millones_ar"]


def dataframe_exportacion(df):
    resultado = df.copy()
    for columna in columnas_importes(resultado):
        resultado[columna] = resultado[columna].map(formato_argentino)
    return resultado


def tabla_formateada(df):
    """Vista formateada sin modificar los datos usados en los cálculos.

    Evitar Styler: el dataset completo supera su límite de celdas renderizables.
    """
    resultado = dataframe_exportacion(df)
    for columna in resultado.columns:
        if columna.endswith("_centavos"):
            resultado[columna] = resultado[columna].map(formato_entero)
    return resultado


def exportar_csv(df, destino=None, **kwargs):
    # El delimitador sigue siendo coma. pandas entrecomilla los importes
    # que contienen coma decimal, de acuerdo con el formato CSV estándar (podriamos revisarlo en detalle)
    return dataframe_exportacion(df).to_csv(destino, index=False, **kwargs)
