import streamlit as st
import psycopg2
import psycopg2.extras
import pandas as pd
from passlib.context import CryptContext

st.set_page_config(
    page_title="RD Simulador - Mercado Libre",
    page_icon="📊",
    layout="wide"
)

# Para despliegue en Streamlit Cloud conviene leer esto de st.secrets["DATABASE_URL"]
DATABASE_URL = st.secrets["DATABASE_URL"]

pwd_context  = CryptContext(
    schemes=["bcrypt"],
    default="bcrypt",
    bcrypt__rounds=12,
    truncate_error=True,
)

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

# --- CLASIFICACIÓN DE RANGOS (COINCIDE CON rd_tabla_costos) ------------------

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

# --- LOOKUPS SEPARADOS EN rd_tabla_costos ------------------------------------

def lookup_costo_und(rango_peso, rango_und):
    """
    Devuelve Costo_por_unidad_vendida usando:
    - rango_peso_facturable
    - rango_valor_costo_und_vendida
    """
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
    """
    Devuelve costo_envio usando:
    - rango_peso_facturable
    - rango_valor_costo_envio
    """
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

# --- LÓGICA DE RENTABILIDAD ---------------------------------------------------

def calcular_rentabilidad(producto, precio_sim, pct_cuotas):
    """
    precio_sim: precio final simulado, YA incluye IVA.
    iva_venta (iva_tasa) es la tasa (por ejemplo 0.21 para 21%).
    Se descompone precio_sim en base imponible + IVA.
    """
    costo_fijo   = float(producto.get("costo_fijo_ecom") or 0)
    pct_venta    = float(producto.get("pct_costo_venta") or 0)
    iva_tasa     = float(producto.get("iva_venta") or 0)
    rango_peso   = producto.get("rango_peso_facturable")
    envio_gratis = producto.get("envio_gratis")

    # Rangos dinámicos según precio_sim
    rango_envio = clasificar_rango_envio(precio_sim)
    rango_und   = clasificar_rango_und(precio_sim)

    # Costo envío: solo si envio_gratis = True
    if envio_gratis:
        costo_envio_sim = lookup_costo_envio(rango_peso, rango_envio)
    else:
        costo_envio_sim = 0.0

    # Costo und vendida: solo si el rango aplica
    if rango_und != "No Aplica":
        costo_und_sim = lookup_costo_und(rango_peso, rango_und)
    else:
        costo_und_sim = 0.0

    # Descomponer precio_sim (que ya incluye IVA) en base imponible + IVA
    factor_iva = 1.0 + (iva_tasa or 0.0)
    if factor_iva <= 0:
        base_imponible = precio_sim
        valor_iva_sim = 0.0
    else:
        base_imponible = precio_sim / factor_iva
        valor_iva_sim = precio_sim - base_imponible

    # Costos variables calculados sobre el precio final (si así está definido)
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

