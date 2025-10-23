# kpflo_core/epargne.py
from .budget import compute_summary
from .storage_sqlite import fetch_all_df_with_id

def get_epargne_stats(user_id: int) -> dict:
    df = fetch_all_df_with_id(user_id)
    summary = compute_summary(df)
    epargne_categorie = "Épargne (virement)"
    epargne_montant = 0.0
    if not summary["by_cat"].empty and epargne_categorie in summary["by_cat"]["categorie"].values:
        epargne_montant = summary["by_cat"][summary["by_cat"]["categorie"] == epargne_categorie]["montant"].sum()
    return {
        "split_20": summary["split"]["20"],
        "epargne_montant": epargne_montant
    }