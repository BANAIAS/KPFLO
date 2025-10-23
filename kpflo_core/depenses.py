# kpflo_core/depenses.py
import pandas as pd
from datetime import date
from .storage_sqlite import insert_transaction, delete_transaction, fetch_all_df_with_id
from .categories import EXPENSE_CATEGORIES

def generate_libelle(categorie: str, input_date: date) -> str:
    month_name = input_date.strftime("%B")   # ex: October
    year = input_date.strftime("%Y")         # ex: 2025
    # "Autre dépense" doit produire "Autre_<Month> <Year>"
    base = "Autre" if categorie in ("Autre", "Autre dépense") else categorie
    return f"{base}_{month_name} {year}"

def get_depenses_df(user_id: int) -> pd.DataFrame:
    df = fetch_all_df_with_id(user_id)
    df_depenses = df[df["type"] == "OUT"].copy()
    df_depenses["date"] = pd.to_datetime(df_depenses["date"]).dt.date
    df_depenses["montant"] = df_depenses["montant"].abs()
    return df_depenses

def validate_depense_row(row: dict) -> bool:
    return (row["libelle"].strip() != "" and row["montant"] > 0 and row["categorie"] in EXPENSE_CATEGORIES)

def save_depenses_edits(edited_rows: list[dict], user_id: int):
    for row in edited_rows:
        if not validate_depense_row(row):
            continue
        insert_transaction(
            row["date"], "OUT", row["categorie"], row["libelle"],
            -float(row["montant"]), recurrent=False, user_id=user_id
        )

def delete_depense(tx_id: int, user_id: int):
    delete_transaction(tx_id, user_id)


# === Helpers UI / règles Dépenses ===

