import streamlit as st
import psycopg2
import psycopg2.extras
import pandas as pd
from passlib.context import CryptContext

from login_streamlit import login_ecom
from ecom_client import fetch_ml_listings_fast, fetch_products_fast, build_outputs

st.set_page_config(
    page_title="RD Simulador - Mercado Libre",
    page_icon="📊",
    layout="wide"
)

DATABASE_URL = st.secrets["DATABASE_URL"]

pwd_context  = CryptContext(
    schemes=["bcrypt"],
    default="bcrypt",
    bcrypt__rounds=12,
    truncate_error=True,
)

# ─────────────────────── CONEXIÓN A POSTGRES ────────────────────────────────
def get_conn():
    return psycopg2.connect(DATABASE_URL)


def fetch_all(query, params=None):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_one(query, params=None):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def execute(query, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
        conn.commit()
    finally:
        conn.close()


def login(username, password):
    user = fetch_one(
        "SELECT * FROM rd_usuarios WHERE username = %s AND is_active = true",
        (username,)
    )
    if not user:
        return None
    if not pwd_context.verify(password, user["password_hash"]):
        return None
    return user


CAMPAIGN_CUOTAS = {
    "Sin cuotas": 0.0000,
    "3 cuotas":   0.0840,
    "6 cuotas":   0.1230,
    "9 cuotas":   0.1570,
    "12 cuotas":  0.1920,
}

# ───────────────── CLASIFICACIÓN DE RANGOS COSTO/ENVÍO ──────────────────────
def clasificar_rango_envio(precio):
    if precio <= 32999:
        return "Hasta $32.999"
    elif precio <= 49999:
        return "De $ 33.000 a $ 49.999"
    else:
        return "Más de $ 50.000"


def clasificar_rango_und(precio):
    if precio <= 15999:
        return "Hasta $15.999"
    elif precio <= 23999:
        return "De $16.000 a $23.999"
    elif precio <= 32999:
        return "De $24.000 a $32.999"
    else:
        return "No Aplica"


def lookup_costo_und(rango_peso, rango_und):
    row = fetch_one("""
        SELECT "Costo_por_unidad_vendida"
        FROM rd_tabla_costos
        WHERE rango_peso_facturable = %s
          AND rango_valor_costo_und_vendida = %s
        LIMIT 1
    """, (rango_peso, rango_und))
    if not row:
        return 0.0
    return float(row.get("Costo_por_unidad_vendida") or 0)


def lookup_costo_envio(rango_peso, rango_envio):
    row = fetch_one("""
        SELECT costo_envio
        FROM rd_tabla_costos
        WHERE rango_peso_facturable = %s
          AND rango_valor_costo_envio = %s
        LIMIT 1
    """, (rango_peso, rango_envio))
    if not row:
        return 0.0
    return float(row.get("costo_envio") or 0)


# ───────────────────────── LÓGICA DE RENTABILIDAD ───────────────────────────
def calcular_rentabilidad(producto, precio_sim, pct_cuotas, incluir_envio: bool):
    costo_fijo   = float(producto.get("costo_fijo_ecom") or 0)  # ya es el final (nuevo si hay)
    pct_venta    = float(producto.get("pct_costo_venta") or 0)
    iva_tasa     = float(producto.get("iva_venta") or 0)
    rango_peso   = producto.get("rango_peso_facturable")

    rango_envio = clasificar_rango_envio(precio_sim)
    rango_und   = clasificar_rango_und(precio_sim)

    if incluir_envio:
        costo_envio_sim = lookup_costo_envio(rango_peso, rango_envio)
    else:
        costo_envio_sim = 0.0

    if rango_und != "No Aplica":
        costo_und_sim = lookup_costo_und(rango_peso, rango_und)
    else:
        costo_und_sim = 0.0

    factor_iva = 1.0 + (iva_tasa or 0.0)
    if factor_iva <= 0:
        base_imponible = precio_sim
        valor_iva_sim = 0.0
    else:
        base_imponible = precio_sim / factor_iva
        valor_iva_sim = precio_sim - base_imponible

    costo_cuotas_sim = pct_cuotas * precio_sim
    costo_venta_var  = pct_venta  * precio_sim
    costo_venta_sim  = costo_cuotas_sim + costo_venta_var + costo_und_sim

    rentabilidad_sim = (
        precio_sim
        - costo_fijo
        - valor_iva_sim
        - (costo_envio_sim / 1.21)
        - (costo_venta_sim / 1.21)
    )
    pct_rent_sim = (rentabilidad_sim / costo_fijo * 100) if costo_fijo else 0

    return {
        "precio_venta_final_sim":  round(precio_sim, 2),
        "costo_fijo_sim":          round(costo_fijo, 2),
        "valor_iva_sim":           round(valor_iva_sim, 2),
        "costo_envio_sim":         round(costo_envio_sim, 2),
        "pct_costo_venta_sim":     round(pct_venta * 100, 2),
        "pct_costo_cuotas_sim":    round(pct_cuotas * 100, 2),
        "costo_venta_sim":         round(costo_venta_sim, 2),
        "costo_und_vendida_sim":   round(costo_und_sim, 2),
        "rentabilidad_sim":        round(rentabilidad_sim, 2),
        "pct_rentabilidad_sim":    round(pct_rent_sim, 2),
        "rango_envio_sim":         rango_envio,
        "rango_und_sim":           rango_und,
    }


def simular_escenario_2(producto, pct_obj, pct_cuotas, incluir_envio: bool):
    costo_fijo = float(producto.get("costo_fijo_ecom") or 0)
    rent_obj   = (pct_obj / 100) * costo_fijo

    def f(p):
        r = calcular_rentabilidad(producto, p, pct_cuotas, incluir_envio)
        return r["rentabilidad_sim"] - rent_obj

    p_min = costo_fijo
    p_max = float(producto.get("precio_venta_final") or 0) * 3
    if p_max <= p_min:
        p_max = p_min * 5
    p_mid = p_max

    for _ in range(200):
        p_mid = (p_min + p_max) / 2
        valor = f(p_mid)
        if abs(valor) < 0.5:
            break
        if valor > 0:
            p_max = p_mid
        else:
            p_min = p_mid

    return round(p_mid, 2)


def fmt(valor):
    try:
        return f"$ {float(valor):,.0f}".replace(",", ".")
    except:
        return "-"


def mostrar_comparativo(producto, sim, escenario="1", nombre_campania=""):
    pct_actual = float(producto.get("pct_rentabilidad") or 0) * 100

    st.markdown(
        f"#### {producto.get('ml_id', '')}  \n"
        f"{producto.get('titulo_ecom', '')}"
    )

    col_actual, col_sim = st.columns(2)

    card_style = "border:1px solid #444;border-radius:6px;padding:10px;margin-bottom:10px;background-color:#111;"
    value_green_style = "color:#00c853;font-weight:bold;font-size:1.1rem;"

    # ACTUAL
    with col_actual:
        st.markdown("**ACTUAL**")
        ca1, ca2, ca3 = st.columns(3)
        with ca1:
            st.markdown(f"""
            <div style="{card_style}">
              <div>Precio</div>
              <div style="font-weight:bold;font-size:1.2rem;">{fmt(producto.get('precio_venta_final'))}</div>
            </div>
            """, unsafe_allow_html=True)
        with ca2:
            st.markdown(f"""
            <div style="{card_style}">
              <div>Rentabilidad</div>
              <div style="{value_green_style}">{fmt(producto.get('rentabilidad'))}</div>
            </div>
            """, unsafe_allow_html=True)
        with ca3:
            st.markdown(f"""
            <div style="{card_style}">
              <div>% Rentabilidad</div>
              <div style="{value_green_style}">{pct_actual:.2f}%</div>
            </div>
            """, unsafe_allow_html=True)

        html_actual = f"""
        <div style="{card_style}">
          <table style="width:100%;font-size:0.9rem;">
            <tr>
              <td>Costo fijo (final):</td><td>{fmt(producto.get('costo_fijo_ecom'))}</td>
              <td>Costo venta:</td><td>{fmt(producto.get('costo_venta'))}</td>
            </tr>
            <tr>
              <td>Costo fijo antiguo:</td><td>{fmt(producto.get('costo_fijo_ecom_antiguo'))}</td>
              <td>Costo fijo nuevo:</td><td>{fmt(producto.get('costo_fijo_ecom_nuevo'))}</td>
            </tr>
            <tr>
              <td>Costo envio:</td><td>{fmt(producto.get('costo_envio'))}</td>
              <td>dto_meli:</td><td>{fmt(producto.get('dto_meli'))}</td>
            </tr>
            <tr>
              <td>% Costo cuotas:</td><td>{float(producto.get('pct_costo_cuotas') or 0)*100:.2f}%</td>
              <td>Devolucion meli:</td><td>{fmt(producto.get('devolucion_dto_meli'))}</td>
            </tr>
            <tr>
              <td>Costo und vendida:</td><td>{fmt(producto.get('costo_und_vendida'))}</td>
              <td>Campaña cuotas:</td><td>{nombre_campania or 'Sin cuotas'}</td>
            </tr>
            <tr>
              <td>IVA:</td><td>{fmt(producto.get('valor_iva'))}</td>
              <td>% Costo venta:</td><td>{float(producto.get('pct_costo_venta') or 0)*100:.2f}%</td>
            </tr>
          </table>
        </div>
        """
        st.markdown(html_actual, unsafe_allow_html=True)

    # SIMULADO
    with col_sim:
        st.markdown("**SIMULADO**")
        cs1, cs2, cs3 = st.columns(3)
        with cs1:
            st.markdown(f"""
            <div style="{card_style}">
              <div>Precio</div>
              <div style="font-weight:bold;font-size:1.2rem;">{fmt(sim['precio_venta_final_sim'])}</div>
            </div>
            """, unsafe_allow_html=True)
        with cs2:
            st.markdown(f"""
            <div style="{card_style}">
              <div>Rentabilidad</div>
              <div style="{value_green_style}">{fmt(sim['rentabilidad_sim'])}</div>
            </div>
            """, unsafe_allow_html=True)
        with cs3:
            st.markdown(f"""
            <div style="{card_style}">
              <div>% Rentabilidad</div>
              <div style="{value_green_style}">{sim['pct_rentabilidad_sim']:.2f}%</div>
            </div>
            """, unsafe_allow_html=True)

        html_sim = f"""
        <div style="{card_style}">
          <table style="width:100%;font-size:0.9rem;">
            <tr>
              <td>Costo fijo:</td><td>{fmt(sim['costo_fijo_sim'])}</td>
              <td>Costo venta:</td><td>{fmt(sim['costo_venta_sim'])}</td>
            </tr>
            <tr>
              <td>Costo envio:</td><td>{fmt(sim['costo_envio_sim'])}</td>
              <td>dto_meli:</td><td>$ 0</td>
            </tr>
            <tr>
              <td>% Costo cuotas:</td><td>{sim['pct_costo_cuotas_sim']:.2f}%</td>
              <td>Devolucion meli:</td><td>$ 0</td>
            </tr>
            <tr>
              <td>Costo und vendida:</td><td>{fmt(sim['costo_und_vendida_sim'])}</td>
              <td>Campaña cuotas:</td><td>{nombre_campania or 'Sin cuotas'}</td>
            </tr>
            <tr>
              <td>IVA:</td><td>{fmt(sim['valor_iva_sim'])}</td>
              <td>% Costo venta:</td><td>{sim['pct_costo_venta_sim']:.2f}%</td>
            </tr>
          </table>
        </div>
        """
        st.markdown(html_sim, unsafe_allow_html=True)

    diff_rent = sim["rentabilidad_sim"] - float(producto.get("rentabilidad") or 0)
    diff_pct  = sim["pct_rentabilidad_sim"] - pct_actual

    col_v1, col_v2 = st.columns(2)
    with col_v1:
        if diff_rent >= 0:
            st.success(f"💲 Variacion Rentabilidad: +{fmt(diff_rent)}")
        else:
            st.error(f"💲 Variacion Rentabilidad: {fmt(diff_rent)}")
    with col_v2:
        if diff_pct >= 0:
            st.success(f"📈 Variacion % Rentabilidad: +{diff_pct:.2f}%")
        else:
            st.error(f"📉 Variacion % Rentabilidad: {diff_pct:.2f}%")


# ───────────────────────── SESSION / ESTADO ──────────────────────────────────
for key, default in [
    ("df_base", pd.DataFrame()),
    ("df_filtrado", pd.DataFrame()),
    ("seleccionados", []),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def apply_filtros(df: pd.DataFrame, f_ml_id, f_titulo, f_sku, f_ml_sinc, f_estado, f_tipo, f_logistica, f_envio) -> pd.DataFrame:
    if df.empty:
        return df
    vista = df.copy()
    if f_ml_id.strip():
        vista = vista[vista["ml_id"].astype(str).str.contains(f_ml_id.strip(), case=False, na=False)]
    if f_titulo.strip():
        vista = vista[vista["titulo_ecom"].astype(str).str.contains(f_titulo.strip(), case=False, na=False)]
    if f_sku.strip():
        vista = vista[vista["sku_asociados"].astype(str).str.contains(f_sku.strip(), case=False, na=False)]
    if f_ml_sinc.strip() and "ml_id_sincronizados" in vista.columns:
        vista = vista[vista["ml_id_sincronizados"].astype(str).str.contains(f_ml_sinc.strip(), case=False, na=False)]
    if f_estado != "Todos":
        vista = vista[vista["estado_meli"] == f_estado]
    if f_tipo != "Todos":
        vista = vista[vista["tipo_publicacion"] == f_tipo]
    if f_logistica != "Todas":
        vista = vista[vista["logistica"] == f_logistica]
    if f_envio == "Si":
        vista = vista[vista["envio_gratis"] == True]
    elif f_envio == "No":
        vista = vista[vista["envio_gratis"] == False]
    return vista


# ───────────────────────── LOGIN CON ECOM ────────────────────────────────────
if not st.session_state.get("authenticated"):
    login_ecom()
    st.stop()

st.markdown("### RD Simulador | Usuario autenticado via EcomExperts")
st.markdown("---")

# OPCIONES LISTAS (para filtros)
opciones = fetch_all("""
    SELECT
        array_agg(DISTINCT estado_meli)       AS estados,
        array_agg(DISTINCT logistica)         AS logisticas,
        array_agg(DISTINCT tipo_publicacion)  AS tipos
    FROM rd_tabla_rentas
    WHERE estado_meli IS NOT NULL
""")
estados    = sorted([x for x in (opciones[0]["estados"]    or []) if x])
logisticas = sorted([x for x in (opciones[0]["logisticas"] or []) if x])
tipos      = sorted([x for x in (opciones[0]["tipos"]      or []) if x])

# BOTÓN CONSULTAR (ACTUALIZA TODO)
col_consultar, _ = st.columns([1, 3])
with col_consultar:
    btn_consultar = st.button("Consultar datos", type="primary", use_container_width=True)

if btn_consultar:
    session = st.session_state.get("ecom_session")
    if session is None:
        st.error("La sesión de EcomExperts no es válida. Vuelve a iniciar sesión.")
        st.stop()

    progress_bar = st.progress(0)
    status_text  = st.empty()
    EST_MAX_PAGES = 200  # máximo estimado para normalizar 0–100 %

    def status_callback(msg: str):
        if "mlListings - página" in msg:
            try:
                num = int(msg.split("página")[1].split("...")[0].strip())
                pct = min(int(num / EST_MAX_PAGES * 100), 100)
                progress_bar.progress(pct)
                status_text.text(f"Cargando datos desde EcomExperts... página {num} ({pct}%)")
            except Exception:
                pass

    with st.spinner("Consultando información completa desde EcomExperts y Postgres..."):
        df_listings = fetch_ml_listings_fast(session, status_callback)
        df_products = fetch_products_fast(session, None)
        detalle_costos, final_costos = build_outputs(df_listings, df_products, None)

        resultados_pg = fetch_all("SELECT * FROM rd_tabla_rentas", ())

        if resultados_pg:
            df_pg = pd.DataFrame(resultados_pg)

            # Guardar costo fijo original de Postgres
            df_pg["costo_fijo_ecom_antiguo"] = df_pg["costo_fijo_ecom"]

            # Costo nuevo desde Ecom (costo_total_mla)
            df_costos = final_costos[["mla", "costo_total_mla"]].rename(
                columns={"mla": "ml_id", "costo_total_mla": "costo_fijo_ecom_nuevo"}
            )

            df_pg["ml_id"] = df_pg["ml_id"].astype(str)
            df_costos["ml_id"] = df_costos["ml_id"].astype(str)

            df_merged = df_pg.merge(df_costos, on="ml_id", how="left")

            # Costo final que usará la app: nuevo si existe, si no el antiguo
            df_merged["costo_fijo_ecom"] = df_merged["costo_fijo_ecom_nuevo"].fillna(
                df_merged["costo_fijo_ecom_antiguo"]
            )

            st.session_state.df_base = df_merged.copy()
            st.session_state.df_filtrado = df_merged.copy()
            st.session_state.seleccionados = []
            st.session_state.pop("sim_e1_params", None)
            st.session_state.pop("sim_e2_params", None)
        else:
            st.session_state.df_base = pd.DataFrame()
            st.session_state.df_filtrado = pd.DataFrame()
            st.session_state.seleccionados = []

    progress_bar.progress(100)
    status_text.text("Carga completada (100%)")
    st.success("Datos actualizados correctamente desde EcomExperts y Postgres.")

# FILTROS
st.markdown("### Filtros sobre datos cargados")
st.caption("Primero pulsa 'Consultar datos'. Luego aplica filtros sin volver a llamar a Ecom ni a Postgres.")

col1, col2, col3, col4 = st.columns(4)
with col1:
    f_ml_id  = st.text_input("ML ID")
    f_titulo = st.text_input("Titulo")
with col2:
    f_sku    = st.text_input("SKU")
    f_ml_sinc = st.text_input("ML ID Sincronizados")
with col3:
    f_estado = st.selectbox("Estado",           ["Todos"] + estados)
    f_tipo   = st.selectbox("Tipo publicacion", ["Todos"] + tipos)
with col4:
    f_logistica = st.selectbox("Logistica",    ["Todas"] + logisticas)
    f_envio     = st.selectbox("Envio gratis", ["Todos", "Si", "No"])

col_filt, col_lim = st.columns([1, 2])
with col_filt:
    btn_filtrar = st.button("Aplicar filtros", use_container_width=True)
with col_lim:
    limite = st.selectbox("Limite de resultados (solo visual)", options=[50, 100, 200, 300, 500, 1000], index=3)

if btn_filtrar:
    if st.session_state.df_base.empty:
        st.warning("Primero pulsa 'Consultar datos' para cargar la información.")
    else:
        df_vista = apply_filtros(
            st.session_state.df_base,
            f_ml_id, f_titulo, f_sku, f_ml_sinc, f_estado, f_tipo, f_logistica, f_envio
        )
        if limite:
            df_vista = df_vista.head(limite)
        st.session_state.df_filtrado = df_vista.copy()
        st.session_state.seleccionados = []
        st.session_state.pop("sim_e1_params", None)
        st.session_state.pop("sim_e2_params", None)

# TABLA RESULTADOS (FILTRADOS)
df_vista = st.session_state.df_filtrado

if not df_vista.empty:
    st.markdown(f"**{len(df_vista)} publicaciones en la vista filtrada**")
    st.markdown("---")

    df_show = df_vista.copy()
    cols_inicio = ["ml_id", "titulo_ecom", "sku_asociados"]
    otras_cols  = [c for c in df_show.columns if c not in cols_inicio]
    df_show = df_show[cols_inicio + otras_cols]

    for col in [
        "precio_venta_final",
        "precio_venta_base",
        "costo_fijo_ecom",
        "costo_fijo_ecom_antiguo",
        "costo_fijo_ecom_nuevo",
        "costo_envio",
        "costo_und_vendida",
        "costo_venta",
        "valor_iva",
        "rentabilidad",
    ]:
        if col in df_show.columns:
            df_show[col] = df_show[col].apply(fmt)

    if "pct_rentabilidad" in df_show.columns:
        df_show["pct_rentabilidad"] = (df_show["pct_rentabilidad"].astype(float) * 100).round(2)

    st.dataframe(
        df_show,
        use_container_width=True,
        height=500,
        column_config={
            "ml_id":                     st.column_config.TextColumn("ML ID",        width="medium"),
            "titulo_ecom":               st.column_config.TextColumn("Titulo",       width="large"),
            "sku_asociados":             st.column_config.TextColumn("SKU",          width="medium"),
            "estado_meli":               st.column_config.TextColumn("Estado",       width="small"),
            "tipo_publicacion":          st.column_config.TextColumn("Tipo",         width="medium"),
            "logistica":                 st.column_config.TextColumn("Logistica",    width="medium"),
            "envio_gratis":              st.column_config.TextColumn("Envio Gratis", width="small"),
            "campaign_ofrecida":         st.column_config.TextColumn("Campaign",     width="medium"),
            "rango_peso_facturable":     st.column_config.TextColumn("Rango Peso",   width="medium"),
            "rango_valor_costo_envio":   st.column_config.TextColumn("Rango Envio",  width="medium"),
            "rango_valor_costo_und_vendida": st.column_config.TextColumn("Rango Und", width="medium"),
        }
    )

    st.markdown("---")

    st.markdown("**Selecciona publicaciones para simular (sobre datos filtrados):**")

    resultados_vista = df_vista.to_dict(orient="records")

    col_sel_all, col_des_all = st.columns([1, 1])
    with col_sel_all:
        if st.button("Seleccionar todas", key="btn_sel_all"):
            st.session_state.seleccionados = [str(r["ml_id"]) for r in resultados_vista]
            st.experimental_rerun()
    with col_des_all:
        if st.button("Deseleccionar todas", key="btn_des_all"):
            st.session_state.seleccionados = []
            st.experimental_rerun()

    seleccion = st.multiselect(
        "Publicaciones a simular",
        options=[str(r["ml_id"]) for r in resultados_vista],
        format_func=lambda x: next(
            (f"{r['ml_id']} | {str(r['titulo_ecom'])[:60]} | {fmt(r['precio_venta_final'])}"
             for r in resultados_vista if str(r["ml_id"]) == x),
            x
        ),
        default=st.session_state.seleccionados,
        key="multiselect_sim",
        label_visibility="collapsed"
    )
    st.session_state.seleccionados = seleccion

    if st.session_state.seleccionados:
        st.success(f"{len(st.session_state.seleccionados)} publicacion(es) seleccionada(s)")

    st.markdown("---")

    if st.session_state.seleccionados:
        st.markdown(f"## Simulacion de {len(st.session_state.seleccionados)} publicacion(es)")

        col_e1, col_e2 = st.columns(2)

        # ESCENARIO 1
        with col_e1:
            st.markdown("**Escenario 1: Cambio de precio**")
            with st.form(key="form_e1"):
                nuevo_precio_global = st.number_input(
                    "Nuevo precio de venta",
                    min_value=0.0, value=0.0, step=1000.0
                )
                campaign_global = st.selectbox(
                    "Campaign ofrecida",
                    list(CAMPAIGN_CUOTAS.keys())
                )
                envio_e1 = st.selectbox(
                    "Envio gratis (simulacion)",
                    ["Si", "No"],
                    index=0
                )
                simular_e1 = st.form_submit_button("Simular Escenario 1", use_container_width=True)

            if simular_e1:
                if nuevo_precio_global <= 0:
                    st.error("Ingresa un precio de venta mayor a 0")
                else:
                    st.session_state["sim_e1_params"] = {
                        "precio":        nuevo_precio_global,
                        "campaign":      campaign_global,
                        "envio":         envio_e1,
                        "seleccionados": list(st.session_state.seleccionados)
                    }
                    st.session_state.pop("sim_e2_params", None)

        # ESCENARIO 2
        with col_e2:
            st.markdown("**Escenario 2: Rentabilidad objetivo**")
            with st.form(key="form_e2"):
                pct_obj_global = st.number_input(
                    "Porcentaje de rentabilidad objetivo",
                    min_value=0.0, max_value=500.0,
                    value=30.0, step=1.0
                )
                campaign_global_e2 = st.selectbox(
                    "Campaign ofrecida",
                    list(CAMPAIGN_CUOTAS.keys())
                )
                envio_e2 = st.selectbox(
                    "Envio gratis (simulacion)",
                    ["Si", "No"],
                    index=0
                )
                simular_e2 = st.form_submit_button("Simular Escenario 2", use_container_width=True)

            if simular_e2:
                st.session_state["sim_e2_params"] = {
                    "pct_obj":       pct_obj_global,
                    "campaign":      campaign_global_e2,
                    "envio":         envio_e2,
                    "seleccionados": list(st.session_state.seleccionados)
                }
                st.session_state.pop("sim_e1_params", None)

        # RESULTADOS ESCENARIO 1
        if "sim_e1_params" in st.session_state:
            p = st.session_state["sim_e1_params"]
            st.markdown("### Resultados Escenario 1")
            pct_cuotas = CAMPAIGN_CUOTAS[p["campaign"]]
            incluir_envio = (p["envio"] == "Si")
            base_records = st.session_state.df_base.to_dict(orient="records")
            for ml_id in p["seleccionados"]:
                producto = next(
                    (r for r in base_records if str(r["ml_id"]) == str(ml_id)),
                    None
                )
                if not producto:
                    continue
                sim = calcular_rentabilidad(producto, p["precio"], pct_cuotas, incluir_envio)
                with st.expander(f"{producto['titulo_ecom']} | ML: {ml_id}", expanded=True):
                    mostrar_comparativo(producto, sim, escenario="1", nombre_campania=p["campaign"])

        # RESULTADOS ESCENARIO 2
        if "sim_e2_params" in st.session_state:
            p = st.session_state["sim_e2_params"]
            st.markdown("### Resultados Escenario 2")
            pct_cuotas2 = CAMPAIGN_CUOTAS[p["campaign"]]
            incluir_envio2 = (p["envio"] == "Si")
            base_records = st.session_state.df_base.to_dict(orient="records")
            for ml_id in p["seleccionados"]:
                producto = next(
                    (r for r in base_records if str(r["ml_id"]) == str(ml_id)),
                    None
                )
                if not producto:
                    continue
                precio_sug = simular_escenario_2(producto, p["pct_obj"], pct_cuotas2, incluir_envio2)
                sim2       = calcular_rentabilidad(producto, precio_sug, pct_cuotas2, incluir_envio2)
                with st.expander(f"{producto['titulo_ecom']} | ML: {ml_id}", expanded=True):
                    st.markdown(f"**Precio sugerido para {p['pct_obj']:.1f}% de rentabilidad: {fmt(precio_sug)}**")
                    mostrar_comparativo(producto, sim2, escenario="2", nombre_campania=p["campaign"])
