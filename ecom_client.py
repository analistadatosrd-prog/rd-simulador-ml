# ecom_client.py
import json
import time
from typing import Callable, Tuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGIN_URL = "https://api.ecomexperts.com/users/users/doLogin.json"
GRAPHQL_URL = "https://api.ecomexperts.com/graphql"
ACCOUNT_ID_OBJETIVO = "33833"
TIMEOUT = (20, 90)
PAGE_DELAY = 0.05

COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}


def build_session() -> requests.Session:
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


def post_graphql(session: requests.Session, query: str) -> dict:
    payload = {"query": query, "operationName": None}
    resp = session.post(GRAPHQL_URL, data=json.dumps(payload), timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise ValueError(f"GraphQL error: {data['errors']}")
    return data


def fetch_ml_listings_fast(
    session: requests.Session,
    status_callback: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    all_rows = []
    current_page = 1

    while True:
        if status_callback:
            status_callback(f"Consultando mlListings - página {current_page}...")

        query = f"""
        query {{
          mlListings {{
            find(page: {current_page}, filters:[{{ filter:"active", values:["1"] }}]) {{
              data {{
                id
                accountId
                owner
                ownerId
                productListings {{
                  qty
                  productId
                  productVariantId
                  product {{
                    sku
                    title
                  }}
                }}
              }}
            }}
          }}
        }}
        """

        data = post_graphql(session, query)
        listings = data.get("data", {}).get("mlListings", {}).get("find", {}).get("data", [])
        if not listings:
            break

        for listing in listings:
            account_id = str(listing.get("accountId", ""))
            if account_id != ACCOUNT_ID_OBJETIVO:
                continue

            owner = str(listing.get("owner", ""))
            mla = str(listing.get("ownerId", ""))
            for item in listing.get("productListings") or []:
                product = item.get("product") or {}
                all_rows.append(
                    {
                        "mla": mla,
                        "owner": owner,
                        "account_id": account_id,
                        "product_id": str(item.get("productId", "") or ""),
                        "product_variant_id": str(item.get("productVariantId", "") or ""),
                        "sku": str(product.get("sku", "") or ""),
                        "titulo_producto_base": str(product.get("title", "") or ""),
                        "unidades": pd.to_numeric(item.get("qty"), errors="coerce"),
                    }
                )

        current_page += 1
        if PAGE_DELAY:
            time.sleep(PAGE_DELAY)

    return pd.DataFrame(all_rows)


def fetch_products_fast(
    session: requests.Session,
    status_callback: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    rows = []
    current_page = 1

    while True:
        if status_callback:
            status_callback(f"Consultando products - página {current_page}...")

        query = f"""
        query {{
          products {{
            find(page: {current_page}) {{
              data {{
                sku
                title
                tax
                variants {{
                  sku
                  cost
                }}
              }}
            }}
          }}
        }}
        """

        data = post_graphql(session, query)
        products = data.get("data", {}).get("products", {}).get("find", {}).get("data", [])
        if not products:
            break

        for product in products:
            product_sku = str(product.get("sku", "") or "")
            product_title = str(product.get("title", "") or "")
            product_tax = product.get("tax", None)

            tax_value = None
            if isinstance(product_tax, dict):
                for k in ["iva", "IVA", "tax", "value", "amount", "percentage", "percent"]:
                    if k in product_tax:
                        tax_value = product_tax[k]
                        break
            else:
                tax_value = product_tax

            variants = product.get("variants") or []
            if variants:
                costs = [pd.to_numeric(v.get("cost"), errors="coerce") for v in variants]
                costs = [c for c in costs if pd.notna(c)]
                max_cost = max(costs) if costs else None
            else:
                max_cost = None

            rows.append(
                {
                    "sku": product_sku,
                    "titulo_catalogo": product_title,
                    "costo_unitario": pd.to_numeric(max_cost, errors="coerce"),
                    "iva": pd.to_numeric(tax_value, errors="coerce"),
                }
            )

        current_page += 1
        if PAGE_DELAY:
            time.sleep(PAGE_DELAY)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["sku", "titulo_catalogo", "costo_unitario", "iva"])

    df["sku"] = df["sku"].astype(str)
    return (
        df.groupby("sku", as_index=False)
        .agg(
            {
                "titulo_catalogo": "first",
                "costo_unitario": "max",
                "iva": "max",
            }
        )
    )


def clasificar_mla(num_skus: int, total_qty: float) -> str:
    if num_skus == 1 and total_qty == 1:
        return "monoproducto"
    if num_skus == 1 and total_qty > 1:
        return "monoproducto multioferta"
    if num_skus > 1:
        return "combo"
    return "sin clasificar"


def build_outputs(
    df_listings: pd.DataFrame,
    df_products: pd.DataFrame,
    status_callback: Callable[[str], None] | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Devuelve:
      - detalle: detalle MLA + SKU
      - final_df: una fila por MLA con costo_total_mla, unidades_totales, iva, etc.
    """
    if df_listings.empty:
        detalle = pd.DataFrame(
            columns=[
                "mla",
                "sku",
                "unidades",
                "costo_unitario",
                "costo_total_sku",
                "iva",
                "titulo_final",
            ]
        )
        final_df = pd.DataFrame(
            columns=[
                "mla",
                "titulo_producto",
                "skus_asociados",
                "unidades_totales",
                "costo_total_mla",
                "iva",
                "cant_sku",
                "tipo_producto",
            ]
        )
        return detalle, final_df

    if status_callback:
        status_callback("Agrupando detalle por MLA + SKU...")

    detalle = (
        df_listings.groupby(["mla", "sku"], as_index=False)
        .agg(
            {
                "unidades": "sum",
                "titulo_producto_base": lambda s: " | ".join(
                    sorted(set([str(x) for x in s if str(x).strip()]))
                ),
                "owner": "first",
                "account_id": "first",
            }
        )
    )

    if status_callback:
        status_callback("Cruzando con catálogo de productos...")

    detalle = detalle.merge(df_products, on="sku", how="left")
    detalle["costo_unitario"] = pd.to_numeric(detalle["costo_unitario"], errors="coerce").fillna(0)
    detalle["iva"] = pd.to_numeric(detalle["iva"], errors="coerce")
    detalle["unidades"] = pd.to_numeric(detalle["unidades"], errors="coerce").fillna(0)
    detalle["titulo_final"] = (
        detalle["titulo_producto_base"].replace("", pd.NA).fillna(detalle["titulo_catalogo"])
    )
    detalle["costo_total_sku"] = detalle["costo_unitario"] * detalle["unidades"]

    detalle = (
        detalle[
            [
                "mla",
                "sku",
                "unidades",
                "costo_unitario",
                "costo_total_sku",
                "iva",
                "titulo_final",
            ]
        ]
        .sort_values(["mla", "sku"])
        .reset_index(drop=True)
    )

    if status_callback:
        status_callback("Construyendo resumen final por MLA...")

    final_df = (
        detalle.groupby("mla", as_index=False)
        .agg(
            titulo_producto=(
                "titulo_final",
                lambda s: " | ".join(sorted(set([str(x) for x in s if str(x).strip()]))),
            ),
            skus_asociados=(
                "sku",
                lambda s: " | ".join(sorted(set([str(x) for x in s if str(x).strip()]))),
            ),
            unidades_totales=("unidades", "sum"),
            costo_total_mla=("costo_total_sku", "sum"),
            iva=("iva", "max"),
            cant_sku=("sku", "nunique"),
        )
    )

    final_df["tipo_producto"] = final_df.apply(
        lambda r: clasificar_mla(r["cant_sku"], r["unidades_totales"]), axis=1
    )
    final_df = (
        final_df[
            [
                "mla",
                "titulo_producto",
                "skus_asociados",
                "unidades_totales",
                "costo_total_mla",
                "iva",
                "cant_sku",
                "tipo_producto",
            ]
        ]
        .sort_values(["tipo_producto", "mla"])
        .reset_index(drop=True)
    )

    return detalle, final_df
