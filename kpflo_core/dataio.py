from __future__ import annotations
import pandas as pd
from datetime import date, datetime

SCHEMA = ["date", "type", "categorie", "libelle", "montant", "recurrent"]
# type ∈ {"IN","OUT"} ; montant >0 pour IN, <0 pour OUT

def empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=SCHEMA)

def _to_date(d) -> pd.Timestamp:
    if isinstance(d, (pd.Timestamp, )):
        return d
    if isinstance(d, (datetime, date)):
        return pd.to_datetime(d)
    return pd.to_datetime(str(d))

def validate_entry(date_val, type_val, categorie, libelle, montant, recurrent=False):
    if type_val not in {"IN","OUT"}:
        raise ValueError("type doit être 'IN' (revenu) ou 'OUT' (dépense).")
    if not libelle or not str(libelle).strip():
        raise ValueError("libelle vide.")
    try:
        _ = float(montant)
    except Exception:
        raise ValueError("montant doit être un nombre.")
    # signe attendu: IN > 0 ; OUT < 0
    if type_val == "IN" and float(montant) <= 0:
        raise ValueError("Pour un revenu (IN), le montant doit être > 0.")
    if type_val == "OUT" and float(montant) >= 0:
        raise ValueError("Pour une dépense (OUT), le montant doit être < 0.")
    _ = _to_date(date_val)  # valide la date

def add_entry(df: pd.DataFrame, date_val, type_val, categorie, libelle, montant, recurrent=False) -> pd.DataFrame:
    validate_entry(date_val, type_val, categorie, libelle, montant, recurrent)
    row = {
        "date": _to_date(date_val),
        "type": type_val,
        "categorie": str(categorie),
        "libelle": str(libelle).strip(),
        "montant": float(montant),
        "recurrent": bool(recurrent),
    }
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)

def load_csv(path: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(path, parse_dates=["date"])
    except FileNotFoundError:
        return empty_df()
    # garantit l'ordre/colonnes
    for c in SCHEMA:
        if c not in df.columns:
            df[c] = [] if c != "recurrent" else False
    return df[SCHEMA].copy()

def save_csv(df: pd.DataFrame, path: str):
    df.to_csv(path, index=False)
