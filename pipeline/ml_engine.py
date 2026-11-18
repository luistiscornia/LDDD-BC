from __future__ import annotations
"""Módulo de Analítica Avanzada, Machine Learning y Detección de Anomalías.

Implementa los requisitos de las Unidades IV y V de Laboratorio de Datos:
1. Scatter Plot Multivariado de Cuadrantes de Desvío Fiscal (Altair).
2. Modelo Predictivo con XGBoost para clasificación de riesgo presupuestario.
3. Feature Importance (Explicabilidad algorítmica XAI).
4. Detección no supervisada de anomalías fiscales con Isolation Forest.
"""
from pathlib import Path
from typing import Any, Optional

import altair as alt
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
import xgboost as xgb

BASE = Path(__file__).resolve().parent.parent


def preparar_dataset_modelado(base_dir: Path = BASE) -> pd.DataFrame:
    """Construye un dataset tabular para Machine Learning integrando historia y partidas."""
    filas = []

    # Cargar datos históricos de jurisdicciones (2022 a 2025)
    for anio in ["2022", "2023", "2024", "2025"]:
        archivo = base_dir / "salida" / f"billetera_ciudadana_jurisdicciones_{anio}.csv"
        if not archivo.exists():
            continue
        df_j = pd.read_csv(archivo)

        # Mapeo de grupos funcionales
        def asignar_grupo(cod):
            cod_int = int(cod)
            if cod_int in (8, 9, 19):
                return "Social_Salud_Genero"
            elif cod_int in (4, 6, 7, 16):
                return "Infraestructura_Servicios"
            elif cod_int in (1, 2, 3, 5, 12, 13, 15, 20, 21):
                return "Gobierno_Control_Hacienda"
            else:
                return "Deuda_Obligaciones_Tesoro"

        for _, r in df_j.iterrows():
            pres = float(r["presupuesto_proyectado_pesos"])
            dev = float(r["gasto_devengado_pesos"])
            pct = float(r["porcentaje_ejecucion"]) if pres > 0 else 100.0

            # Target: 0 = Subejecución (<85%), 1 = Normal (85-110%), 2 = Ampliación (>110%)
            if pct < 85.0:
                target = 0
                label_target = "SUBEJECUCION"
            elif pct > 110.0:
                target = 2
                label_target = "AMPLIACION_CREDITO"
            else:
                target = 1
                label_target = "NORMAL"

            filas.append({
                "ejercicio": int(anio),
                "codigo_jurisdiccion": int(r["codigo_jurisdiccion"]),
                "area_gobierno": r["area_gobierno"],
                "presupuesto_pesos": pres,
                "gasto_devengado_pesos": dev,
                "pct_ejecucion": pct,
                "gasto_por_habitante": float(r["gasto_por_habitante_pesos"]),
                "peso_total_pct": float(r["porcentaje_del_total_gastado"]),
                "var_interanual_pct": float(r["variacion_interanual_pct"]) if pd.notna(r.get("variacion_interanual_pct")) else 0.0,
                "grupo_funcional": asignar_grupo(r["codigo_jurisdiccion"]),
                "target_clase": target,
                "label_target": label_target,
            })

    # Cargar enriquecimiento de partidas del clasificador por objeto y fuentes
    # Esto expande las observaciones con patrones microeconómicos oficiales
    archivo_obj = base_dir / "salida" / "billetera_ciudadana_objeto_2025.csv"
    if archivo_obj.exists():
        df_o = pd.read_csv(archivo_obj)
        total_obj = df_o["monto_pesos"].sum()
        for idx, ro in df_o.iterrows():
            monto_obj = float(ro["monto_pesos"])
            inciso = str(ro["objeto_inciso"])
            # Características estructurales
            es_personal = 1 if "personal" in inciso.lower() else 0
            es_obras = 1 if any(k in inciso.lower() for k in ["bienes de uso", "construccion", "capital"]) else 0
            es_deuda = 1 if "deuda" in inciso.lower() else 0

            # Sintetizar comportamiento sectorial para el entrenamiento
            # Obras tienen tendencia a subejecución por licitaciones; Personal a ampliación por paritarias
            if es_personal:
                t_obj = 2
                lbl_obj = "AMPLIACION_CREDITO"
            elif es_obras:
                t_obj = 0
                lbl_obj = "SUBEJECUCION"
            else:
                t_obj = 1
                lbl_obj = "NORMAL"

            filas.append({
                "ejercicio": 2025,
                "codigo_jurisdiccion": 99,
                "area_gobierno": f"Inciso: {inciso[:25]}",
                "presupuesto_pesos": monto_obj,
                "gasto_devengado_pesos": monto_obj,
                "pct_ejecucion": 100.0,
                "gasto_por_habitante": monto_obj / 1_029_999,
                "peso_total_pct": (monto_obj / total_obj * 100) if total_obj else 0.0,
                "var_interanual_pct": 50.0,
                "grupo_funcional": "Objeto_Economico",
                "target_clase": t_obj,
                "label_target": lbl_obj,
            })

    df = pd.DataFrame(filas)
    return df


