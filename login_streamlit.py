# login_streamlit.py
import json
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGIN_URL = "https://api.ecomexperts.com/users/users/doLogin.json"
COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}
TIMEOUT = (20, 90)


def build_session() -> requests.Session:
    """
    Crea una sesión HTTP con reintentos para hablar con EcomExperts.
    """
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(COMMON_HEADERS)
    return session


def login_session(email: str, password: str) -> requests.Session:
    """
    Hace login contra EcomExperts y devuelve una sesión autenticada
    (con cookie de sesión válida) o lanza ValueError si falla.
    """
    session = build_session()
    payload_login = {"User": {"email_address": email, "password": password}}
    resp = session.post(LOGIN_URL, data=json.dumps(payload_login), timeout=TIMEOUT)
    resp.raise_for_status()

    try:
        body = resp.json()
    except Exception:
        body = {}

    if isinstance(body, dict) and (body.get("error") or body.get("errors")):
        raise ValueError(f"Login inválido: {body.get('error') or body.get('errors')}")
    if len(session.cookies) == 0:
        raise ValueError("Login inválido: no se recibió cookie de sesión.")
    return session


def login_ecom():
    """
    Renderiza el formulario de login para el simulador usando credenciales de Ecom.
    Si el login es exitoso, setea en st.session_state:
      - authenticated = True
      - ecom_session = sesión requests.Session autenticada
      - ecom_email   = correo usado
    """
    st.title("RD Simulador - Acceso EcomExperts")
    st.caption("Ingresa con tus credenciales de EcomExperts para usar el simulador.")

    email = st.text_input("Correo EcomExperts", key="ecom_email_input")
    password = st.text_input("Contraseña EcomExperts", type="password", key="ecom_password_input")

    login_clicked = st.button("Ingresar", use_container_width=True, key="btn_login_ecom")

    # Logout: limpiar estado y recargar
    if logout_clicked:
        for k in ["authenticated", "ecom_session", "ecom_email"]:
            if k in st.session_state:
                st.session_state.pop(k)
        st.success("Sesión cerrada")
        st.experimental_rerun()

    # Login
    if login_clicked:
        if not email or not password:
            st.warning("Debes ingresar correo y contraseña de EcomExperts.")
        else:
            try:
                with st.spinner("Validando credenciales con EcomExperts..."):
                    session = login_session(email, password)
                st.session_state["authenticated"] = True
                st.session_state["ecom_session"] = session
                st.session_state["ecom_email"] = email
                st.success("Acceso concedido")
                st.experimental_rerun()
            except Exception as e:
                st.session_state["authenticated"] = False
                st.session_state["ecom_session"] = None
                st.error(f"No fue posible validar las credenciales: {e}")
