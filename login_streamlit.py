import streamlit as st
from ecom_client import login_session

def login_ecom():
    st.title("RD Simulador - Acceso EcomExperts")
    email = st.text_input("Correo EcomExperts")
    password = st.text_input("Contraseña EcomExperts", type="password")

    col1, col2 = st.columns(2)
    with col1:
        login_clicked = st.button("Ingresar", use_container_width=True)
    with col2:
        logout_clicked = st.button("Salir", use_container_width=True)

    if logout_clicked:
        for k in ["authenticated", "ecom_session", "ecom_email"]:
            if k in st.session_state:
                st.session_state.pop(k)
        st.rerun()

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
                st.rerun()
            except Exception as e:
                st.session_state["authenticated"] = False
                st.session_state["ecom_session"] = None
                st.error(f"No fue posible validar las credenciales: {e}")
