# kpflo_core/depenses.py
# ---------------------------------------------------------
# Logique des dépenses : libellés, ratios, CRUD, helpers UI
# ---------------------------------------------------------

from datetime import date
from typing import List

import pandas as pd
import streamlit as st

from .storage_sqlite import insert_transaction, delete_transaction, fetch_all_df_with_id
from .categories import (
    EXPENSE_CATEGORIES,
    CATEGORY_BUDGET_RULES,
    suggestions_for_category,
)

# ---------- Libellés / formatage ----------


def generate_libelle(categorie: str, input_date: date) -> str:
    """Formate 'Catégorie_Month Year' (fallback 'Autre' pour catégories libres)."""
    month_name = input_date.strftime("%B")
    year = input_date.strftime("%Y")
    base = "Autre" if categorie in ("Autre", "Autre dépense") else categorie
    return f"{base}_{month_name} {year}"


# ---------- Lecture / normalisation ----------


def get_depenses_df(user_id: int) -> pd.DataFrame:
    """Récupère toutes les dépenses (type OUT) et normalise date & montant(+)."""
    df = fetch_all_df_with_id(user_id)
    df_depenses = df[df["type"] == "OUT"].copy()
    df_depenses["date"] = pd.to_datetime(df_depenses["date"]).dt.date
    df_depenses["montant"] = df_depenses["montant"].abs()
    return df_depenses


# ---------- Validation & CRUD ----------


def validate_depense_row(row: dict) -> bool:
    """Valide libellé non vide, montant > 0 et catégorie connue."""
    return (
        bool(row.get("libelle", "").strip())
        and float(row.get("montant", 0)) > 0
        and row.get("categorie") in EXPENSE_CATEGORIES
    )


def save_depenses_edits(edited_rows: list[dict], user_id: int):
    """Insère en base toutes les lignes valides (montant stocké en négatif)."""
    for row in edited_rows:
        if not validate_depense_row(row):
            continue
        insert_transaction(
            row["date"],
            "OUT",
            row["categorie"],
            row["libelle"],
            -float(row["montant"]),
            recurrent=False,
            user_id=user_id,
        )


def delete_depense(tx_id: int, user_id: int):
    """Supprime une dépense par id de transaction (DELETE)."""
    delete_transaction(tx_id, user_id)


# ---------- Helpers UI / ratios ----------


def color_for_pct(pct: float) -> str:
    """Retourne une couleur hex selon le % du revenu utilisé."""
    if pct < 10:
        return "#22c55e"  # green
    if pct < 20:
        return "#eab308"  # yellow
    if pct < 35:
        return "#f97316"  # orange
    return "#ef4444"  # red


def default_depense_row(expense_categories: List[str], today):
    """Ligne par défaut : 1ère catégorie, 1er libellé suggéré, montant=0, date=today."""
    if not expense_categories:
        raise ValueError("Liste des catégories vide.")
    cat = expense_categories[0]
    suggs = suggestions_for_category(cat)
    libelle = suggs[0] if suggs else cat
    return {"date": today, "categorie": cat, "libelle": libelle, "montant": 0.0}


# ---------- Utils dates ----------


def _month_key(d):
    """Clé technique 'YYYY-MM' pour groupby mensuel (ou None)."""
    return pd.Timestamp(d).strftime("%Y-%m") if d else None


def _month_human(d):
    """Libellé lisible 'Month Year' (sinon '')."""
    try:
        return pd.Timestamp(d).strftime("%B %Y")
    except Exception:
        return ""


# ---------- Agrégats mis en cache ----------


