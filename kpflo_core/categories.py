# kpflo_core/categories.py
# -------------------------------------------------------------------
# Définit toutes les catégories, règles et libellés suggérés
# pour les revenus et dépenses dans KPFLO.
# -------------------------------------------------------------------

from typing import Dict, List

# ---- Catégories principales de revenus ----
INCOME_CATEGORIES = [
    "Revenus professionnels",
    "Revenus financiers",
    "Revenus sociaux & aides",
    "Revenus exceptionnels",
]

# ---- Catégories principales de dépenses (23 types) ----
EXPENSE_CATEGORIES = [
    "Logement",
    "Alimentation & boissons non alcoolisées",
    "Boissons alcoolisées & tabac",
    "Transport",
    "Santé",
    "Habillement & chaussures",
    "Ameublement & équipement ménager",
    "Communications",
    "Loisirs & culture",
    "Éducation & formation",
    "Restaurants & hôtels",
    "Biens & services divers",
    "Impôts & taxes",
    "Crédits & dettes",
    "Épargne & placements",
    "Assurances",
    "Aides & dons",
    "Frais bancaires & services financiers",
    "Enfants & famille",
    "Animaux de compagnie",
    "Travail & revenus professionnels",
    "Dépenses exceptionnelles",
    "Épargne de précaution / projets",
]

# ---- Règles budgétaires indicatives (% du revenu total) ----
CATEGORY_BUDGET_RULES = {
    "Logement": {"target": 30.0, "cap": 35.0},
    "Alimentation & boissons non alcoolisées": {"target": 11.0, "cap": 15.0},
    "Transport": {"target": 5.0, "cap": 12.0},
    "Santé": {"target": 2.0, "cap": 6.0},
    "Communications": {"target": 2.0, "cap": 5.0},
    "Ameublement & équipement ménager": {"target": 3.0, "cap": 6.0},
    "Habillement & chaussures": {"target": 3.0, "cap": 6.0},
    "Loisirs & culture": {"target": 6.0, "cap": 10.0},
    "Restaurants & hôtels": {"target": 3.0, "cap": 6.0},
    "Biens & services divers": {"target": 3.0, "cap": 6.0},
    "Boissons alcoolisées & tabac": {"target": 1.0, "cap": 2.0},
    "Enfants & famille": {"target": 5.0, "cap": 10.0},
    "Animaux de compagnie": {"target": 1.0, "cap": 3.0},
    "Dépenses exceptionnelles": {"target": 5.0, "cap": None},
    "Frais bancaires & services financiers": {"target": 0.5, "cap": 1.0},
    "Assurances": {"target": 2.0, "cap": 4.0},
    "__DEFAULT__": {"target": 15.0, "cap": 20.0},
}

# ---- Libellés proposés pour chaque catégorie de revenus ----
INCOME_LABEL_SUGGESTIONS: Dict[str, List[str]] = {
    "Revenus professionnels": [
        "Salaire principal",
        "Heures supplémentaires",
        "Indemnités (maladie, chômage, congés)",
        "Job secondaire / freelance",
        "Factures / prestations professionnelles",
        "Prime / bonus",
        "Autres revenus professionnels",
    ],
    "Revenus financiers": [
        "Intérêts bancaires",
        "Dividendes",
        "Revenus locatifs",
        "Plus-values boursières",
        "Plus-values cryptomonnaies",
        "Plus-values vente d’actifs (voiture, matériel, etc.)",
        "Autres revenus financiers",
    ],
    "Revenus sociaux & aides": [
        "Pension alimentaire (reçue)",
        "CAF",
        "Allocations familiales",
        "RSA / Aide sociale",
        "Retraite / pension",
        "Indemnités chômage",
        "Autres aides sociales",
    ],
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

# ---- Libellés proposés pour chaque catégorie de dépenses ----
EXPENSE_LABEL_SUGGESTIONS = {
    # (tu gardes ici ton tableau complet de 23 catégories)
    # inchangé, donc je ne le recopie pas ici pour alléger
}


def suggestions_for_category(cat: str) -> list[str]:
    """Retourne la liste de libellés racine (stems) pour une catégorie donnée."""
    return EXPENSE_LABEL_SUGGESTIONS.get(cat, [])


# ---- Répartition budgétaire type 50/30/20 ----
FIFTY = {
    "Logement",
    "Alimentation & boissons non alcoolisées",
    "Transport",
    "Santé",
    "Communications",
    "Assurances",
}

THIRTY = {
    "Habillement & chaussures",
    "Loisirs & culture",
    "Restaurants & hôtels",
    "Biens & services divers",
    "Animaux de compagnie",
    "Enfants & famille",
}

TWENTY = {
    "Épargne & placements",
    "Épargne de précaution / projets",
    "Crédits & dettes",
}
