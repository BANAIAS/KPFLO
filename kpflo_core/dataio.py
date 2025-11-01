# dataio.py
# -------------------------------------------------------------------
# Gère la création, la validation, le chargement et la sauvegarde
# des transactions financières au format CSV.
# -------------------------------------------------------------------

from __future__ import annotations
import pandas as pd
from datetime import date, datetime

# Schéma standard des transactions
SCHEMA = ["date", "type", "categorie", "libelle", "montant", "recurrent"]


def empty_df() -> pd.DataFrame:
    """Crée un DataFrame vide avec le schéma de base."""
    return pd.DataFrame(columns=SCHEMA)


def _to_date(d) -> pd.Timestamp:
    """Convertit une date (str, datetime, Timestamp) en format Timestamp."""
    if isinstance(d, (pd.Timestamp,)):
        return d
    if isinstance(d, (datetime, date)):
        return pd.to_datetime(d)
    return pd.to_datetime(str(d))


def validate_entry(date_val, type_val, categorie, libelle, montant, recurrent=False):
    """Vérifie la validité d'une transaction avant ajout."""
    if type_val not in {"IN", "OUT"}:
        raise ValueError("type doit être 'IN' ou 'OUT'.")
    if not libelle or not str(libelle).strip():
        raise ValueError("libelle vide.")
    try:
        _ = float(montant)
    except Exception:
        raise ValueError("montant doit être un nombre.")
    if type_val == "IN" and float(montant) <= 0:
        raise ValueError("Revenu (IN) → montant > 0.")
    if type_val == "OUT" and float(montant) >= 0:
        raise ValueError("Dépense (OUT) → montant < 0.")
    _ = _to_date(date_val)


def add_entry(
    df: pd.DataFrame, date_val, type_val, categorie, libelle, montant, recurrent=False
) -> pd.DataFrame:
    """Ajoute une transaction validée au DataFrame existant."""
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
    """Charge un fichier CSV ou crée un DataFrame vide si le fichier n’existe pas."""
    try:
        df = pd.read_csv(path, parse_dates=["date"])
    except FileNotFoundError:
        return empty_df()
    for c in SCHEMA:
        if c not in df.columns:
            df[c] = [] if c != "recurrent" else False
    return df[SCHEMA].copy()


def save_csv(df: pd.DataFrame, path: str):
    """Sauvegarde le DataFrame dans un fichier CSV (sans index)."""
    df.to_csv(path, index=False)