# ===== NOUVELLE TABLE DE LIBELLÉS (stems / racines, SANS suffixe mois) =====
EXPENSE_LABEL_SUGGESTIONS = {
    # 1. Logement
    "Logement": [
        "Loyer / crédit immobilier",
        "Eau",
        "Électricité",
        "Gaz & combustibles",
        "Assurance habitation",
        "Entretien maison / jardin",
        "Taxe foncière",
        "Copropriété / syndic",
        "Autres frais logement",
    ],

    # 2. Alimentation & boissons non alcoolisées
    "Alimentation & boissons non alcoolisées": [
        "Courses (supermarché, épicerie)",
        "Boulangerie / pâtisserie",
        "Boucherie / charcuterie / poissonnerie",
        "Fruits & légumes",
        "Plats préparés",
        "Boissons sans alcool",
        "Produits bio / spécialisés",
        "Autres dépenses alimentaires",
    ],

    # 3. Boissons alcoolisées & tabac
    "Boissons alcoolisées & tabac": [
        "Vin",
        "Bière",
        "Spiritueux",
        "Cigarettes / tabac",
        "Vapoteuse / e-liquides",
    ],

    # 4. Transport
    "Transport": [
        "Essence / carburant",
        "Entretien / réparations",
        "Assurance auto / moto",
        "Transports en commun",
        "Péages / parking",
        "Location de véhicule",
        "Vélo / trottinette",
        "Abonnement mobilité (Navigo, etc.)",
    ],

    # 5. Santé
    "Santé": [
        "Médecin / spécialiste",
        "Pharmacie / parapharmacie",
        "Lunettes / lentilles",
        "Soins dentaires",
        "Hôpital / clinique",
        "Mutuelle santé",
        "Examens / analyses",
        "Autres dépenses santé",
    ],

    # 6. Habillement & chaussures
    "Habillement & chaussures": [
        "Vêtements",
        "Chaussures",
        "Accessoires / bijoux",
        "Pressing / couture",
        "Autres habillement",
    ],

    # 7. Ameublement & équipement ménager
    "Ameublement & équipement ménager": [
        "Meubles",
        "Électroménager",
        "Décoration",
        "Bricolage / outillage",
        "Jardinage",
        "Produits ménagers",
        "Entretien courant du logement",
    ],

    # 8. Communications
    "Communications": [
        "Forfait mobile",
        "Internet / fibre",
        "Télévision / streaming",
        "Abonnements numériques (Netflix, Spotify, etc.)",
        "Téléphonie fixe",
    ],

    # 9. Loisirs & culture
    "Loisirs & culture": [
        "Cinéma / spectacles",
        "Livres / magazines",
        "Jeux vidéo",
        "Sport / salle de sport",
        "Instruments de musique / matériel créatif",
        "Voyages / vacances",
        "Sorties diverses / loisirs",
        "Streaming audio / vidéo",
    ],

    # 10. Éducation & formation
    "Éducation & formation": [
        "École / scolarité",
        "Cantine scolaire",
        "Fournitures / matériel scolaire",
        "Cours particuliers / soutien",
        "Formation professionnelle / MOOC",
        "Études supérieures / université",
    ],

    # 11. Restaurants & hôtels
    "Restaurants & hôtels": [
        "Restaurants",
        "Fast-food",
        "Cafés / bars",
        "Livraison de repas",
        "Hôtels / hébergements / Airbnb",
    ],

    # 12. Biens & services divers
    "Biens & services divers": [
        "Coiffeur / esthétique",
        "Produits de beauté / soins",
        "Services administratifs",
        "Abonnements divers",
        "Nettoyage / pressing",
        "Autres services personnels",
    ],

    # 13. Impôts & taxes
    "Impôts & taxes": [
        "Impôt sur le revenu",
        "CSG / CRDS",
        "Taxe d’habitation",
        "Autres contributions fiscales",
        "Amendes / contraventions",
    ],

    # 14. Crédits & dettes
    "Crédits & dettes": [
        "Crédit consommation",
        "Crédit auto",
        "Carte de crédit / revolving",
        "Remboursement de dettes privées",
        "Intérêts bancaires",
    ],

    # 15. Épargne & placements
    "Épargne & placements": [
        "Livret A / LDDS",
        "Assurance-vie",
        "PEL / CEL",
        "Bourse / actions / ETF",
        "Cryptomonnaies",
        "Autres placements financiers",
    ],

    # 16. Assurances
    "Assurances": [
        "Assurance auto",
        "Assurance habitation",
        "Assurance santé complémentaire",
        "Assurance vie",
        "Assurance téléphone / multimédia",
        "Autres assurances",
    ],

    # 17. Aides & dons
    "Aides & dons": [
        "Aide à un proche / famille",
        "Dons associations / œuvres",
        "Pension alimentaire versée",
        "Cotisations caritatives",
    ],

    # 18. Frais bancaires & services financiers
    "Frais bancaires & services financiers": [
        "Frais de tenue de compte",
        "Commissions bancaires",
        "Frais de carte / retraits",
        "Frais PayPal / virement",
        "Autres frais financiers",
    ],

    # 19. Enfants & famille
    "Enfants & famille": [
        "Crèche / garde d’enfants",
        "Nounou / baby-sitter",
        "Activités enfants / loisirs",
        "Vêtements enfants",
        "Santé enfants",
        "Éducation enfants",
        "Cantine scolaire enfants",
    ],

    # 20. Animaux de compagnie
    "Animaux de compagnie": [
        "Nourriture",
        "Vétérinaire",
        "Toilettage",
        "Accessoires / jouets",
        "Assurance animale",
    ],

    # 21. Travail & revenus professionnels
    "Travail & revenus professionnels": [
        "Déplacements pro",
        "Repas / restauration pro",
        "Matériel professionnel",
        "Cotisations syndicales ou pro",
        "Formation / certification",
        "Autres dépenses liées au travail",
    ],

    # 22. Dépenses exceptionnelles
    "Dépenses exceptionnelles": [
        "Cadeaux",
        "Mariage / anniversaire / fêtes",
        "Funérailles",
        "Gros achats imprévus",
        "Déménagement",
        "Urgences diverses",
    ],

    # 23. Épargne de précaution / projets
    "Épargne de précaution / projets": [
        "Projet vacances",
        "Projet voiture",
        "Projet rénovation",
        "Projet mariage",
        "Épargne sécurité / imprévus",
    ],
}

def suggestions_for_category(cat: str) -> list[str]:
    """Retourne la liste de suggestions de libellés pour une catégorie (peut être vide)."""
    return EXPENSE_LABEL_SUGGESTIONS.get(cat, [])


def color_for_pct(pct: float) -> str:
    """
    Couleur selon % du revenu utilisé par la dépense.
    <10% vert, <20% jaune, <35% orange, sinon rouge.
    """
    if pct < 10:
        return "#22c55e"   # green
    if pct < 20:
        return "#eab308"   # yellow
    if pct < 35:
        return "#f97316"   # orange
    return "#ef4444"       # red

def default_depense_row(expense_categories: list[str], today):
    """
    Ligne par défaut pour l'UI Dépenses.
    - Catégorie = premier élément de la liste
    - Libellé = 1ère suggestion si dispo, sinon nom de catégorie
    - Montant = 0.0
    - Date = today
    """
    if not expense_categories:
        raise ValueError("Liste des catégories vide.")
    cat = expense_categories[0]
    suggs = suggestions_for_category(cat)
    libelle = suggs[0] if suggs else cat
    return {
        "date": today,
        "categorie": cat,
        "libelle": libelle,
        "montant": 0.0,
    }