def simular_escenario_2(producto, pct_obj, pct_cuotas):
    costo_fijo = float(producto.get("costo_fijo_ecom") or 0)
    rent_obj   = (pct_obj / 100) * costo_fijo

    def f(p):
        r = calcular_rentabilidad(producto, p, pct_cuotas)
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

    # Encabezado con ML_ID y título
    st.markdown(
        f"#### {producto.get('ml_id', '')}  \n"
        f"{producto.get('titulo_ecom', '')}"
    )

    col_actual, col_sim = st.columns(2)

    card_style = "border:1px solid #444;border-radius:6px;padding:10px;margin-bottom:10px;background-color:#111;"
    value_green_style = "color:#00c853;font-weight:bold;font-size:1.1rem;"

    # --- PANEL ACTUAL --------------------------------------------------------
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

        # bloque tipo tabla, todo dentro de un único div (sin cuadro vacío)
        html_actual = f"""
        <div style="{card_style}">
          <table style="width:100%;font-size:0.9rem;">
            <tr>
              <td>Costo fijo:</td><td>{fmt(producto.get('costo_fijo_ecom'))}</td>
              <td>Costo venta:</td><td>{fmt(producto.get('costo_venta'))}</td>
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

    # --- PANEL SIMULADO ------------------------------------------------------
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

    # --- VARIACIONES ---------------------------------------------------------
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

# ── SESSION STATE ─────────────────────────────────────────────────────────────
for key, default in [
    ("logged_in", False),
    ("user", None),
    ("show_pwd", False),
    ("resultados", None),
    ("seleccionados", []),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── LOGIN ─────────────────────────────────────────────────────────────────────
if not st.session_state.logged_in:
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("## RD Simulador")
        st.markdown("**Mercado Libre - Simulador de Rentabilidad**")
        st.markdown("---")
        username = st.text_input("Usuario")
        password = st.text_input("Contrasena", type="password")
        if st.button("Ingresar", use_container_width=True, key="btn_login"):
            user = login(username, password)
            if user:
                st.session_state.logged_in = True
                st.session_state.user = user
                st.rerun()
            else:
                st.error("Usuario o contrasena incorrectos")
    st.stop()

# ── APP PRINCIPAL ─────────────────────────────────────────────────────────────
st.markdown(f"### RD Simulador | Usuario: {st.session_state.user['username']}")

col_logout, col_pwd, col_space = st.columns([1, 1, 6])
with col_logout:
    if st.button("Salir", key="btn_salir"):
        st.session_state.logged_in = False
        st.session_state.user = None
        st.session_state.resultados = None
        st.session_state.seleccionados = []
        st.session_state.pop("sim_e1_params", None)
        st.session_state.pop("sim_e2_params", None)
        st.rerun()
with col_pwd:
    if st.button("Cambiar contrasena", key="btn_cambiar_pwd"):
        st.session_state.show_pwd = not st.session_state.show_pwd

if st.session_state.show_pwd:
    with st.expander("Cambiar contrasena", expanded=True):
        old_pwd = st.text_input("Contrasena actual", type="password", key="old_pwd")
        new_pwd = st.text_input("Nueva contrasena",  type="password", key="new_pwd")
        con_pwd = st.text_input("Confirmar nueva",   type="password", key="con_pwd")
        if st.button("Actualizar contrasena", key="btn_actualizar_pwd"):
            if new_pwd != con_pwd:
                st.error("Las contrasenas nuevas no coinciden")
            elif len(new_pwd) < 6:
                st.error("La nueva contrasena debe tener al menos 6 caracteres")
            else:
                user = login(st.session_state.user["username"], old_pwd)
                if not user:
                    st.error("Contrasena actual incorrecta")
                else:
                    new_hash = pwd_context.hash(new_pwd)
                    execute(
                        "UPDATE rd_usuarios SET password_hash = %s WHERE username = %s",
                        (new_hash, st.session_state.user["username"])
                    )
                    st.success("Contrasena actualizada")
                    st.session_state.show_pwd = False

st.markdown("---")

# ── OPCIONES LISTAS ───────────────────────────────────────────────────────────
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

# ── FILTROS ───────────────────────────────────────────────────────────────────
st.markdown("### Filtros de busqueda")
st.caption("Deja todos los filtros vacios para ver todas las publicaciones")

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

col_bus, col_lim = st.columns([1, 2])
with col_bus:
    buscar = st.button("Buscar / Mostrar todo", use_container_width=True, key="btn_buscar")
with col_lim:
    limite = st.selectbox("Limite de resultados", options=[50, 100, 200, 300, 500], index=2)

if buscar:
    conditions = []
    params     = []

    if f_ml_id.strip():
        conditions.append("CAST(ml_id AS TEXT) ILIKE %s")
        params.append(f"%{f_ml_id.strip()}%")
    if f_titulo.strip():
        conditions.append("titulo_ecom ILIKE %s")
        params.append(f"%{f_titulo.strip()}%")
    if f_sku.strip():
        conditions.append("sku_asociados ILIKE %s")
        params.append(f"%{f_sku.strip()}%")
    if f_ml_sinc.strip():
        conditions.append("CAST(ml_id_sincronizados AS TEXT) ILIKE %s")
        params.append(f"%{f_ml_sinc.strip()}%")
    if f_estado != "Todos":
        conditions.append("estado_meli = %s")
        params.append(f_estado)
    if f_tipo != "Todos":
        conditions.append("tipo_publicacion = %s")
        params.append(f_tipo)
    if f_logistica != "Todas":
        conditions.append("logistica = %s")
        params.append(f_logistica)
    if f_envio == "Si":
        conditions.append("envio_gratis = true")
    elif f_envio == "No":
        conditions.append("envio_gratis = false")

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limite)

    query = (
        "SELECT * FROM rd_tabla_rentas "
        + where_sql +
        " ORDER BY titulo_ecom LIMIT %s"
    )

    st.session_state.resultados    = fetch_all(query, tuple(params))
    st.session_state.seleccionados = []
    st.session_state.pop("sim_e1_params", None)
    st.session_state.pop("sim_e2_params", None)

# ── TABLA RESULTADOS ──────────────────────────────────────────────────────────
if st.session_state.resultados is not None:
    resultados = st.session_state.resultados
    if len(resultados) == 0:
        st.warning("No se encontraron publicaciones con esos filtros")
    else:
        st.markdown(f"**{len(resultados)} publicaciones encontradas**")
        st.markdown("---")

        df = pd.DataFrame(resultados)
        cols_inicio = ["ml_id", "titulo_ecom", "sku_asociados"]
        otras_cols  = [c for c in df.columns if c not in cols_inicio]
        df = df[cols_inicio + otras_cols]

        # Formatear montos como texto moneda (evita signos de alerta)
        for col in [
            "precio_venta_final",
            "precio_venta_base",
            "costo_fijo_ecom",
            "costo_envio",
            "costo_und_vendida",
            "costo_venta",
            "valor_iva",
            "rentabilidad",
        ]:
            if col in df.columns:
                df[col] = df[col].apply(fmt)

        if "pct_rentabilidad" in df.columns:
            df["pct_rentabilidad"] = (df["pct_rentabilidad"].astype(float) * 100).round(2)

        st.dataframe(
            df,
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

        st.markdown("**Selecciona publicaciones para simular:**")

        col_sel_all, col_des_all = st.columns([1, 1])
        with col_sel_all:
            if st.button("Seleccionar todas", key="btn_sel_all"):
                st.session_state.seleccionados = [str(r["ml_id"]) for r in resultados]
                st.rerun()
        with col_des_all:
            if st.button("Deseleccionar todas", key="btn_des_all"):
                st.session_state.seleccionados = []
                st.rerun()

        seleccion = st.multiselect(
            "Publicaciones a simular",
            options=[str(r["ml_id"]) for r in resultados],
            format_func=lambda x: next(
                (f"{r['ml_id']} | {str(r['titulo_ecom'])[:60]} | {fmt(r['precio_venta_final'])}"
                 for r in resultados if str(r["ml_id"]) == x),
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
                    simular_e1 = st.form_submit_button("Simular Escenario 1", use_container_width=True)

                if simular_e1:
                    if nuevo_precio_global <= 0:
                        st.error("Ingresa un precio de venta mayor a 0")
                    else:
                        st.session_state["sim_e1_params"] = {
                            "precio":        nuevo_precio_global,
                            "campaign":      campaign_global,
                            "seleccionados": list(st.session_state.seleccionados)
                        }
                        st.session_state.pop("sim_e2_params", None)

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
                    simular_e2 = st.form_submit_button("Simular Escenario 2", use_container_width=True)

                if simular_e2:
                    st.session_state["sim_e2_params"] = {
                        "pct_obj":       pct_obj_global,
                        "campaign":      campaign_global_e2,
                        "seleccionados": list(st.session_state.seleccionados)
                    }
                    st.session_state.pop("sim_e1_params", None)

            # ── RESULTADOS ESCENARIO 1 ────────────────────────────────────────
            if "sim_e1_params" in st.session_state:
                p = st.session_state["sim_e1_params"]
                st.markdown("### Resultados Escenario 1")
                pct_cuotas = CAMPAIGN_CUOTAS[p["campaign"]]
                for ml_id in p["seleccionados"]:
                    producto = fetch_one(
                        "SELECT * FROM rd_tabla_rentas WHERE CAST(ml_id AS TEXT) = %s",
                        (ml_id,)
                    )
                    if not producto:
                        continue
                    sim = calcular_rentabilidad(producto, p["precio"], pct_cuotas)
                    with st.expander(f"{producto['titulo_ecom']} | ML: {ml_id}", expanded=True):
                        mostrar_comparativo(producto, sim, escenario="1", nombre_campania=p["campaign"])

            # ── RESULTADOS ESCENARIO 2 ────────────────────────────────────────
            if "sim_e2_params" in st.session_state:
                p = st.session_state["sim_e2_params"]
                st.markdown("### Resultados Escenario 2")
                pct_cuotas2 = CAMPAIGN_CUOTAS[p["campaign"]]
                for ml_id in p["seleccionados"]:
                    producto = fetch_one(
                        "SELECT * FROM rd_tabla_rentas WHERE CAST(ml_id AS TEXT) = %s",
                        (ml_id,)
                    )
                    if not producto:
                        continue
                    precio_sug = simular_escenario_2(producto, p["pct_obj"], pct_cuotas2)
                    sim2       = calcular_rentabilidad(producto, precio_sug, pct_cuotas2)
                    with st.expander(f"{producto['titulo_ecom']} | ML: {ml_id}", expanded=True):
                        st.markdown(f"**Precio sugerido para {p['pct_obj']:.1f}% de rentabilidad: {fmt(precio_sug)}**")
                        mostrar_comparativo(producto, sim2, escenario="2", nombre_campania=p["campaign"])