def entrenar_modelo_desvio_xgboost(df: Optional[pd.DataFrame] = None) -> dict[str, Any]:
    """Entrena un modelo predictivo XGBoost para clasificar el riesgo de desvío presupuestario."""
    if df is None or df.empty:
        df = preparar_dataset_modelado()

    # Feature Engineering
    df_modelo = df.copy()
    df_modelo["log_presupuesto"] = np.log1p(df_modelo["presupuesto_pesos"])
    df_modelo["log_gasto_habitante"] = np.log1p(df_modelo["gasto_por_habitante"])

    # One-Hot Encoding de grupos funcionales
    grupos_dummies = pd.get_dummies(df_modelo["grupo_funcional"], prefix="grupo", drop_first=True)

    features_cols = ["log_presupuesto", "log_gasto_habitante", "peso_total_pct", "var_interanual_pct"]
    X = pd.concat([df_modelo[features_cols], grupos_dummies], axis=1)
    y = df_modelo["target_clase"]

    # Modelo XGBoost Classifier
    clf = xgb.XGBClassifier(
        n_estimators=45,
        max_depth=3,
        learning_rate=0.1,
        random_state=42,
        eval_metric="mlogloss",
    )

    # Validación cruzada estratificada (Stratified K-Fold)
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
    y_pred = cross_val_predict(clf, X, y, cv=cv)

    acc = accuracy_score(y, y_pred)
    f1 = f1_score(y, y_pred, average="weighted")

    # Ajuste final sobre todo el dataset para interpretabilidad
    clf.fit(X, y)

    # Feature Importance (Ganancia / Gini Importance)
    importancias = clf.feature_importances_
    df_importancia = pd.DataFrame({
        "Característica": X.columns,
        "Importancia": importancias,
    }).sort_values(by="Importancia", ascending=False)

    # Nombres amigables para las variables
    nombres_legibles = {
        "log_presupuesto": "Volumen Presupuestario (Escala $)",
        "peso_total_pct": "Participación s/ Presupuesto Total",
        "log_gasto_habitante": "Gasto Anual por Rosarino",
        "var_interanual_pct": "Tasa de Variación Interanual",
        "grupo_Infraestructura_Servicios": "Área de Infraestructura y Obras",
        "grupo_Social_Salud_Genero": "Área de Salud y Asistencia Social",
        "grupo_Deuda_Obligaciones_Tesoro": "Servicio de Deuda y Tesoro",
        "grupo_Objeto_Economico": "Naturaleza Económica (Incisos)",
    }
    df_importancia["Característica_Legible"] = df_importancia["Característica"].map(
        lambda c: nombres_legibles.get(c, c.replace("grupo_", "Grupo "))
    )

    return {
        "modelo": clf,
        "features": list(X.columns),
        "accuracy": acc,
        "f1_score": f1,
        "importancias": df_importancia,
        "clases": ["Subejecución (<85%)", "Normal (85-110%)", "Ampliación de Crédito (>110%)"],
        "n_muestras": len(df_modelo),
    }


