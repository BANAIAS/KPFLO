# kpflo_core/revenus.py
# ---------------------------------------------------------
# Logique métiers des revenus : suggestions, CRUD, helpers UI
# ---------------------------------------------------------

from datetime import date
from typing import List

import pandas as pd
import streamlit as st

from .storage_sqlite import (
    insert_transaction,
    delete_transaction,
    fetch_all_df_with_id,
    update_transaction,
)
from .categories import INCOME_CATEGORIES, INCOME_LABEL_SUGGESTIONS

# ---------- Suggestions / libellés revenus ----------


def suggestions_for_income_category(category: str) -> List[str]:
    """Retourne les stems de libellés pour une catégorie donnée."""
    return INCOME_LABEL_SUGGESTIONS.get(category, [category] if category else [])


def default_revenu_row(categories: List[str], d: date) -> dict:
    """Construit une ligne par défaut (catégorie + stem, sans suffixe mois)."""
    cat = categories[0] if categories else "Revenus professionnels"
    stem = (INCOME_LABEL_SUGGESTIONS.get(cat) or [cat])[0]
    return {"date": d, "categorie": cat, "libelle": stem, "montant": 0.0}


def generate_libelle(categorie: str, input_date: date) -> str:
    """Formate un libellé générique CAT_« Month Year » (fallback simple)."""
    month_name = input_date.strftime("%B")
    year = input_date.strftime("%Y")
    base = categorie if categorie else "Revenu"
    return f"{base}_{month_name} {year}"


# ---------- Lecture / agrégats ----------


def get_revenus_df(user_id: int) -> pd.DataFrame:
    """Récupère tous les revenus (type IN) et normalise date & montant(+)."""
    df = fetch_all_df_with_id(user_id)
    df_revenus = df[df["type"] == "IN"].copy()
    df_revenus["date"] = pd.to_datetime(df_revenus["date"]).dt.date
    df_revenus["montant"] = df_revenus["montant"].abs()
    return df_revenus


def get_revenus_summary(user_id: int) -> float:
    """Somme des revenus enregistrés pour l’utilisateur."""
    df_revenus = get_revenus_df(user_id)
    return df_revenus["montant"].sum() if not df_revenus.empty else 0.0


def get_revenus_preview_summary(user_id: int, temp_forms: list[dict]) -> float:
    """Total en base + montants en cours de saisie (aperçu)."""
    total_enregistre = get_revenus_summary(user_id)
    total_temp = sum(float(form.get("montant", 0.0)) for form in temp_forms)
    return total_enregistre + total_temp


# ---------- Validation & CRUD ----------


def validate_revenu_row(row: dict) -> bool:
    """Valide libellé non vide, montant > 0 et catégorie connue."""
    return (
        bool(row.get("libelle", "").strip())
        and float(row.get("montant", 0)) > 0
        and row.get("categorie") in INCOME_CATEGORIES
    )


def save_revenus_edits(edited_rows: list[dict], user_id: int):
    """Insère en base toutes les lignes valides (INSERT)."""
    for row in edited_rows:
        if not validate_revenu_row(row):
            continue
        insert_transaction(
            row["date"],
            "IN",
            row["categorie"],
            row["libelle"],
            float(row["montant"]),
            recurrent=False,
            user_id=user_id,
        )


def delete_revenu(tx_id: int, user_id: int):
    """Supprime un revenu par id de transaction (DELETE)."""
    delete_transaction(tx_id, user_id)


def update_revenu(tx_id: int, row: dict, user_id: int):
    """Met à jour un revenu existant si la ligne est valide (UPDATE)."""
    if not validate_revenu_row(row):
        raise ValueError("Données invalides pour mise à jour.")
    update_transaction(
        tx_id,
        row["date"],
        "IN",
        row["categorie"],
        row["libelle"],
        float(row["montant"]),
        recurrent=False,
        user_id=user_id,
    )


# ---------- Helpers UI (suffixes mensuels + callbacks Streamlit) ----------


def _month_key(d) -> str | None:
    """Clé technique 'YYYY-MM' d’une date (ou None)."""
    return pd.Timestamp(d).strftime("%Y-%m") if d else None


def _month_human(d) -> str:
    """Retourne 'Month Year' lisible (ex. 'October 2025'), sinon ''."""
    try:
        return pd.Timestamp(d).strftime("%B %Y")
    except Exception:
        return ""


def income_suggestions_for_category(cat: str, d) -> list[str]:
    """Construit les libellés proposés = stems + suffixe '_Month Year'."""
    mois = _month_human(d)
    stems = suggestions_for_income_category(cat) or [cat if cat else "Revenu"]
    return [f"{s}_{mois}" if mois else s for s in stems]


def rev_update_form(
    index, key_date, key_cat, key_lib_choice, key_lib_value, key_montant
):
    """Callback Streamlit : resynchronise la ligne (date/cat/libellé/montant)."""
    cur_date = st.session_state.get(key_date)
    cur_cat = st.session_state.get(key_cat)
    cur_choice = st.session_state.get(key_lib_choice, "")
    cur_montant = st.session_state.get(key_montant, 0.0)

    # Recalcule les options en fonction de la catégorie + mois
    opts = income_suggestions_for_category(cur_cat, cur_date)
    if cur_choice not in opts and opts:
        st.session_state[key_lib_choice] = opts[0]
        cur_choice = opts[0]

    # Libellé final non-éditable (valeur réelle envoyée en DB)
    st.session_state[key_lib_value] = cur_choice

    # Met à jour la ligne temporaire dans la session
    if index < len(st.session_state.revenus_forms):
        st.session_state.revenus_forms[index].update(
            {
                "date": cur_date,
                "categorie": cur_cat,
                "libelle": st.session_state[key_lib_value],
                "montant": cur_montant,
            }
        )

    # Force le re-render (copie superficielle)
    st.session_state.revenus_forms = st.session_state.revenus_forms[:]