@st.cache_data(show_spinner=False)
def build_month_revenue_totals(df_revenus: pd.DataFrame) -> dict:
    """Totaux revenus par mois → dict 'YYYY-MM' → somme."""
    if df_revenus is None or df_revenus.empty:
        return {}
    tmp = df_revenus.copy()
    tmp["mk"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m")
    return tmp.groupby("mk")["montant"].sum().to_dict()


@st.cache_data(show_spinner=False)
def build_month_impots_totals(df_depenses: pd.DataFrame) -> dict:
    """Totaux 'Impôts & taxes' par mois → dict 'YYYY-MM' → somme."""
    if df_depenses is None or df_depenses.empty:
        return {}
    tmp = df_depenses[df_depenses["categorie"] == "Impôts & taxes"].copy()
    if tmp.empty:
        return {}
    tmp["mk"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m")
    return tmp.groupby("mk")["montant"].sum().to_dict()


def revenue_total_net_for_month(
    d, df_revenus: pd.DataFrame, df_depenses: pd.DataFrame
) -> float:
    """Revenu NET du mois = revenus - impôts (>= 0)."""
    mk = _month_key(d)
    if not mk:
        return 0.0
    brut = float(build_month_revenue_totals(df_revenus).get(mk, 0.0))
    imp = float(build_month_impots_totals(df_depenses).get(mk, 0.0))
    return max(brut - imp, 0.0)


def budget_rule_for_category(cat: str) -> tuple[float, float | None]:
    """Retourne (target, cap) pour une catégorie (fallback __DEFAULT__)."""
    rule = CATEGORY_BUDGET_RULES.get(cat or "", CATEGORY_BUDGET_RULES["__DEFAULT__"])
    return rule["target"], rule["cap"]


def ratio_status_color(pct: float, target: float, cap: float | None) -> str:
    """Classe CSS selon seuils : ok / warn / bad / na."""
    if target <= 0:
        return "ratio-na"
    if pct <= target + 1e-9:
        return "ratio-ok"
    if cap is None or pct <= cap + 1e-9:
        return "ratio-warn"
    return "ratio-bad"


def render_ratio_box(
    date_, categorie, montant, df_revenus: pd.DataFrame, df_depenses: pd.DataFrame
):
    """Affiche la box ratio 'X%' avec sous-texte et tooltip (cible/cap)."""
    rev_net = revenue_total_net_for_month(date_, df_revenus, df_depenses)
    if rev_net <= 0:
        st.markdown(
            "<div class='ratio-box ratio-na'>n/a"
            "<span class='ratio-sub'>revenu net mensuel indisponible</span></div>",
            unsafe_allow_html=True,
        )
        return
    pct = (float(montant) / rev_net) * 100.0
    target, cap = budget_rule_for_category(categorie)
    status = ratio_status_color(pct, target, cap)
    ratio_txt = f"{pct:.0f}%"
    sub_txt = f"sur {_month_human(date_)} • Seuil {target:.0f}%"
    cap_txt = "—" if cap is None else f"{cap:.0f}%"
    title_attr = f"title='Cible {target:.0f}% • Cap {cap_txt}'"
    st.markdown(
        f"<div class='ratio-box {status}' {title_attr}>{ratio_txt}"
        f"<span class='ratio-sub'>{sub_txt}</span></div>",
        unsafe_allow_html=True,
    )


# ---------- Suggestions + callback UI ----------


def expense_suggestions_for_category(cat: str, d) -> list[str]:
    """Retourne stems dépense + suffixe '_Month Year' (+ 'Autre')."""
    mois = _month_human(d)
    stems = suggestions_for_category(cat) or [cat if cat else "Dépense"]
    options = [f"{s}_{mois}" if mois else s for s in stems]
    autre = f"Autre_{mois}" if mois else "Autre"
    if autre not in options:
        options.append(autre)
    return options


def dep_update_form(index, key_date, key_cat, key_lib_choice, key_lib_value, key_amt):
    """Callback Streamlit : resynchronise (date/cat/libellé/montant)."""
    cur_date = st.session_state.get(key_date)
    cur_cat = st.session_state.get(key_cat)
    cur_choice = st.session_state.get(key_lib_choice, "")
    cur_amt = st.session_state.get(key_amt, 0.0)

    opts = expense_suggestions_for_category(cur_cat, cur_date)
    if cur_choice not in opts and opts:
        st.session_state[key_lib_choice] = opts[0]
        cur_choice = opts[0]

    st.session_state[key_lib_value] = cur_choice

    if index < len(st.session_state.depenses_forms):
        st.session_state.depenses_forms[index].update(
            {
                "date": cur_date,
                "categorie": cur_cat,
                "libelle": cur_choice,
                "montant": cur_amt,
            }
        )

    # Force le re-render (copie superficielle)
    st.session_state.depenses_forms = st.session_state.depenses_forms[:]
