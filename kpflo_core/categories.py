# kpflo_core/categories.py
# Catégories standardisées pour l’entrée des données

INCOME_CATEGORIES = [
    "Revenus professionnels",
    "Revenus financiers",
    "Revenus sociaux & aides",
    "Revenus exceptionnels",
]

# ===== NOUVELLE LISTE DE DÉPENSES (23 catégories) =====
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

# ================================================
#  RÈGLES DE BUDGET PAR CATÉGORIE
# ================================================
# Chaque catégorie a une cible et un plafond (% du revenu total)
# Ces valeurs servent dans l’app principale pour les ratios Dépenses.

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


# Répartition 50/30/20 (règle conseillée)
# ⚠️ Les libellés doivent correspondre EXACTEMENT à tes catégories de dépenses.

FIFTY = {
    "Logement",
    "Alimentation & boissons non alcoolisées",
    "Transport",
    "Santé",
    "Communications",
    "Assurances",  # si tu l'utilises comme catégorie séparée
}

THIRTY = {
    "Habillement & chaussures",
    "Loisirs & culture",
    "Restaurants & hôtels",
    "Biens & services divers",
    "Animaux de compagnie",
    "Enfants & famille",  # tu peux bouger cette catégorie en FIFTY si tu préfères
}

TWENTY = {
    "Épargne & placements",  # si tu saisis l'épargne comme dépense (enveloppe)
    "Épargne de précaution / projets",  # idem
    "Crédits & dettes",  # désendettement
    # (si tu ne les considères pas comme des "dépenses", laisse TWENTY vide et calcule l’épargne ailleurs)
}