def detectar_anomalias_isolation_forest(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica Isolation Forest no supervisado combinando escala logarítmica y desvíos porcentuales extremos."""
    df_feat = df.copy()
    df_feat["log_presupuesto"] = np.log1p(df_feat["presupuesto_pesos"].fillna(0))
    df_feat["log_devengado"] = np.log1p(df_feat["gasto_devengado_pesos"].fillna(0))
    df_feat["desvio_pct_abs"] = (df_feat["pct_ejecucion"].fillna(100.0) - 100.0).abs()

    features = ["log_presupuesto", "log_devengado", "pct_ejecucion", "desvio_pct_abs"]
    X = df_feat[features].fillna(0)

    iso = IsolationForest(contamination=0.18, random_state=42)
    df = df.copy()
    df["es_anomalia"] = iso.fit_predict(X) == -1
    df["score_anomalia"] = iso.decision_function(X)
    return df


def crear_scatter_plot_cuadrantes(df_jur: pd.DataFrame) -> alt.Chart:
    """Crea el Scatter Plot Multivariado de Cuadrantes de Desvío Fiscal interactivo con Altair."""
    datos = df_jur.copy()
    datos["presupuesto_billones"] = datos["presupuesto_proyectado_pesos"] / 1e9
    datos["gasto_billones"] = datos["gasto_devengado_pesos"] / 1e9

    # Mapeo de colores del semáforo
    color_scale = alt.Scale(
        domain=["NORMAL", "AMPLIACION_CREDITO", "SUBEJECUCION"],
        range=["#16a34a", "#0284c7", "#d97706"],
    )

    puntos = (
        alt.Chart(datos)
        .mark_circle(opacity=0.85, stroke="#1e293b", strokeWidth=1.2)
        .encode(
            x=alt.X(
                "presupuesto_billones:Q",
                title="Presupuesto Proyectado (en Miles de Millones de Pesos - B$)",
                scale=alt.Scale(zero=False),
            ),
            y=alt.Y(
                "porcentaje_ejecucion:Q",
                title="Porcentaje de Ejecución Real (% Devengado s/ Proyectado)",
                scale=alt.Scale(domain=[0, 320]),
            ),
            size=alt.Size(
                "gasto_por_habitante_pesos:Q",
                title="Gasto x Rosarino ($)",
                scale=alt.Scale(range=[80, 1100]),
            ),
            color=alt.Color("semaforo:N", title="Semáforo Cívico", scale=color_scale),
            tooltip=[
                alt.Tooltip("area_gobierno:N", title="Área de Gobierno"),
                alt.Tooltip("codigo_jurisdiccion:N", title="Cód."),
                alt.Tooltip("presupuesto_billones:Q", title="Presupuesto ($ B)", format=",.2f"),
                alt.Tooltip("gasto_billones:Q", title="Devengado ($ B)", format=",.2f"),
                alt.Tooltip("porcentaje_ejecucion:Q", title="% Ejecutado", format=",.1f"),
                alt.Tooltip("gasto_por_habitante_pesos:Q", title="Gasto x Rosarino ($)", format=",.0f"),
                alt.Tooltip("semaforo:N", title="Diagnóstico"),
            ],
        )
    )

    # Línea horizontal meta en el 100% de ejecución
    linea_meta = (
        alt.Chart(pd.DataFrame({"y": [100.0]}))
        .mark_rule(color="#94a3b8", strokeDash=[6, 4], strokeWidth=2)
        .encode(y="y:Q")
    )

    # Anotación textual en la línea meta
    texto_meta = (
        alt.Chart(pd.DataFrame({"x": [datos["presupuesto_billones"].max() * 0.85], "y": [108.0], "texto": ["Meta Presupuestaria (100%)"]}))
        .mark_text(color="#64748b", fontSize=12, fontWeight="bold")
        .encode(x="x:Q", y="y:Q", text="texto:N")
    )

    anio_str = f" (Ejercicio {datos['ejercicio'].iloc[0]})" if "ejercicio" in datos.columns and not datos.empty else ""
    chart = (
        (linea_meta + puntos + texto_meta)
        .properties(
            width="container",
            height=460,
            title=f"Matriz de Cuadrantes de Desvío Fiscal{anio_str}: Volumen vs. Tasa de Ejecución",
        )
        .interactive()
    )

    return chart


def crear_grafico_feature_importance(df_importancia: pd.DataFrame) -> alt.Chart:
    """Crea el gráfico de barras interactivo de Feature Importance de XGBoost."""
    chart = (
        alt.Chart(df_importancia.head(7))
        .mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6, color="#4f46e5")
        .encode(
            x=alt.X("Importancia:Q", title="Importancia Relativa (Ganancia en XGBoost)", axis=alt.Axis(format=".1%")),
            y=alt.Y("Característica_Legible:N", title="Variable Institucional / Económica", sort="-x"),
            tooltip=[
                alt.Tooltip("Característica_Legible:N", title="Variable"),
                alt.Tooltip("Importancia:Q", title="Peso Relativo", format=".2%"),
            ],
        )
        .properties(width="container", height=280, title="Factores que Explican el Riesgo de Desvío (XGBoost Feature Importance)")
    )
    return chart
