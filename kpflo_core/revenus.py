# kpflo_core/revenus.py
import pandas as pd
from datetime import date
from typing import List, Dict
from .storage_sqlite import insert_transaction, delete_transaction, fetch_all_df_with_id, update_transaction
from .categories import INCOME_CATEGORIES

# ============== Libellés (stems) pour le sélecteur Revenus ==============
INCOME_LABEL_SUGGESTIONS: Dict[str, List[str]] = {
    # 1. Revenus professionnels
    "Revenus professionnels": [
        "Salaire principal",
        "Heures supplémentaires",
        "Indemnités (maladie, chômage, congés)",
        "Job secondaire / freelance",
        "Factures / prestations professionnelles",
        "Prime / bonus",
        "Autres revenus professionnels",
    ],

    # 2. Revenus financiers
    "Revenus financiers": [
        "Intérêts bancaires",
        "Dividendes",
        "Revenus locatifs",
        "Plus-values boursières",
        "Plus-values cryptomonnaies",
        "Plus-values vente d’actifs (voiture, matériel, etc.)",
        "Autres revenus financiers",
    ],

    # 3. Revenus sociaux & aides
    "Revenus sociaux & aides": [
        "Pension alimentaire (reçue)",
        "CAF",
        "Allocations familiales",
        "RSA / Aide sociale",
        "Retraite / pension",
        "Indemnités chômage",
        "Autres aides sociales",
    ],

    # 4. Revenus exceptionnels
    "Revenus exceptionnels": [
        "Héritage",
        "Donation",
        "Remboursement d’impôts",
        "Remboursement d’assurance",
        "Gains concours / jeux",
        "Vente ponctuelle (meubles, objets, etc.)",
        "Autre revenu exceptionnel",
    ],
}

def suggestions_for_income_category(category: str) -> List[str]:
    """Retourne la liste de 'stems' (libellés racine) pour la catégorie de revenus."""
    return INCOME_LABEL_SUGGESTIONS.get(category, [category] if category else [])

# (Optionnel) fournir une ligne par défaut si ton bouton "Nouveau revenu" en a besoin
def default_revenu_row(categories: List[str], d: date) -> dict:
    cat = categories[0] if categories else "Revenus professionnels"
    stem = (INCOME_LABEL_SUGGESTIONS.get(cat) or [cat])[0]
    # On laisse la SECTION suffixer avec _<Month Year> pour cohérence UI
    return {"date": d, "categorie": cat, "libelle": stem, "montant": 0.0}

# ================== Fonctions existantes (conservées) ===================
def generate_libelle(categorie: str, input_date: date) -> str:
    # Uniformise: <stem ou catégorie>_<Month Year>
    month_name = input_date.strftime("%B")
    year = input_date.strftime("%Y")
    base = categorie if categorie else "Revenu"
    return f"{base}_{month_name} {year}"

def get_revenus_df(user_id: int) -> pd.DataFrame:
    df = fetch_all_df_with_id(user_id)
    df_revenus = df[df["type"] == "IN"].copy()
    df_revenus["date"] = pd.to_datetime(df_revenus["date"]).dt.date
    df_revenus["montant"] = df_revenus["montant"].abs()
    return df_revenus

def get_revenus_summary(user_id: int) -> float:
    df_revenus = get_revenus_df(user_id)
    return df_revenus["montant"].sum() if not df_revenus.empty else 0.0

def get_revenus_preview_summary(user_id: int, temp_forms: list[dict]) -> float:
    total_enregistre = get_revenus_summary(user_id)
    total_temp = sum(form.get("montant", 0.0) for form in temp_forms)
    return total_enregistre + total_temp

def validate_revenu_row(row: dict) -> bool:
    return (row["libelle"].strip() != "" and row["montant"] > 0 and row["categorie"] in INCOME_CATEGORIES)

def save_revenus_edits(edited_rows: list[dict], user_id: int):
    for row in edited_rows:
        if not validate_revenu_row(row):
            continue
        insert_transaction(
            row["date"], "IN", row["categorie"], row["libelle"],
            float(row["montant"]), recurrent=False, user_id=user_id
        )

def delete_revenu(tx_id: int, user_id: int):
    delete_transaction(tx_id, user_id)

def update_revenu(tx_id: int, row: dict, user_id: int):
    """Met à jour un revenu existant via update_transaction."""
    if not validate_revenu_row(row):
        raise ValueError("Données invalides pour mise à jour.")
    update_transaction(
        tx_id, row["date"], "IN", row["categorie"], row["libelle"],
        float(row["montant"]), recurrent=False, user_id=user_id
    )
