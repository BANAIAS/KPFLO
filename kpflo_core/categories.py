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
