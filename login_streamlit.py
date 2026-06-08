import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import streamlit as st

load_dotenv()

def get_connection():
    conn = psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT"),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        sslmode=os.getenv("PGSSLMODE", "require"),
    )
    return conn

def check_user(username, password_plain):
    query = """
        SELECT id, username, password_hash, role
        FROM rd_usuarios
        WHERE username = %s
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (username,))
            row = cur.fetchone()
            if not row:
                return None
            if password_plain != row["password_hash"]:
                return None
            return row

def main():
    st.set_page_config(page_title="Login RD Simulador", page_icon="🔐", layout="centered")

    st.title("Simulador Rentabilidad Mercado Libre - Login")

    username = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")

    if st.button("Ingresar"):
        user = check_user(username, password)
        if user:
            st.session_state["usuario"] = {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
            }
            st.success("Login exitoso. Luego ejecuta app_streamlit.py para usar el simulador.")
        else:
            st.error("Usuario o contraseña incorrectos.")

if __name__ == "__main__":
    main()