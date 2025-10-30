# kpflo_core/depenses.py
import pandas as pd
from datetime import date
from .storage_sqlite import insert_transaction, delete_transaction, fetch_all_df_with_id
from .categories import EXPENSE_CATEGORIES


def generate_libelle(categorie: str, input_date: date) -> str:
    month_name = input_date.strftime("%B")  # ex: October
    year = input_date.strftime("%Y")  # ex: 2025
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
    return (
        row["libelle"].strip() != ""
        and row["montant"] > 0
        and row["categorie"] in EXPENSE_CATEGORIES
    )


def save_depenses_edits(edited_rows: list[dict], user_id: int):
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
        return "#22c55e"  # green
    if pct < 20:
        return "#eab308"  # yellow
    if pct < 35:
        return "#f97316"  # orange
    return "#ef4444"  # red


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


import pandas as pd
import streamlit as st
from kpflo_core.categories import CATEGORY_BUDGET_RULES

# On suppose que suggestions_for_category(cat: str) existe déjà dans ce module.


# 🔹 Convertit une date en clé "YYYY-MM"
def _month_key(d):
    return pd.Timestamp(d).strftime("%Y-%m") if d else None


# 🔹 Convertit une date en libellé lisible "October 2025"
def _month_human(d):
    try:
        return pd.Timestamp(d).strftime("%B %Y")
    except Exception:
        return ""


# 🔹 Calcule les totaux de revenus par mois (dict "YYYY-MM" → somme)
@st.cache_data(show_spinner=False)
def build_month_revenue_totals(df_revenus: pd.DataFrame) -> dict:
    if df_revenus is None or df_revenus.empty:
        return {}
    tmp = df_revenus.copy()
    tmp["mk"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m")
    return tmp.groupby("mk")["montant"].sum().to_dict()


# 🔹 Calcule les totaux mensuels de la catégorie "Impôts & taxes" (dict)
@st.cache_data(show_spinner=False)
def build_month_impots_totals(df_depenses: pd.DataFrame) -> dict:
    if df_depenses is None or df_depenses.empty:
        return {}
    tmp = df_depenses[df_depenses["categorie"] == "Impôts & taxes"].copy()
    if tmp.empty:
        return {}
    tmp["mk"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m")
    return tmp.groupby("mk")["montant"].sum().to_dict()


# 🔹 Retourne le revenu NET du mois = revenus - "Impôts & taxes" du mois
def revenue_total_net_for_month(
    d, df_revenus: pd.DataFrame, df_depenses: pd.DataFrame
) -> float:
    mk = _month_key(d)
    if not mk:
        return 0.0
    rev_totals = build_month_revenue_totals(df_revenus)
    imp_totals = build_month_impots_totals(df_depenses)
    brut = float(rev_totals.get(mk, 0.0))
    imp = float(imp_totals.get(mk, 0.0))
    return max(brut - imp, 0.0)


# 🔹 Récupère (target, cap) pour une catégorie, avec fallback "__DEFAULT__"
def budget_rule_for_category(cat: str) -> tuple[float, float | None]:
    if not cat:
        rule = CATEGORY_BUDGET_RULES["__DEFAULT__"]
    else:
        rule = CATEGORY_BUDGET_RULES.get(cat, CATEGORY_BUDGET_RULES["__DEFAULT__"])
    return rule["target"], rule["cap"]


# 🔹 Renvoie la classe CSS (ok/warn/bad/na) selon le pourcentage et les seuils
def ratio_status_color(pct: float, target: float, cap: float | None) -> str:
    if target <= 0:
        return "ratio-na"
    if pct <= target + 1e-9:
        return "ratio-ok"
    if cap is None:
        return "ratio-warn"  # pas de rouge si pas de cap
    if pct <= cap + 1e-9:
        return "ratio-warn"
    return "ratio-bad"


# 🔹 Affiche la box de ratio "X%" + sous-texte "Seuil Y%" (tooltip cible/cap)
def render_ratio_box(
    date_, categorie, montant, df_revenus: pd.DataFrame, df_depenses: pd.DataFrame
):
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


# 🔹 Propose des libellés de dépense selon la catégorie et le mois (+ 'Autre')
def expense_suggestions_for_category(cat: str, d) -> list[str]:
    mois = _month_human(d)
    stems = suggestions_for_category(cat)  # ex. ["Eau", "Électricité", "Gaz"]
    stems = stems or [cat if cat else "Dépense"]
    options = [f"{s}_{mois}" if mois else s for s in stems]
    autre = f"Autre_{mois}" if mois else "Autre"
    if autre not in options:
        options.append(autre)
    return options


# 🔹 Callback : synchronise une ligne du formulaire Dépenses à chaque changement
def dep_update_form(index, key_date, key_cat, key_lib_choice, key_lib_value, key_amt):
    """Met à jour la ligne (date/catégorie/libellé/montant) dans st.session_state.depenses_forms."""
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
        st.session_state.depenses_forms[index]["date"] = cur_date
        st.session_state.depenses_forms[index]["categorie"] = cur_cat
        st.session_state.depenses_forms[index]["libelle"] = st.session_state[
            key_lib_value
        ]
        st.session_state.depenses_forms[index]["montant"] = cur_amt

    st.session_state.depenses_forms = st.session_state.depenses_forms  # rerender
