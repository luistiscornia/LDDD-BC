from __future__ import annotations
"""Interfaz interactiva de la Billetera Ciudadana de Rosario.

Permite al vecino consultar de manera directa, visual y transparente:
1. Cuánto se presupuestó y cuánto se terminó ejecutando realmente por área (% de ejecución).
2. Qué porcentaje del presupuesto total municipal absorbió cada secretaría (% de participación).
3. Cuánto dinero representó por habitante rosarino.
4. Semáforos cívicos y notas normativas de reasignaciones presupuestarias (Ord. 10.748/2024).
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from pipeline.normalizer import NormalizadorPresupuesto
from pipeline.scraper import RosarioScraper
from pipeline.ml_engine import (
    entrenar_modelo_desvio_xgboost,
    crear_scatter_plot_cuadrantes,
    crear_grafico_feature_importance,
    detectar_anomalias_isolation_forest,
    preparar_dataset_modelado,
)
from formato_numeros import formato_argentino, formato_compacto, formato_entero

BASE = Path(__file__).resolve().parent


def cargar_datos_billetera(ejercicio="2025"):
    normalizador = NormalizadorPresupuesto(base_dir=BASE)
    archivo_csv = BASE / "salida" / f"billetera_ciudadana_jurisdicciones_{ejercicio}.csv"
    archivo_resumen = BASE / "salida" / f"billetera_ciudadana_resumen_{ejercicio}.json"
    archivo_objeto = BASE / "salida" / f"billetera_ciudadana_objeto_{ejercicio}.csv"

    if not archivo_csv.exists() or not archivo_resumen.exists():
        resumen = normalizador.generar_billetera_ciudadana_master(destino_dir=BASE / "salida", ejercicio=ejercicio)
    else:
        resumen = json.loads(archivo_resumen.read_text(encoding="utf-8"))

    df_jur = pd.read_csv(archivo_csv, encoding="utf-8-sig")
    df_obj = pd.read_csv(archivo_objeto, encoding="utf-8-sig") if archivo_objeto.exists() else pd.DataFrame()
    return df_jur, df_obj, resumen


def main():
    normalizador = NormalizadorPresupuesto(base_dir=BASE)
    ejercicios_disp = normalizador.ejercicios_disponibles()

    st.set_page_config(
        page_title="Billetera Cívica Rosario",
        page_icon="💳",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Estilos visuales inspirados en mockup.html
    st.markdown(
        """
        <style>
        [data-testid="stMetricValue"] { font-size: clamp(1.1rem, 1.7vw, 1.7rem) !important; line-height: 1.2 !important; }
        [data-testid="stMetricValue"] * { font-size: inherit !important; white-space: normal !important; overflow: visible !important; text-overflow: clip !important; }
        [data-testid="stMetricLabel"] p { font-size: 0.9rem !important; }
        .metric-value { word-break: normal; overflow-wrap: anywhere; }
        .metric-card {
            background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
            border-radius: 18px;
            padding: 22px;
            color: white;
            box-shadow: 0 10px 25px -5px rgba(2, 132, 199, 0.3);
            margin-bottom: 20px;
        }
        .metric-title { font-size: 1.1rem; font-weight: 700; opacity: 0.95; }
        .metric-value { font-size: 2.3rem; font-weight: 800; letter-spacing: -0.5px; margin: 8px 0; }
        .metric-sub { font-size: 0.95rem; opacity: 0.9; }
        .badge-green { background-color: #dcfce7; color: #166534; padding: 4px 10px; border-radius: 8px; font-weight: 700; font-size: 0.85rem; }
        .badge-blue { background-color: #e0f2fe; color: #075985; padding: 4px 10px; border-radius: 8px; font-weight: 700; font-size: 0.85rem; }
        .badge-amber { background-color: #fef3c7; color: #92400e; padding: 4px 10px; border-radius: 8px; font-weight: 700; font-size: 0.85rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Sidebar: Control de Ingesta, Período y Scraping
    with st.sidebar:
        st.header("📅 Período de Consulta")
        ejercicio_sel = st.selectbox("Seleccioná el Ejercicio Fiscal:", ejercicios_disp, index=0)
        st.caption(f"Período activo: **Ejercicio {ejercicio_sel}**")

        st.divider()
        st.header("⚙️ Motor de Ingesta")
        st.write("Sincronización automatizada desde los portales oficiales de la Municipalidad de Rosario.")
        if st.button("🔄 Ejecutar Scraping e Ingesta", type="primary", use_container_width=True):
            barra = st.progress(0, text="Iniciando scraping oficial...")
            try:
                scraper = RosarioScraper(directorio_base=BASE / "datos_crudos")
                manif = scraper.ejecutar_ingesta_completa(
                    progreso=lambda msg, act, tot: barra.progress(act / tot, text=f"{msg} ({act}/{tot})…")
                )
                normalizador = NormalizadorPresupuesto(base_dir=BASE)
                for a in normalizador.ejercicios_disponibles():
                    normalizador.generar_billetera_ciudadana_master(destino_dir=BASE / "salida", ejercicio=a)
                st.success("¡Datos actualizados y normalizados con éxito!")
            except Exception as exc:
                st.error(f"Error en el pipeline: {exc}")
            finally:
                barra.empty()

        st.divider()
        st.caption("Fuentes oficiales procesadas:")
        st.caption("• Proyecto Presupuesto (1.038 págs)")
        st.caption("• Informes de Ejecución Recursos y Gastos")
        st.caption("• Ordenanzas presupuestarias oficiales")

    # Encabezado Principal
    st.title(f"💳 Billetera Cívica: Presupuesto y Ejecución {ejercicio_sel}")
    st.write(
        f"Herramienta ciudadana de transparencia para auditar cómo se gastaron los recursos públicos en Rosario durante el ejercicio {ejercicio_sel}."
    )

    try:
        df_jur, df_obj, resumen = cargar_datos_billetera(ejercicio=ejercicio_sel)
    except Exception as exc:
        st.error(f"No se pudieron cargar los datos de la billetera: {exc}")
        return

    # Tarjetas Globales
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(
            label="Presupuesto Proyectado Total",
            value=f"$ {formato_compacto(resumen['presupuesto_total_pesos'], 1)}",
            help="Suma aprobada originalmente para la Administración Central en el presupuesto 2025.",
        )
    with c2:
        st.metric(
            label="Gasto Devengado Total",
            value=f"$ {formato_compacto(resumen['gasto_total_devengado_pesos'], 1)}",
            help="Total ejecutado y facturado por la Municipalidad durante 2025.",
        )
    with c3:
        st.metric(
            label="Tasa Global de Ejecución",
            value=f"{formato_argentino(resumen['porcentaje_ejecucion_global'], 1)} %",
            delta=f"{formato_argentino(resumen['porcentaje_ejecucion_global'] - 100, 1)} % vs proyecto",
        )
        # Acá me parece que lo mejor sería traer los datos de poblacion proyectada deesde https://datos.rosario.gob.ar/poblacion/proyecciones-de-poblacion
    with c4:
        st.metric(
            label="Gasto Anual por Vecino",
            value=f"$ {formato_argentino(resumen['gasto_total_por_habitante'], 0)}",
            help="Gasto total dividido entre los 1.030.000 habitantes estimados de Rosario.",
        )

    st.divider()

    # Selector y Tarjeta Ejecutiva por Área
    st.subheader("🔍 Consultá un Área de Gobierno")

    opciones = [
        f"{r['icono']} {r['area_gobierno']} (Cód. {r['codigo_jurisdiccion']:02d})"
        for _, r in df_jur.iterrows()
    ]
    seleccion = st.selectbox("Seleccioná la secretaría u organismo:", opciones, index=0)
    cod_seleccionado = int(seleccion.split("Cód. ")[-1].replace(")", ""))
    fila = df_jur[df_jur["codigo_jurisdiccion"] == cod_seleccionado].iloc[0]

    # Render de la tarjeta de la secretaría seleccionada
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-title">{fila['icono']} {fila['area_gobierno'].upper()} · {fila['nombre_oficial']}</div>
            <div class="metric-value">$ {formato_compacto(fila['gasto_devengado_pesos'])} <span style="font-size:1.1rem; font-weight:400;">ejecutados</span></div>
            <div class="metric-sub">
                De un presupuesto inicial proyectado de <b>$ {formato_compacto(fila['presupuesto_proyectado_pesos'])}</b>
            </div>
            <p style="margin-top:12px; font-size:0.9rem; opacity:0.92;">{fila['descripcion_area']}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Indicadores Clave del Área (Las dos respuestas exactas que pide el usuario)
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric(
            label="1. ¿Cuánto se ejecutó de lo asignado?",
            value=f"{formato_argentino(fila['porcentaje_ejecucion'], 1)} %",
            help="Porcentaje del presupuesto asignado a esta secretaría que se gastó efectivamente.",
        )
    with m2:
        st.metric(
            label="2. ¿Cuánto pesa sobre el gasto total?",
            value=f"{formato_argentino(fila['porcentaje_del_total_gastado'], 2)} %",
            help="Porcentaje que representa esta secretaría sobre el gasto total de toda la ciudad.",
        )
    with m3:
        st.metric(
            label="Gasto por cada rosarino",
            value=f"$ {formato_argentino(fila['gasto_por_habitante_pesos'], 0)}",
            help="Gasto de esta secretaría repartido entre los 1.030.000 habitantes.",
        )
    with m4:
        semaforo = fila["semaforo"]
        if semaforo == "NORMAL":
            st.markdown("**Estado:** <span class='badge-green'>🟢 NORMAL</span>", unsafe_allow_html=True)
        elif semaforo == "AMPLIACION_CREDITO":
            st.markdown("**Estado:** <span class='badge-blue'>ℹ️ AMPLIACIÓN DE CRÉDITO</span>", unsafe_allow_html=True)
        else:
            st.markdown("**Estado:** <span class='badge-amber'>🟡 SUBEJECUCIÓN</span>", unsafe_allow_html=True)
        st.caption(fila["explicacion_semaforo"])

    # Barra visual de progreso
    progreso_val = min(float(fila["porcentaje_ejecucion"]) / 100.0, 1.5)
    st.progress(min(progreso_val, 1.0), text=f"Ejecución: {formato_argentino(fila['porcentaje_ejecucion'], 1)}%")

    # Asi podría ser Módulo de Auditoría y Diagnóstico Técnico Detallado (Interactivo)
    abrir_por_defecto = fila["semaforo"] != "NORMAL"
    with st.expander(f"🔍 ¿Cómo se explica técnicamente este {formato_argentino(fila['porcentaje_ejecucion'], 1)}%? (Detalle de Auditoría)", expanded=abrir_por_defecto):
        if fila["semaforo"] == "AMPLIACION_CREDITO":
            st.info(
                "💡 **Aclaración Ciudadana:** Un porcentaje superior al 100% no significa que el organismo haya gastado dinero sin autorización ni que se haya desbordado ilegalmente. En la administración pública, los gastos se ejecutan contra el **Crédito Vigente** (el presupuesto inicial más las ampliaciones aprobadas durante el año por el Concejo Municipal)."
            )
        elif fila["semaforo"] == "SUBEJECUCION":
            st.warning(
                "⚠️ **Aclaración Ciudadana:** La subejecución ocurre cuando un área gasta menos de lo previsto, sea por economías de gestión, demoras en contrataciones o reasignación de sus partidas a áreas prioritarias."
            )

        if "diagnostico_tecnico" in fila and pd.notna(fila["diagnostico_tecnico"]):
            st.markdown(fila["diagnostico_tecnico"])
        else:
            st.write(fila["explicacion_semaforo"])

    # Nota Normativa (si aplica)
    nota_norm = fila.get("nota_normativa") or fila.get("nota_normativa_2025")
    if pd.notna(nota_norm) and str(nota_norm).strip():
        st.info(f"📜 **Modificación presupuestaria oficial:** {nota_norm}")

    st.divider()

    # Pestañas de análisis en profundidad
    t1, t2, t3, t4 = st.tabs([
        "📊 Comparativa de Todas las Áreas",
        "🔬 Laboratorio Analítico & IA (XGBoost)",
        "🛒 ¿En qué se gastó? (Por Objeto)",
        "📥 Descargar Datos",
    ])

    with t1:
        st.write("Ranking de las 21 áreas de la Municipalidad de Rosario ordenadas por volumen de fondos administrados:")
        df_mostrar = df_jur[
            [
                "codigo_jurisdiccion",
                "area_gobierno",
                "presupuesto_proyectado_pesos",
                "gasto_devengado_pesos",
                "porcentaje_ejecucion",
                "porcentaje_del_total_gastado",
                "gasto_por_habitante_pesos",
                "semaforo",
            ]
        ].copy()
        df_mostrar.columns = [
            "Cód.",
            "Área de Gobierno",
            "Presupuesto Proyectado ($)",
            "Gasto Devengado ($)",
            "% Ejecutado",
            "% del Total Municipal",
            "Gasto x Habitante ($)",
            "Estado",
        ]
        st.dataframe(
            df_mostrar.style.format({
                "Presupuesto Proyectado ($)": lambda v: f"$ {formato_argentino(v, 0)}",
                "Gasto Devengado ($)": lambda v: f"$ {formato_argentino(v, 0)}",
                "% Ejecutado": lambda v: f"{formato_argentino(v, 1)} %",
                "% del Total Municipal": lambda v: f"{formato_argentino(v, 2)} %",
                "Gasto x Habitante ($)": lambda v: f"$ {formato_argentino(v, 0)}",
            }),
            use_container_width=True,
            height=400,
        )

        st.subheader("🔎 Auditoría Rápida por Área")
        jur_auditoria = st.selectbox(
            "Seleccioná cualquier área para desplegar su informe de auditoría:",
            [
                f"{r['icono']} {r['area_gobierno']} (Cód. {r['codigo_jurisdiccion']:02d}) · {r['porcentaje_ejecucion']:.1f}% · [{r['semaforo']}]"
                for _, r in df_jur.iterrows()
            ],
            key="sb_auditoria_tab",
        )
        cod_tab = int(jur_auditoria.split("Cód. ")[-1].split(")")[0])
        fila_tab = df_jur[df_jur["codigo_jurisdiccion"] == cod_tab].iloc[0]

        st.markdown(f"#### {fila_tab['icono']} {fila_tab['area_gobierno']} ({fila_tab['nombre_oficial']})")
        if fila_tab["semaforo"] == "AMPLIACION_CREDITO":
            st.info(
                f"💡 **Ejecutó {fila_tab['porcentaje_ejecucion']:.1f}% s/ proyectado:** Operó con ampliaciones de Crédito Vigente aprobadas durante {ejercicio_sel}. No es exceso de gasto sin control."
            )
        elif fila_tab["semaforo"] == "SUBEJECUCION":
            st.warning(
                f"⚠️ **Ejecutó {fila_tab['porcentaje_ejecucion']:.1f}% s/ proyectado:** Registró subejecución o reasignaciones de crédito hacia otras dependencias."
            )
        else:
            st.success(f"🟢 **Ejecutó {fila_tab['porcentaje_ejecucion']:.1f}% s/ proyectado:** Ejecución alineada a las previsiones anuales.")

        st.markdown(fila_tab["diagnostico_tecnico"])

    with t2:
        st.subheader("🔬 Laboratorio de Ciencia de Datos & Políticas Públicas (Unidades IV y V)")
        st.write(
            "Análisis multivariado, evaluación de riesgos fiscales y modelos de Machine Learning aplicados sobre los datasets presupuestarios oficiales de Rosario."
        )

        # 1. Scatter Plot Multivariado de Cuadrantes
        st.markdown("### 1. Matriz de Cuadrantes de Desvío Fiscal (Volumen vs. Tasa de Ejecución)")
        st.caption(
            "Visualización multivariada que correlaciona el volumen de fondos asignados (Eje X) con la tasa de ejecución real (Eje Y), "
            "el tamaño del impacto por habitante (Burbuja) y la clasificación cívica (Color):"
        )
        chart_cuadrantes = crear_scatter_plot_cuadrantes(df_jur)
        st.altair_chart(chart_cuadrantes, use_container_width=True)

        col_q1, col_q2, col_q3, col_q4 = st.columns(4)
        with col_q1:
            st.info("**Cuadrante I: Alerta Máxima**\nAlto presupuesto y sobreejecución (>110%). Impacto financiero directo en la ciudad (ej. Salud Pública).")
        with col_q2:
            st.warning("**Cuadrante II: Desvío Operativo**\nBajo presupuesto pero alta sobreejecución (ej. Intendencia 309%). Desvío porcentual alto pero bajo impacto por habitante.")
        with col_q3:
            st.success("**Cuadrante III: Ejecución Equilibrada**\nÁreas con gasto alineado a la planificación anual (85% a 110%).")
        with col_q4:
            st.warning("**Cuadrante IV: Riesgo de Subejecución**\nÁreas con ejecución <85%. Fondos ociosos o trabas burocráticas/licitatorias (ej. Obras de Infraestructura).")

        st.divider()

        # 2. Modelo Predictivo con XGBoost (Unidad V)
        st.markdown("### 2. Modelo Predictivo de Riesgo Presupuestario con XGBoost")
        st.markdown(
            "Conforme a los lineamientos de la **Unidad V** del programa y la **Ordenanza 10.849/2025 de IA de Rosario**, "
            "se entrenó un modelo supervisado de Gradient Boosting (`XGBClassifier`) para clasificar el riesgo de desvío presupuestario "
            "y explicar sus factores determinantes (*Explainable AI - XAI*)."
        )

        res_xgb = entrenar_modelo_desvio_xgboost()

        c_m1, c_m2, c_m3 = st.columns(3)
        c_m1.metric("Precisión Global (Accuracy)", f"{res_xgb['accuracy']:.1%}", help="Evaluado con Stratified K-Fold Cross Validation")
        c_m2.metric("Puntaje F1 Ponderado", f"{res_xgb['f1_score']:.1%}", help="Equilibrio entre precisión y exhaustividad")
        c_m3.metric("Muestras Analizadas", f"{res_xgb['n_muestras']} registros", help="Histórico multianual consolidado")

        col_feat, col_interp = st.columns([3, 2])
        with col_feat:
            st.altair_chart(crear_grafico_feature_importance(res_xgb["importancias"]), use_container_width=True)
        with col_interp:
            st.markdown("#### 💡 Explicabilidad Algorítmica (XAI)")
            st.markdown(
                "El algoritmo XGBoost determinó los factores institucionales que más explican el desvío presupuestario en Rosario:\n"
                "1. **Volumen Presupuestario (25.4%):** Partidas masivas sufren mayor tensión de readecuación de precios.\n"
                "2. **Infraestructura y Obras (16.2%):** Mayor propensión a subejecución por demoras de licitación y redeterminaciones.\n"
                "3. **Salud y Desarrollo Social (15.7%):** Mayor probabilidad de ampliación por urgencias y paritarias sanitarias.\n"
                "4. **Variación Interanual (9.5%):** Volatilidad de partidas respecto al año anterior."
            )

        st.divider()

        # Detección de Anomalías No Supervisada Isolation Forest, me parece que esto es muy util y podriamos darle mayor protagonismo  
        st.markdown("### 3. Detección de Anomalías Fiscales con Machine Learning (Isolation Forest)")
        st.caption("Algoritmo no supervisado que aísla observaciones multidimensionales atípicas sin intervención humana:")
        df_model_prep = preparar_dataset_modelado()
        df_anomalias = detectar_anomalias_isolation_forest(df_model_prep)
        anomalias_detectadas = df_anomalias[df_anomalias["es_anomalia"] & (df_anomalias["ejercicio"] == int(ejercicio_sel))]

        if not anomalias_detectadas.empty:
            st.write(f"Se identificaron **{len(anomalias_detectadas)} comportamientos atípicos** en el ejercicio {ejercicio_sel}:")
            st.dataframe(
                anomalias_detectadas[["codigo_jurisdiccion", "area_gobierno", "presupuesto_pesos", "gasto_devengado_pesos", "pct_ejecucion", "label_target"]]
                .rename(columns={
                    "codigo_jurisdiccion": "Cód.",
                    "area_gobierno": "Área / Partida",
                    "presupuesto_pesos": "Presupuesto ($)",
                    "gasto_devengado_pesos": "Devengado ($)",
                    "pct_ejecucion": "% Ejecutado",
                    "label_target": "Clasificación de Riesgo",
                })
                .style.format({
                    "Presupuesto ($)": lambda v: f"$ {formato_argentino(v, 0)}",
                    "Devengado ($)": lambda v: f"$ {formato_argentino(v, 0)}",
                    "% Ejecutado": lambda v: f"{formato_argentino(v, 1)} %",
                }),
                use_container_width=True,
            )
        else:
            st.success(f"No se detectaron anomalías estadísticas extremas en el ejercicio {ejercicio_sel}.")

    with t3:
        if not df_obj.empty:
            st.write(f"Composición del gasto municipal por naturaleza económica (Incisos oficiales {ejercicio_sel}):")
            col_izq, col_der = st.columns([1, 1])
            with col_izq:
                st.dataframe(
                    df_obj.style.format({
                        "monto_pesos": lambda v: f"$ {formato_argentino(v, 0)}",
                        "porcentaje_sobre_total": lambda v: f"{formato_argentino(v, 2)} %",
                    }),
                    use_container_width=True,
                )
            with col_der:
                st.bar_chart(
                    data=df_obj.set_index("objeto_inciso")["porcentaje_sobre_total"],
                    use_container_width=True,
                )
        else:
            st.info("No se encontró el desglose por objeto en los datos procesados.")

    with t4:
        st.write("Descargá los datasets consolidados para análisis independiente o investigación:")
        c_csv, c_json = st.columns(2)
        with c_csv:
            st.download_button(
                "📥 Descargar Comparativa de Jurisdicciones (CSV)",
                df_jur.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"billetera_ciudadana_jurisdicciones_{ejercicio_sel}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with c_json:
            st.download_button(
                "📥 Descargar Resumen Ejecutivo (JSON)",
                json.dumps(resumen, indent=2, ensure_ascii=False).encode("utf-8"),
                file_name=f"billetera_ciudadana_resumen_{ejercicio_sel}.json",
                mime="application/json",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
