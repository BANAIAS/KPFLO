# ================================================
#  Streamlit App – Tableau de bord financier KPFLO
#  Interface principale : revenus, dépenses, budget
#  Gère l’affichage, les interactions et la logique
#  s’appuie sur les modules : revenus.py, depenses.py,
#  budget.py, categories.py et storage_sqlite.py
# ================================================

# --- Standard library ---
import os
import io
import hashlib
from datetime import date, datetime
from calendar import monthrange
from pathlib import Path

# --- Third-party libraries ---
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from dateutil.relativedelta import relativedelta


# --- Internal modules (notre code KPFLO) ---
from kpflo_core import budget  # module complet si on l'utilise tel quel

from kpflo_core.budget import (
    compute_summary,
    predict_end_of_month,
    predict_end_of_year,
    build_coach_text,
    savings_projection,
    slice_df_for_month,
    slice_df_for_year,
    compute_totals,
    build_year_timeseries,
    build_coach_text_year,
)

from kpflo_core.categories import (
    INCOME_CATEGORIES,
    EXPENSE_CATEGORIES,
    CATEGORY_BUDGET_RULES,
)

from kpflo_core.revenus import (
    get_revenus_df,
    delete_revenu,
    get_revenus_preview_summary,
    update_revenu,
    suggestions_for_income_category,  # stems revenus (_<Month Year>)
    default_revenu_row,  # ligne par défaut revenus (sans suffixe)
    _month_key,
    _month_human,
    income_suggestions_for_category,
    rev_update_form,
)

from kpflo_core.depenses import (
    get_depenses_df,
    save_depenses_edits,
    delete_depense,
    suggestions_for_category,  # stems dépenses (_<Month Year>)
    default_depense_row,  # ligne par défaut dépenses (sans suffixe)
    _month_key as dep_month_key,
    _month_human as dep_month_human,
    build_month_revenue_totals,
    build_month_impots_totals,
    revenue_total_net_for_month,
    budget_rule_for_category,
    ratio_status_color,
    render_ratio_box,
    expense_suggestions_for_category,
    dep_update_form,
)


from kpflo_core.storage_sqlite import (
    init_db,
    list_users,
    create_user,
    validate_user,
    ensure_default_user,
    fetch_all_df,
    fetch_all_df_with_id,
    insert_transaction,
    _connect,
    bulk_insert_transactions,
    build_export_or_template_csv_sqlite,
    # si tu en as besoin côté app :
    _users_map_sqlite,
    month_iter,
)

# ================================================
#  DÉFINITION DES FONCTIONS PRINCIPALES
#  Fonctions utilisées dans tout le tableau de bord
# ================================================


# --- Fonction : bulk_insert_transactions() ---
# Cette fonction permet d’insérer plusieurs transactions d’un seul coup dans la base SQLite.
# Chaque élément de la liste passée en argument correspond à une transaction complète :
# (date, type, catégorie, libellé, montant, récurrence, date de création, identifiant utilisateur)


# --- Fonction : load_user_df() ---
# Charge toutes les transactions d’un utilisateur spécifique depuis la base SQLite.
# Les données sont ensuite normalisées pour être prêtes à l’affichage et à l’analyse.
# Grâce au décorateur @st.cache_data, le résultat est mis en cache pour éviter
# de recharger inutilement les mêmes données à chaque interaction.


@st.cache_data(show_spinner=False)
def load_user_df(user_id: int):
    df = fetch_all_df_with_id(user_id)
    return normalize_df(df)


# --- Fonction : normalize_df() ---
# Harmonise et nettoie le DataFrame avant l’analyse.
# Elle renomme les colonnes selon la convention française (type, montant, catégorie),
# convertit les dates au bon format, et standardise les montants :
# revenus positifs (IN) et dépenses négatives (OUT).
# Retourne un DataFrame minimal prêt pour le tableau de bord.
def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Harmoniser les colonnes
    if "type" not in df.columns and "kind" in df.columns:
        df["type"] = np.where(df["kind"].str.lower() == "revenu", "IN", "OUT")
    if "montant" not in df.columns and "amount" in df.columns:
        df["montant"] = df["amount"].astype(float)
    if "categorie" not in df.columns and "category" in df.columns:
        df["categorie"] = df["category"]

    # Dates
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    # Standardiser les signes : OUT -> négatif ; IN -> positif
    # (si ton CSV OUT est déjà négatif, ça ne changera rien ; s'il est positif, on le rend négatif)
    df.loc[df["type"] == "OUT", "montant"] = -df.loc[
        df["type"] == "OUT", "montant"
    ].abs()
    df.loc[df["type"] == "IN", "montant"] = df.loc[df["type"] == "IN", "montant"].abs()

    # Colonnes utiles minimales
    return df[["date", "type", "categorie", "montant"]]


#  ---------- Helpers (import/export) ----------

# - month_iter : génère une liste de mois glissants


# - _users_map_sqlite : mappe les IDs utilisateurs vers leurs noms


# - build_export_or_template_csv_sqlite : exporte la base ou crée un modèle CSV


# - import_unified_csv_to_sqlite : importe un CSV unifié (chunks), option replace_all, insertion batch, refresh cache


def import_unified_csv_to_sqlite(
    file,
    user_id: int,
    replace_all: bool = False,
    chunksize: int = 5000,
):
    """
    Importe un CSV unifié (colonnes: user_id,date,kind,category,label,amount)
    puis recharge la session avec les nouvelles données.

    - Lecture par chunks pour limiter la RAM
    - Insertion bulk pour aller vite
    - Option replace_all: purge les anciennes lignes des user_id présents dans le CSV
    """
    import pandas as pd
    from datetime import datetime
    import io

    # 0. Lecture robuste du fichier Streamlit
    #    -> file est un UploadedFile Streamlit, qu'on doit cloner en BytesIO
    if hasattr(file, "getvalue"):
        raw = file.getvalue()
        file_buf = io.BytesIO(raw)
    else:
        # si déjà un buffer ou un fichier-like
        file_buf = file

    # 1. Si replace_all=True, on purge les transactions EXISTANTES
    #    pour tous les user_id présents dans le CSV AVANT réinsertion.
    if replace_all:
        # On lit juste la colonne user_id par chunks, pour ne pas tout charger
        file_buf.seek(0)
        user_ids = set()
        for chunk in pd.read_csv(file_buf, usecols=["user_id"], chunksize=chunksize):
            user_ids.update(chunk["user_id"].astype(int).unique())

        if user_ids:
            with _connect() as con:
                cur = con.cursor()
                q = "DELETE FROM transactions WHERE user_id IN ({})".format(
                    ",".join("?" for _ in user_ids)
                )
                cur.execute(q, list(user_ids))
                con.commit()

        # très important : on remet le curseur au début pour la vraie importation
        file_buf.seek(0)

    # 2. Parcours du CSV par morceaux pour insertion
    imported_months = set()

    for chunk in pd.read_csv(file_buf, chunksize=chunksize):
        # Colonnes attendues
        expected_cols = ["user_id", "date", "kind", "category", "label", "amount"]
        for col in expected_cols:
            if col not in chunk.columns:
                chunk[col] = None

        # Normalisation
        chunk["user_id"] = chunk["user_id"].astype(int, errors="ignore")
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce").dt.date
        chunk["kind"] = chunk["kind"].astype(str).str.lower().str.strip()
        chunk["amount"] = pd.to_numeric(chunk["amount"], errors="coerce").fillna(0.0)

        rows_to_insert = []
        now_iso = datetime.utcnow().isoformat()

        for _, r in chunk.iterrows():
            # On skippe les lignes pourries
            if (
                pd.isna(r["date"])
                or r["amount"] == 0.0
                or r["kind"] not in ("revenu", "depense", "epargne")
            ):
                continue

            kind = r["kind"]
            user_id_row = int(r["user_id"]) if pd.notna(r["user_id"]) else user_id

            date_val = r["date"]  # déjà un datetime.date
            montant_val = float(r["amount"])

            categorie = (
                r["category"]
                if pd.notna(r["category"])
                else ("Épargne" if kind == "epargne" else "")
            )

            libelle = (
                r["label"]
                if pd.notna(r["label"])
                else ("Épargne" if kind == "epargne" else "")
            )

            trx_type = "IN" if kind == "revenu" else "OUT"

            # >>>>>>>>>> CHANGEMENT ICI : on inclut created_at
            rows_to_insert.append(
                (
                    str(date_val),  # date (en string YYYY-MM-DD)
                    trx_type,  # type ('IN'/'OUT')
                    str(categorie),
                    str(libelle),
                    montant_val,
                    0,  # recurrent -> 0 par défaut
                    now_iso,  # created_at -> timestamp ISO
                    user_id_row,  # user_id
                )
            )

            # Pour marquer les mois où il y a de l'épargne (optionnel)
            if kind == "epargne":
                d = pd.to_datetime(date_val, errors="coerce")
                if pd.notnull(d):
                    imported_months.add(d.strftime("%Y-%m"))

        # Insertion batch du chunk courant
        bulk_insert_transactions(rows_to_insert)

    # 3. Invalidation du cache / refresh data en mémoire
    st.session_state["last_imported_user_id"] = user_id
    st.session_state["_force_reload"] = True

    if imported_months:
        st.session_state["_force_epargne_resync"] = True
        st.session_state["_force_epargne_months"] = list(imported_months)
        st.session_state["_open_tab"] = "epargne"

    return True


#  CACHING DES DONNÉES (Revenus / Dépenses)

# - cached_revenus_df : charge les revenus d’un utilisateur avec mise en cache
# - cached_depenses_df : charge les dépenses d’un utilisateur avec mise en cache


@st.cache_data(show_spinner=False)
def cached_revenus_df(user_id: int):
    """Version mise en cache des revenus de l'utilisateur."""
    return get_revenus_df(user_id)


@st.cache_data(show_spinner=False)
def cached_depenses_df(user_id: int):
    """Version mise en cache des dépenses de l'utilisateur."""
    return get_depenses_df(user_id)


# ================================================
#  CONFIGURATION INITIALE DE L'APPLICATION
# ================================================


# ---------- Config ----------
# Paramètres de la page Streamlit (titre, icône, mise en page)
st.set_page_config(page_title="KPFLO 🤑", page_icon="💧", layout="wide")

# Initialisation de la base SQLite
init_db()

# ---------- State ----------
# Définition des variables globales conservées dans st.session_state
# pour gérer la connexion utilisateur et les formulaires dynamiques
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user_id = None

#  Initialiser les formulaires Revenus et Dépenses
if "revenus_forms" not in st.session_state:
    st.session_state.revenus_forms = []

if "depenses_forms" not in st.session_state or st.session_state.depenses_forms is None:
    st.session_state.depenses_forms = []

# ================================================
#  HEADER DE L’APPLICATION
# ================================================

# Header de l'application :
# - colonne gauche : titre et sous-titre
# - colonne droite : gestion de l'utilisateur courant (sélection du profil)
#   et affichage avatar (photo ou initiales)
# - garantit qu'il existe toujours un utilisateur actif dans la session

left, right = st.columns([0.55, 0.45], vertical_alignment="center")
with left:
    st.title("KPFLO 🤑")
    st.caption("Gère ton argent avec style !")

users_df = list_users()
if users_df.empty:
    ensure_default_user()
    users_df = list_users()

default_user_id = ensure_default_user()
if st.session_state.current_user_id is None:
    st.session_state.current_user_id = default_user_id

with right:
    # ========= Helpers avatar (idempotents) =========

    try:
        from PIL import Image  # Assure-toi d'avoir Pillow installé
    except Exception:
        Image = None  # On tombera en mode "initiales" si Pillow n'est pas dispo

    AVATAR_DIR = Path("data/avatars")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    def _avatar_path(user_id: int) -> Path:
        return AVATAR_DIR / f"{int(user_id)}.png"

    def save_avatar(user_id: int, uploaded_file) -> None:
        """Enregistre un avatar carré PNG depuis un upload Streamlit."""
        if uploaded_file is None or Image is None:
            return
        img = Image.open(uploaded_file).convert("RGB")
        s = min(img.width, img.height)
        left = (img.width - s) // 2
        top = (img.height - s) // 2
        img_sq = img.crop((left, top, left + s, top + s)).resize((256, 256))
        img_sq.save(_avatar_path(user_id), "PNG", optimize=True)

    def avatar_src(user_id: int) -> str | None:
        p = _avatar_path(user_id)
        return str(p) if p.exists() else None

    # ========= Un peu de style pour les avatars =========
    st.markdown(
        """
    <style>
    .kpflo-avatar   { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; }
    .kpflo-avatarLG { width: 64px; height: 64px; border-radius: 50%; object-fit: cover; }
    .kpflo-initial  { width: 40px; height: 40px; border-radius: 50%; background:#eee;
                      display:flex; align-items:center; justify-content:center; font-weight:600; }
    .kpflo-initialLG{ width: 64px; height: 64px; border-radius: 50%; background:#eee;
                      display:flex; align-items:center; justify-content:center; font-weight:700; font-size:1.1rem; }
    .kpflo-row { display:flex; align-items:center; gap:.6rem; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    # Zone utilisateur (header droite) :
    # - récupère l'utilisateur courant depuis la session
    # - affiche l'avatar ou les initiales
    # - popover ▼ : voir le profil actif, basculer vers un autre profil
    # - popover 🔑 : se connecter ou créer un nouvel utilisateur + uploader une photo

    # ========= Récup utilisateur courant =========
    cur_row = users_df[users_df["id"] == st.session_state.current_user_id].iloc[0]
    cur_fullname = (
        f'{cur_row.get("prenom","")} {cur_row["nom"]}'.strip() or f'ID {cur_row["id"]}'
    )
    cur_avatar = avatar_src(int(cur_row["id"]))

    # ========= Bandeau avatar (remplace "✏️ Modifier utilisateur") =========
    # On affiche l'avatar (ou initiales) + un popover "▼" qui contient le switch
    cols_hdr = st.columns([0.2, 0.15, 0.65], vertical_alignment="center")
    with cols_hdr[0]:
        if cur_avatar:
            st.image(cur_avatar, caption=None, width=40)
        else:
            st.markdown(
                f"<div class='kpflo-initial'>{(cur_fullname[:1] or '🙂')}</div>",
                unsafe_allow_html=True,
            )
    with cols_hdr[1]:
        with st.popover("▼"):
            st.markdown("**Profil courant**")
            c1, c2 = st.columns([0.3, 0.7], vertical_alignment="center")
            with c1:
                if cur_avatar:
                    st.image(cur_avatar, caption=None, width=64)
                else:
                    st.markdown(
                        f"<div class='kpflo-initialLG'>{(cur_fullname[:1] or '🙂')}</div>",
                        unsafe_allow_html=True,
                    )
            with c2:
                st.markdown(
                    f"**{cur_fullname}**<br/><span style='opacity:.7;'>id:{cur_row['id']}</span>",
                    unsafe_allow_html=True,
                )

            st.divider()
            st.markdown("**Autres profils**")
            for _, row in users_df.iterrows():
                rid = int(row["id"])
                if rid == int(cur_row["id"]):
                    continue
                name = f'{row.get("prenom","")} {row["nom"]}'.strip() or f"ID {rid}"
                ava = avatar_src(rid)

                r1, r2, r3 = st.columns([0.25, 0.55, 0.20], vertical_alignment="center")
                with r1:
                    if ava:
                        st.image(ava, caption=None, width=40)
                    else:
                        st.markdown(
                            f"<div class='kpflo-initial'>{(name[:1] or '🙂')}</div>",
                            unsafe_allow_html=True,
                        )
                with r2:
                    st.write(name)
                with r3:
                    if st.button("Basculer", key=f"switch_{rid}"):
                        st.session_state.current_user_id = rid
                        st.rerun()

            st.caption("Crée plusieurs profils (vert/rouge/limite) puis bascule ici.")

    # ========= Popover "🔑 Se connecter / Créer" (conservé) + Upload photo =========
    with st.popover("🔑 Se connecter / Créer"):
        if st.session_state.logged_in:
            if st.button("Déconnexion"):
                st.session_state.logged_in = False
                st.session_state.current_user_id = default_user_id
                st.success("Déconnecté. Mode invité activé.")
                st.rerun()
        else:
            st.subheader("Se connecter")
            login_nom = st.text_input("Nom d'utilisateur", key="login_nom")
            login_pw = st.text_input("Mot de passe", type="password", key="login_pw")
            if st.button("Se connecter"):
                user_id = validate_user(login_nom, login_pw)
                if user_id:
                    st.session_state.logged_in = True
                    st.session_state.current_user_id = int(user_id)
                    st.success("Connecté !")
                    st.rerun()
                else:
                    st.error("Nom ou mot de passe incorrect.")

            st.divider()
            st.subheader("Créer un nouvel utilisateur")
            new_nom = st.text_input("Nom", placeholder="Ex: Dupont", key="new_nom")
            new_prenom = st.text_input(
                "Prénom", placeholder="Ex: Jean", key="new_prenom"
            )
            new_email = st.text_input(
                "Email", placeholder="Ex: jean@dupont.fr", key="new_email"
            )
            new_pw = st.text_input("Mot de passe", type="password", key="new_pw")
            new_pw_confirm = st.text_input(
                "Confirmer mot de passe", type="password", key="new_pw_confirm"
            )

            # 👉 Nouveau : photo de profil (optionnel)
            new_avatar = st.file_uploader(
                "Photo de profil (PNG/JPG, optionnel)",
                type=["png", "jpg", "jpeg"],
                accept_multiple_files=False,
            )

            if st.button("Créer"):
                if new_pw != new_pw_confirm:
                    st.error("Les mots de passe ne correspondent pas.")
                elif not new_nom or not new_pw:
                    st.error("Nom et mot de passe requis.")
                else:
                    try:
                        new_id = create_user(new_nom, new_prenom, new_email, new_pw)
                        if new_avatar is not None:
                            save_avatar(int(new_id), new_avatar)
                        st.session_state.logged_in = True
                        st.session_state.current_user_id = int(new_id)
                        st.success("Utilisateur créé et connecté !")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))


# ================================================
#  CHARGEMENT DES DONNÉES UTILISATEUR
# ================================================
# Cette partie charge les transactions de l’utilisateur courant
# à partir de la base SQLite, grâce à la fonction load_user_df().
# Les données sont ensuite stockées dans st.session_state.df
# pour être utilisées dans tout le tableau de bord.


if "df" not in st.session_state:
    st.session_state.df = load_user_df(st.session_state.current_user_id)
else:
    st.session_state.df = load_user_df(st.session_state.current_user_id)

# ---------- Sidebar : Import / Export CSV ----------
# Permet de :
# - télécharger toutes les transactions au format CSV (ou un modèle vide)
# - réimporter un CSV pour alimenter la base SQLite
# - éviter les doublons d'import et afficher un message de succès/erreur


st.sidebar.subheader("📦 Données (CSV) – Base SQLite")


# -- Download (export ou modèle 36 mois)
csv_bytes = build_export_or_template_csv_sqlite(months=36)
st.sidebar.download_button(
    label="⬇️ Télécharger CSV",
    data=csv_bytes,
    file_name="kpflo_finances.csv",
    mime="text/csv",
    help="Modèle (ou export DB) pour tous les utilisateurs.",
)

# -- Import (FORMULAIRE + garde anti-répétition)
st.sidebar.markdown("—")
st.sidebar.markdown("**📥 Importer un CSV**")

with st.sidebar.form("csv_import_form", clear_on_submit=False):
    replace_all = st.checkbox(
        "Remplacer toutes les transactions des utilisateurs présents dans le CSV",
        value=False,
    )
    uploaded = st.file_uploader(
        "CSV unifié (user_id,date,kind,category,label,amount)",
        type=["csv"],
        key="csv_upload",
    )
    submitted = st.form_submit_button("Importer")

if submitted:
    if uploaded is None:
        st.sidebar.error("Aucun fichier sélectionné.")
    else:
        content = uploaded.getvalue()
        file_hash = hashlib.md5(content).hexdigest()
        last_hash = st.session_state.get("last_import_hash")

        if last_hash == file_hash:
            st.sidebar.info("Ce fichier a déjà été importé (aucune action).")
        else:
            ok = import_unified_csv_to_sqlite(
                uploaded,
                st.session_state.current_user_id,
                replace_all=replace_all,
            )
            if ok:
                st.session_state["last_import_hash"] = file_hash
                st.sidebar.success("Import réussi ✅")

                st.rerun()
            else:
                st.sidebar.error("Import échoué. Vérifie le format des colonnes.")


# ================================================
#  INTERFACE PRINCIPALE : ONGLET REVENUS / DÉPENSES / BUDGET
# ================================================
# On crée ici trois onglets principaux avec Streamlit :
# - 📈 Revenus : pour visualiser et modifier les entrées d’argent
# - 📉 Dépenses : pour suivre les sorties
# - 📊 Budget : pour analyser le solde et les prévisions
tab_revenus, tab_depenses, tab_budget = st.tabs(
    ["📈 Revenus", "📉 Dépenses", "📊 Budget"]
)


# ================================================
#  SECTION : REVENUS  (Interface et style)
# ================================================
# Cette partie ouvre l’onglet "Revenus" :
with tab_revenus:
    st.subheader("Ajoute tes revenus")

    # --- CSS local de la section ---
    st.markdown(
        """
    <style>
      :root { --primary-color: #f97316; } /* orange pour les boutons primary */

      /* HEADER wrapper */
      #revenus-header { margin-bottom: 12px; }

      /* Styliser UNIQUEMENT le select du header (petit rectangle) */
      #revenus-header div[data-baseweb="select"]{
        width: 220px !important;
        border: 2px solid #d1d5db !important;
        border-radius: 6px !important;
        background-color: #ffffff !important;
        transition: all 0.2s ease-in-out;
      }
      #revenus-header div[data-baseweb="select"]:hover{
        border-color: #f97316 !important;
        box-shadow: 0 0 5px rgba(249,115,22,0.3) !important;
      }
      #revenus-header div[data-baseweb="select"] > div{
        font-size: 0.95rem !important;
        color: #333 !important;
        font-weight: 500 !important;
      }

      /* Mini-carte récap total à droite */
      .revenus-mini-wrap { display: flex; justify-content: flex-end; }
      .revenus-mini {
        display: inline-flex; align-items: baseline; gap: 12px;
        padding: 10px 14px;
        border: 2px solid #d1d5db;
        border-radius: 10px;
        background: #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      }
      .revenus-mini .mini-title { font-weight: 600; color: #374151; }
      .revenus-mini .mini-total { font-variant-numeric: tabular-nums; font-weight: 700; color: #111827; }
      .revenus-mini .mini-euro { opacity: .85; }
      .revenus-mini .mini-badge { margin-left: 6px; font-size: 12px; padding: 2px 6px; border-radius: 999px; background: #f3f4f6; color: #6b7280; }

      hr { border-top: 1px solid #e0e0e0 !important; margin: 4px 0 !important; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    #  HEADER REVENUS : Sélecteur + Total prévisionnel

    # Cette section crée l’en-tête de la page Revenus :
    # - À gauche : un menu déroulant pour choisir le mode d’affichage (par mois ou par année).
    # - À droite : un mini-récapitulatif qui affiche la somme totale des revenus.

    st.markdown('<div id="revenus-header">', unsafe_allow_html=True)
    header_left, header_right = st.columns([1, 5], vertical_alignment="center")

    with header_left:
        group_mode = st.selectbox(
            "🗓️ Grouper par",
            ["Mois", "Année"],  # Semaine retiré
            key="revenus_group_mode",
            index=0,
            label_visibility="visible",
        )

    st.session_state.df = load_user_df(
        st.session_state.current_user_id
    )  # recharge les data avant le calcul du total
    total_preview = get_revenus_preview_summary(
        st.session_state.current_user_id, st.session_state.revenus_forms
    )

    with header_right:
        st.markdown(
            f"""
        <div class="revenus-mini-wrap">
          <div class="revenus-mini" title="Somme des revenus affichés (prévisualisation)">
            <span class="mini-title">Revenus enregistrés</span>
            <span class="mini-total">{total_preview:.2f}</span>
            <span class="mini-euro">€</span>
            <span class="mini-badge">Total</span>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)  # /revenus-header

    #  DONNÉES REVENUS + HELPERS LOCAUX

    df_revenus_all = cached_revenus_df(st.session_state.current_user_id)

    # ---------- Mode "Mois" : un seul niveau (mois -> lignes) ----------
    if group_mode == "Mois" and not df_revenus_all.empty:
        df = df_revenus_all.copy()
        dt = pd.to_datetime(df["date"])
        df["month_key"] = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")  # ex. "October 2025"

        grouped = (
            df.groupby("month_key")
            .agg(
                total_montant=("montant", "sum"),
                month_name=("month_name", "first"),
                count=("id", "count"),
            )
            .reset_index()
            .sort_values("month_key", ascending=False)
        )

        for _, g in grouped.iterrows():
            m_key = g["month_key"]
            m_name = g["month_name"]
            m_total = float(g["total_montant"])
            m_count = int(g["count"])

            # Titre fixe de l’expander
            display_name = f"Revenus_{m_name}"

            with st.expander(
                f"{display_name} ({m_total:.2f} €) - {m_count} lignes", expanded=False
            ):
                # Lignes du mois (optimisées avec st.dataframe)
                m_df = (
                    df[df["month_key"] == m_key]
                    .sort_values("date", ascending=False)
                    .copy()
                )
                # Ajouter une colonne row_num pour un index utilisateur (1, 2, 3, ...)
                m_df = m_df.reset_index(drop=True)
                m_df["row_num"] = m_df.index + 1

                # Préparer un affichage propre
                display_df = m_df[
                    ["row_num", "date", "categorie", "libelle", "montant"]
                ].copy()
                display_df["montant"] = display_df["montant"].map(
                    lambda x: f"{float(x):.2f} €"
                )
                display_df.columns = [
                    "Ligne #",
                    "Date",
                    "Catégorie",
                    "Libellé",
                    "Montant",
                ]

                st.dataframe(
                    display_df,
                    hide_index=True,
                    use_container_width=True,
                    height=300,  # Hauteur fixe pour activer la pagination native
                )

                # Panneau pour gérer une ligne existante
                st.markdown("#### Gérer une ligne existante")
                selected_rownum = st.text_input(
                    "Numéro de ligne à modifier / supprimer",
                    key=f"rev_manage_row_{m_key}",
                    placeholder="ex: 1",
                )

                action_col1, action_col2 = st.columns(2)

                with action_col1:
                    if st.button(
                        "❌ Supprimer cette ligne",
                        key=f"rev_delete_{m_key}",
                        type="secondary",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            # Convertir row_num en ID interne
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                real_id = int(row_match.iloc[0]["id"])
                                delete_revenu(real_id, st.session_state.current_user_id)

                                # Invalider les caches
                                cached_revenus_df.clear()
                                cached_depenses_df.clear()
                                load_user_df.clear()

                                st.session_state.df = load_user_df(
                                    st.session_state.current_user_id
                                )

                                st.success(f"Ligne {rownum_int} supprimée.")
                                st.rerun()

                        except Exception as e:
                            st.error(f"Erreur suppression : {e}")

                with action_col2:
                    if st.button(
                        "✏️ Charger pour édition",
                        key=f"rev_load_{m_key}",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            # Convertir row_num en ID interne
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                r = row_match.iloc[0]
                                # Ajouter la ligne au formulaire de saisie
                                st.session_state.revenus_forms.append(
                                    {
                                        "id": int(r["id"]),
                                        "date": r["date"],
                                        "categorie": r["categorie"],
                                        "libelle": r["libelle"],
                                        "montant": float(r["montant"]),
                                    }
                                )
                                st.info(
                                    f"Ligne {rownum_int} chargée en bas pour édition."
                                )
                                st.rerun()

                        except Exception as e:
                            st.error(f"Erreur chargement : {e}")

                st.caption(
                    "⚠️ Pour modifier ou supprimer une ligne, entrez son numéro de ligne ci-dessus."
                )

    # ---------- Mode "Année" : deux niveaux (année -> mois -> lignes) ----------
    if group_mode == "Année" and not df_revenus_all.empty:
        df = df_revenus_all.copy()
        dt = pd.to_datetime(df["date"])
        df["year"] = dt.dt.year
        df["month_key"] = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        # Groupes Année
        year_groups = (
            df.groupby("year")
            .agg(total_montant=("montant", "sum"))
            .reset_index()
            .sort_values("year", ascending=False)
        )

        for _, yg in year_groups.iterrows():
            y = int(yg["year"])
            y_total = float(yg["total_montant"])

            year_display_name = f"Revenus_{y}"

            with st.expander(f"{year_display_name} ({y_total:.2f} €)", expanded=False):
                y_df = df[df["year"] == y]
                month_groups = (
                    y_df.groupby("month_key")
                    .agg(
                        total_montant=("montant", "sum"),
                        month_name=("month_name", "first"),
                        count=("id", "count"),
                    )
                    .reset_index()
                    .sort_values("month_key", ascending=False)
                )

                for _, mg in month_groups.iterrows():
                    m_key = mg["month_key"]
                    m_name = mg["month_name"]
                    m_total = float(mg["total_montant"])
                    m_count = int(mg["count"])

                    # Titre fixe de l’expander
                    display_name = f"Revenus_{m_name}"

                    with st.expander(
                        f"{display_name} ({m_total:.2f} €) - {m_count} lignes",
                        expanded=False,
                    ):
                        # Lignes du mois (optimisées avec st.dataframe)
                        m_df = (
                            y_df[y_df["month_key"] == m_key]
                            .sort_values("date", ascending=False)
                            .copy()
                        )
                        # Ajouter une colonne row_num pour un index utilisateur (1, 2, 3, ...)
                        m_df = m_df.reset_index(drop=True)
                        m_df["row_num"] = m_df.index + 1

                        # Préparer un affichage propre
                        display_df = m_df[
                            ["row_num", "date", "categorie", "libelle", "montant"]
                        ].copy()
                        display_df["montant"] = display_df["montant"].map(
                            lambda x: f"{float(x):.2f} €"
                        )
                        display_df.columns = [
                            "Ligne #",
                            "Date",
                            "Catégorie",
                            "Libellé",
                            "Montant",
                        ]

                        st.dataframe(
                            display_df,
                            hide_index=True,
                            use_container_width=True,
                            height=300,  # Hauteur fixe pour activer la pagination native
                        )

                        # Panneau pour gérer une ligne existante
                        st.markdown("#### Gérer une ligne existante")
                        selected_rownum = st.text_input(
                            "Numéro de ligne à modifier / supprimer",
                            key=f"rev_manage_row_{y}_{m_key}",
                            placeholder="ex: 1",
                        )

                        action_col1, action_col2 = st.columns(2)

                        with action_col1:
                            if st.button(
                                "❌ Supprimer cette ligne",
                                key=f"rev_delete_{y}_{m_key}",
                                type="secondary",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    # Convertir row_num en ID interne
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        real_id = int(row_match.iloc[0]["id"])
                                        delete_revenu(
                                            real_id, st.session_state.current_user_id
                                        )

                                        # Invalider les caches
                                        cached_revenus_df.clear()
                                        cached_depenses_df.clear()
                                        load_user_df.clear()

                                        st.session_state.df = load_user_df(
                                            st.session_state.current_user_id
                                        )

                                        st.success(f"Ligne {rownum_int} supprimée.")
                                        st.rerun()

                                except Exception as e:
                                    st.error(f"Erreur suppression : {e}")

                        with action_col2:
                            if st.button(
                                "✏️ Charger pour édition",
                                key=f"rev_load_{y}_{m_key}",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    # Convertir row_num en ID interne
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        r = row_match.iloc[0]
                                        # Ajouter la ligne au formulaire de saisie
                                        st.session_state.revenus_forms.append(
                                            {
                                                "id": int(r["id"]),
                                                "date": r["date"],
                                                "categorie": r["categorie"],
                                                "libelle": r["libelle"],
                                                "montant": float(r["montant"]),
                                            }
                                        )
                                        st.info(
                                            f"Ligne {rownum_int} chargée en bas pour édition."
                                        )
                                        st.rerun()

                                except Exception as e:
                                    st.error(f"Erreur chargement : {e}")

                        st.caption(
                            "⚠️ Pour modifier ou supprimer une ligne, entrez son numéro de ligne ci-dessus."
                        )

    # ---------- Saisie dynamique des nouvelles lignes ----------
    edited_rows = []
    if st.session_state.revenus_forms:
        st.markdown("### Saisie en cours (nouvelle série ou édition)")
        header_cols = st.columns([1, 2, 2.6, 1, 0.6])
        header_cols[0].markdown("**Date**")
        header_cols[1].markdown("**Catégorie**")
        header_cols[2].markdown("**Libellé**")
        header_cols[3].markdown("**Montant (€)**")

    for i, form in enumerate(st.session_state.revenus_forms):
        key_date = f"rev_date_{i}"
        key_cat = f"rev_cat_{i}"
        key_lib_choice = f"rev_lib_choice_{i}"
        key_lib_value = f"rev_lib_value_{i}"  # valeur réelle envoyée en DB
        key_montant = f"rev_montant_{i}"

        # Calculer les valeurs initiales
        initial_date = form.get("date", date.today())
        initial_cat = form.get("categorie", INCOME_CATEGORIES[0])
        initial_montant = form.get("montant", 0.0)
        if initial_cat in INCOME_CATEGORIES:
            initial_cat_index = INCOME_CATEGORIES.index(initial_cat)
        else:
            initial_cat_index = 0

        init_opts = income_suggestions_for_category(initial_cat, initial_date)
        initial_lib = form.get("libelle")
        if not initial_lib or initial_lib not in init_opts:
            initial_lib = init_opts[0] if init_opts else "Revenu"
        if initial_lib in init_opts:
            initial_lib_index = init_opts.index(initial_lib)
        else:
            initial_lib_index = 0

        # Colonnes de saisie
        cols = st.columns([1, 2, 2.6, 1, 0.6])

        # Date
        with cols[0]:
            st.date_input(
                "",
                value=initial_date,
                key=key_date,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )

        # Catégorie
        with cols[1]:
            st.selectbox(
                "",
                options=INCOME_CATEGORIES,
                index=initial_cat_index,
                key=key_cat,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )

        # Libellé (sélecteur non-éditable, suffixé mois)
        with cols[2]:
            opts = income_suggestions_for_category(
                st.session_state[key_cat], st.session_state[key_date]
            )
            st.selectbox(
                "",
                options=opts,
                index=initial_lib_index,
                key=key_lib_choice,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )
            # Sync lib_value avec choice après création
            st.session_state[key_lib_value] = st.session_state.get(
                key_lib_choice, initial_lib
            )

        # Montant
        with cols[3]:
            st.number_input(
                "",
                min_value=0.0,
                step=10.0,
                value=float(initial_montant),
                key=key_montant,
                on_change=rev_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_montant),
                label_visibility="hidden",
            )

        # Supprimer la ligne
        with cols[4]:
            if st.button(
                "❌",
                key=f"del_form_{i}",
                type="secondary",
                help="Supprimer cette ligne du formulaire",
            ):
                new_forms = st.session_state.revenus_forms.copy()
                new_forms.pop(i)
                st.session_state.revenus_forms = new_forms
                st.rerun()

        edited_rows.append(
            {
                "id": form.get("id"),
                "date": st.session_state[key_date],
                "categorie": st.session_state[key_cat],
                "libelle": st.session_state[key_lib_value],  # valeur réelle
                "montant": st.session_state[key_montant],
            }
        )

    # ---------- Boutons bas de section ----------
    btn_left, btn_right = st.columns([3, 1])

    with btn_left:
        st.button(
            "Nouvelle ligne",
            key="add_revenu",
            type="primary",
            help="Ajoute une nouvelle ligne de saisie de revenu",
        )
        if st.session_state.get("add_revenu"):
            new_forms = st.session_state.revenus_forms.copy()
            today = date.today()
            # ligne par défaut via core (non suffixée) + suffixe ici pour cohérence UI
            base = default_revenu_row(INCOME_CATEGORIES, today)
            mois = _month_human(today)
            base["libelle"] = f"{base['libelle']}_{mois}" if mois else base["libelle"]
            new_forms.append(base)
            st.session_state.revenus_forms = new_forms
            st.rerun()

    with btn_right:
        if edited_rows and st.button(
            "Enregistrer revenus", type="primary", use_container_width=True
        ):
            try:
                for row in edited_rows:
                    if row.get("id"):
                        update_revenu(row["id"], row, st.session_state.current_user_id)
                    else:
                        insert_transaction(
                            row["date"],
                            "IN",
                            row["categorie"],
                            row["libelle"],
                            float(row["montant"]),
                            recurrent=False,
                            user_id=st.session_state.current_user_id,
                        )

                # on efface les formulaires temporaires
                st.session_state.revenus_forms = []

                # on invalide les caches
                cached_revenus_df.clear()
                cached_depenses_df.clear()  # car les revenus impactent les ratios dans Dépenses
                load_user_df.clear()

                # on recharge les données propres
                st.session_state.df = load_user_df(st.session_state.current_user_id)
                st.balloons()
                st.success("Revenus enregistrés !")

            except Exception as e:
                st.error(f"Erreur : {e}")
            st.rerun()


# =======================
# SECTION : DÉPENSES 🧾 (Année -> Mois -> Lignes)
# - Libellé via depenses.py + suffixe mois
# - "Autre" = entrée non-éditable (suffixée du mois)
# - Ratio sur revenu NET (revenus - Impôts & taxes) avec cibles/caps 50/30/20
# - Affichage court : "Seuil X%" (détail cible/cap en tooltip)
# =======================

with tab_depenses:
    st.subheader("Ajoute tes dépenses")

    # ---------- CSS local ----------
    st.markdown(
        """
    <style>
      :root { --primary-color: #f97316; }
      #depenses-header { margin-bottom: 12px; }
      #depenses-header div[data-baseweb="select"]{
        width: 220px !important; border: 2px solid #d1d5db !important; border-radius: 6px !important;
        background-color: #ffffff !important; transition: all 0.2s ease-in-out;
      }
      #depenses-header div[data-baseweb="select"]:hover{
        border-color: #f97316 !important; box-shadow: 0 0 5px rgba(249,115,22,0.3) !important;
      }
      .depenses-mini-wrap { display: flex; justify-content: flex-end; }
      .depenses-mini {
        display: inline-flex; align-items: baseline; gap: 12px; padding: 10px 14px;
        border: 2px solid #d1d5db; border-radius: 10px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.04);
      }
      .depenses-mini .mini-title { font-weight: 600; color: #374151; }
      .depenses-mini .mini-total { font-variant-numeric: tabular-nums; font-weight: 700; color: #111827; }
      .depenses-mini .mini-euro { opacity: .85; }
      .ratio-box {
        width:100%; padding:8px 10px; border-radius:8px; border:1.5px solid #d1d5db; background:#f9fafb;
        font-weight:600; text-align:center; line-height:1.25;
      }
      .ratio-sub { display:block; font-weight:500; font-size:12px; opacity:.8; margin-top:2px; }
      .ratio-ok{background:#ecfdf5;border-color:#34d399;color:#065f46;}
      .ratio-warn{background:#fff7ed;border-color:#fb923c;color:#7c2d12;}
      .ratio-bad{background:#fef2f2;border-color:#f87171;color:#7f1d1d;}
      .ratio-na{background:#f3f4f6;border-color:#d1d5db;color:#374151;}
      hr { border-top: 1px solid #e0e0e0 !important; margin: 4px 0 !important; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    # ---------- Header : sélecteur + mini-total ----------
    st.markdown('<div id="depenses-header">', unsafe_allow_html=True)
    header_left, header_right = st.columns([1, 5], vertical_alignment="center")

    with header_left:
        dep_group_mode = st.selectbox(
            "🗓️ Grouper par",
            ["Mois", "Année"],
            key="depenses_group_mode_v3",
            index=0,
            label_visibility="visible",
        )

    df_depenses_all = cached_depenses_df(st.session_state.current_user_id)
    total_depenses_preview = (
        float(df_depenses_all["montant"].sum()) if not df_depenses_all.empty else 0.0
    )

    with header_right:
        st.markdown(
            f"""<div class="depenses-mini-wrap">
                  <div class="depenses-mini" title="Somme des dépenses affichées (prévisualisation)">
                    <span class="mini-title">Dépenses enregistrées</span>
                    <span class="mini-total">{total_depenses_preview:.2f}</span>
                    <span class="mini-euro">€</span>
                  </div>
                </div>""",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    revenus_df_all = cached_revenus_df(st.session_state.current_user_id)

    # ---------- Groupes (Mois / Année -> Mois -> Lignes) ----------
    if dep_group_mode == "Mois" and not df_depenses_all.empty:
        df = df_depenses_all.copy()
        dt = pd.to_datetime(df["date"])
        df["month_key"] = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        grouped = (
            df.groupby("month_key")
            .agg(
                total_montant=("montant", "sum"),
                month_name=("month_name", "first"),
                count=("id", "count"),
            )
            .reset_index()
            .sort_values("month_key", ascending=False)
        )

        for _, g in grouped.iterrows():
            m_key, m_name = g["month_key"], g["month_name"]
            m_total, m_count = float(g["total_montant"]), int(g["count"])

            # Titre fixe de l’expander
            display_name = f"Dépenses_{m_name}"

            with st.expander(
                f"{display_name} ({m_total:.2f} €) - {m_count} lignes", expanded=False
            ):
                # Calcul du ratio global pour le mois
                m_df = df[df["month_key"] == m_key]
                for cat in m_df["categorie"].unique():
                    cat_total = float(m_df[m_df["categorie"] == cat]["montant"].sum())
                    st.markdown(f"<b>{cat}</b>:", unsafe_allow_html=True)
                    render_ratio_box(
                        m_df["date"].iloc[0],
                        cat,
                        cat_total,
                        revenus_df_all,
                        df_depenses_all,
                    )

                # Lignes du mois (optimisées avec st.dataframe)
                m_df = (
                    df[df["month_key"] == m_key]
                    .sort_values("date", ascending=False)
                    .copy()
                )
                # Ajouter une colonne row_num pour un index utilisateur (1, 2, 3, ...)
                m_df = m_df.reset_index(drop=True)
                m_df["row_num"] = m_df.index + 1

                # Préparer un affichage propre
                display_df = m_df[
                    ["row_num", "date", "categorie", "libelle", "montant"]
                ].copy()
                display_df["montant"] = display_df["montant"].map(
                    lambda x: f"{float(x):.2f} €"
                )
                display_df.columns = [
                    "Ligne #",
                    "Date",
                    "Catégorie",
                    "Libellé",
                    "Montant (€)",
                ]

                st.dataframe(
                    display_df,
                    hide_index=True,
                    use_container_width=True,
                    height=300,
                )

                # Panneau pour gérer une ligne existante
                st.markdown("#### Gérer une ligne existante")
                selected_rownum = st.text_input(
                    "Numéro de ligne à modifier / supprimer",
                    key=f"dep_manage_row_{m_key}",
                    placeholder="ex: 1",
                )

                c1, c2 = st.columns(2)

                with c1:
                    if st.button(
                        "❌ Supprimer cette ligne",
                        key=f"dep_delete_{m_key}",
                        type="secondary",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                real_id = int(row_match.iloc[0]["id"])
                                delete_depense(
                                    real_id, st.session_state.current_user_id
                                )

                                # Invalider les caches
                                cached_revenus_df.clear()
                                cached_depenses_df.clear()
                                load_user_df.clear()

                                st.session_state.df = load_user_df(
                                    st.session_state.current_user_id
                                )

                                st.success(f"Ligne {rownum_int} supprimée.")
                                st.rerun()

                        except Exception as e:
                            st.error(f"Erreur suppression : {e}")

                with c2:
                    if st.button(
                        "✏️ Charger pour édition",
                        key=f"dep_load_{m_key}",
                        disabled=(selected_rownum.strip() == ""),
                    ):
                        try:
                            rownum_int = int(selected_rownum)
                            row_match = m_df[m_df["row_num"] == rownum_int]
                            if row_match.empty:
                                st.warning("Numéro de ligne invalide.")
                            else:
                                r = row_match.iloc[0]
                                # Pousser la ligne dans le formulaire dynamique
                                st.session_state.depenses_forms.append(
                                    {
                                        "id": int(r["id"]),
                                        "date": r["date"],
                                        "categorie": r["categorie"],
                                        "libelle": r["libelle"],
                                        "montant": float(r["montant"]),
                                    }
                                )
                                st.info(
                                    f"Ligne {rownum_int} chargée en bas pour édition."
                                )
                                st.rerun()

                        except Exception as e:
                            st.error(f"Erreur chargement : {e}")

                st.caption(
                    "Vous pouvez modifier ou supprimer une ligne en indiquant son numéro de ligne ci-dessus."
                )

    if dep_group_mode == "Année" and not df_depenses_all.empty:
        df = df_depenses_all.copy()
        dt = pd.to_datetime(df["date"])
        df["year"] = dt.dt.year
        df["month_key"] = dt.dt.strftime("%Y-%m")
        df["month_name"] = dt.dt.strftime("%B %Y")

        year_groups = (
            df.groupby("year")
            .agg(total_montant=("montant", "sum"))
            .reset_index()
            .sort_values("year", ascending=False)
        )

        for _, yg in year_groups.iterrows():
            y, y_total = int(yg["year"]), float(yg["total_montant"])
            year_display_name = f"Dépenses_{y}"
            with st.expander(f"{year_display_name} ({y_total:.2f} €)", expanded=False):
                y_df = df[df["year"] == y].sort_values("date", ascending=False)
                month_groups = (
                    y_df.groupby("month_key")
                    .agg(
                        total_montant=("montant", "sum"),
                        month_name=("month_name", "first"),
                        count=("id", "count"),
                    )
                    .reset_index()
                    .sort_values("month_key", ascending=False)
                )
                for _, mg in month_groups.iterrows():
                    m_key, m_name = mg["month_key"], mg["month_name"]
                    m_total, m_count = float(mg["total_montant"]), int(mg["count"])

                    # Titre fixe de l’expander
                    display_name = f"Dépenses_{m_name}"

                    with st.expander(
                        f"{display_name} ({m_total:.2f} €) - {m_count} lignes",
                        expanded=False,
                    ):
                        # Calcul du ratio global pour le mois
                        m_df = y_df[y_df["month_key"] == m_key]
                        for cat in m_df["categorie"].unique():
                            cat_total = float(
                                m_df[m_df["categorie"] == cat]["montant"].sum()
                            )
                            st.markdown(f"<b>{cat}</b>:", unsafe_allow_html=True)
                            render_ratio_box(
                                m_df["date"].iloc[0],
                                cat,
                                cat_total,
                                revenus_df_all,
                                df_depenses_all,
                            )

                        # Lignes du mois (optimisées avec st.dataframe)
                        m_df = (
                            y_df[y_df["month_key"] == m_key]
                            .sort_values("date", ascending=False)
                            .copy()
                        )
                        # Ajouter une colonne row_num pour un index utilisateur (1, 2, 3, ...)
                        m_df = m_df.reset_index(drop=True)
                        m_df["row_num"] = m_df.index + 1

                        # Préparer un affichage propre
                        display_df = m_df[
                            ["row_num", "date", "categorie", "libelle", "montant"]
                        ].copy()
                        display_df["montant"] = display_df["montant"].map(
                            lambda x: f"{float(x):.2f} €"
                        )
                        display_df.columns = [
                            "Ligne #",
                            "Date",
                            "Catégorie",
                            "Libellé",
                            "Montant (€)",
                        ]

                        st.dataframe(
                            display_df,
                            hide_index=True,
                            use_container_width=True,
                            height=300,
                        )

                        # Panneau pour gérer une ligne existante
                        st.markdown("#### Gérer une ligne existante")
                        selected_rownum = st.text_input(
                            "Numéro de ligne à modifier / supprimer",
                            key=f"dep_manage_row_{y}_{m_key}",
                            placeholder="ex: 1",
                        )

                        c1, c2 = st.columns(2)

                        with c1:
                            if st.button(
                                "❌ Supprimer cette ligne",
                                key=f"dep_delete_{y}_{m_key}",
                                type="secondary",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        real_id = int(row_match.iloc[0]["id"])
                                        delete_depense(
                                            real_id, st.session_state.current_user_id
                                        )

                                        # Invalider les caches
                                        cached_revenus_df.clear()
                                        cached_depenses_df.clear()
                                        load_user_df.clear()

                                        st.session_state.df = load_user_df(
                                            st.session_state.current_user_id
                                        )

                                        st.success(f"Ligne {rownum_int} supprimée.")
                                        st.rerun()

                                except Exception as e:
                                    st.error(f"Erreur suppression : {e}")

                        with c2:
                            if st.button(
                                "✏️ Charger pour édition",
                                key=f"dep_load_{y}_{m_key}",
                                disabled=(selected_rownum.strip() == ""),
                            ):
                                try:
                                    rownum_int = int(selected_rownum)
                                    row_match = m_df[m_df["row_num"] == rownum_int]
                                    if row_match.empty:
                                        st.warning("Numéro de ligne invalide.")
                                    else:
                                        r = row_match.iloc[0]
                                        # Pousser la ligne dans le formulaire dynamique
                                        st.session_state.depenses_forms.append(
                                            {
                                                "id": int(r["id"]),
                                                "date": r["date"],
                                                "categorie": r["categorie"],
                                                "libelle": r["libelle"],
                                                "montant": float(r["montant"]),
                                            }
                                        )
                                        st.info(
                                            f"Ligne {rownum_int} chargée en bas pour édition."
                                        )
                                        st.rerun()

                                except Exception as e:
                                    st.error(f"Erreur chargement : {e}")

                        st.caption(
                            "Vous pouvez modifier ou supprimer une ligne en indiquant son numéro de ligne ci-dessus."
                        )

    # =================================================
    # Saisie dynamique (formulaires)
    # =================================================
    edited_rows_dep = []
    for i, form in enumerate(st.session_state.depenses_forms):
        key_date = f"dep_date_{i}"
        key_cat = f"dep_cat_{i}"
        key_lib_choice = f"dep_lib_choice_{i}"
        key_lib_value = f"dep_lib_value_{i}"
        key_amt = f"dep_montant_{i}"

        # Valeurs initiales propres
        initial_date = form.get("date", date.today())
        initial_cat = form.get("categorie", EXPENSE_CATEGORIES[0])
        initial_amt = form.get("montant", 0.0)

        # Index initial pour la catégorie
        if initial_cat in EXPENSE_CATEGORIES:
            initial_cat_index = EXPENSE_CATEGORIES.index(initial_cat)
        else:
            initial_cat_index = 0

        # Options de libellé
        init_opts = expense_suggestions_for_category(initial_cat, initial_date)
        initial_lib = form.get("libelle", init_opts[0] if init_opts else "Dépense")
        if initial_lib not in init_opts and init_opts:
            initial_lib = init_opts[0]
        try:
            initial_lib_index = init_opts.index(initial_lib)
        except ValueError:
            initial_lib_index = 0

        # Colonnes de saisie
        cols = st.columns([1, 2, 2.6, 1.2, 1.2, 0.6])

        # DATE
        with cols[0]:
            st.date_input(
                "Date",
                value=initial_date,
                key=key_date,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )

        # CATEGORIE
        with cols[1]:
            st.selectbox(
                "Catégorie",
                options=EXPENSE_CATEGORIES,
                index=initial_cat_index,
                key=key_cat,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )

        # LIBELLE
        with cols[2]:
            st.selectbox(
                "Libellé",
                options=init_opts,
                index=initial_lib_index,
                key=key_lib_choice,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )
            # Synchroniser la valeur réelle libellé dans key_lib_value
            st.session_state[key_lib_value] = st.session_state.get(
                key_lib_choice, initial_lib
            )

        # MONTANT
        with cols[3]:
            st.number_input(
                "Montant (€)",
                min_value=0.0,
                step=5.0,
                value=float(initial_amt),
                key=key_amt,
                on_change=dep_update_form,
                args=(i, key_date, key_cat, key_lib_choice, key_lib_value, key_amt),
            )

        # RATIO LIVE
        with cols[4]:
            cur_date = st.session_state.get(key_date, initial_date)
            cur_cat = st.session_state.get(key_cat, initial_cat)
            cur_amt = float(st.session_state.get(key_amt, initial_amt))
            render_ratio_box(
                cur_date, cur_cat, cur_amt, revenus_df_all, df_depenses_all
            )

        # SUPPRIMER LA LIGNE DU FORMULAIRE
        with cols[5]:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            if st.button(
                "❌",
                key=f"del_dep_form_{i}",
                type="secondary",
                help="Supprimer cette ligne du formulaire",
                use_container_width=True,
            ):
                st.session_state.depenses_forms.pop(i)
                # Nettoyer les clés des lignes suivantes
                for j in range(i + 1, len(st.session_state.depenses_forms) + 1):
                    for k in [
                        f"dep_date_{j}",
                        f"dep_cat_{j}",
                        f"dep_lib_choice_{j}",
                        f"dep_lib_value_{j}",
                        f"dep_montant_{j}",
                    ]:
                        st.session_state.pop(k, None)
                st.rerun()

        # Construire la ligne à sauvegarder
        edited_rows_dep.append(
            {
                "id": form.get("id"),
                "date": st.session_state.get(key_date, initial_date),
                "categorie": st.session_state.get(key_cat, initial_cat),
                "libelle": st.session_state.get(key_lib_value, initial_lib),
                "montant": st.session_state.get(key_amt, initial_amt),
            }
        )

    st.session_state.depenses_forms = edited_rows_dep

    # ---------- Boutons bas ----------
    #  DÉPENSES – ACTIONS (bas de section)
    btn_left, btn_right = st.columns([3, 1])

    with btn_left:
        st.button("➕ Nouvelle dépense", key="add_depense", type="primary")
        if st.session_state.get("add_depense"):
            today = date.today()
            # Base par défaut via depenses.py (catégorie + libellé cohérents)
            base = default_depense_row(
                EXPENSE_CATEGORIES, today
            )  # dict: date, categorie, libelle, montant
            # Suffixe le mois sur le libellé proposé (par cohérence d'affichage)
            mois = _month_human(today)
            base["libelle"] = (
                f"{base['libelle']}_{mois}"
                if mois and "_" not in base["libelle"]
                else base["libelle"]
            )
            st.session_state.depenses_forms.append(base)
            st.rerun()

    with btn_right:
        if edited_rows_dep and st.button(
            "💾 Enregistrer dépenses", type="primary", use_container_width=True
        ):
            try:
                # Écrit en base (INSERT ou UPDATE)
                save_depenses_edits(edited_rows_dep, st.session_state.current_user_id)

                # Vider le formulaire temporaire
                st.session_state.depenses_forms = []

                # Invalider les caches
                cached_revenus_df.clear()
                cached_depenses_df.clear()
                load_user_df.clear()

                # Recharger les données fraîches pour le dashboard global
                st.session_state.df = load_user_df(st.session_state.current_user_id)

                st.success("Dépenses enregistrées !")
                st.rerun()

            except Exception as e:
                st.error(f"Erreur : {e}")


# ================================================
#  BUDGET / DASHBOARD
# ================================================
with tab_budget:

    # --- compute_month_basics local ---
    def compute_month_basics(df: pd.DataFrame) -> dict:
        if df.empty:
            return {"revenus": 0.0, "depenses": 0.0, "solde": 0.0}
        revenus = float(df.loc[df["type"] == "IN", "montant"].sum())
        depenses = float(-df.loc[df["type"] == "OUT", "montant"].sum())
        return {"revenus": revenus, "depenses": depenses, "solde": revenus - depenses}

    # --- Styles ---
    st.markdown(
        """
        <style>
        .budget-card { border-radius: 10px; border: 1.5px solid #d1d5db; padding: 12px 16px; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.03); font-family: system-ui; }
        .budget-card.ok { background: #ecfdf5; border-color: #34d399; color: #065f46; }
        .budget-card.warn { background: #fff7ed; border-color: #fb923c; color: #7c2d12; }
        .budget-card.bad { background: #fef2f2; border-color: #f87171; color: #7f1d1d; }
        .budget-card .label { font-size: 0.9rem; font-weight: 600; line-height: 1.2; margin-bottom: 4px; }
        .budget-card .mainline { font-size: 1.1rem; font-weight: 700; line-height: 1.3; }
        .budget-card .subline { font-size: 0.8rem; font-weight: 500; opacity: 0.8; line-height: 1.2; margin-top: 4px; }
        .alert-row { border-radius:8px; border:1.5px solid #d1d5db; padding:8px 12px; box-shadow:0 1px 2px rgba(0,0,0,0.03); font-size:0.9rem; line-height:1.4; font-weight:500; margin-bottom:8px; background:#fff; color:#374151; }
        .alert-row.spend { border-color:#fb923c; background:#fff7ed; color:#7c2d12; }
        .header-stat-card { border: 1.5px solid #d1d5db; background: #fff; border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.03); padding: 8px 12px; min-width: 120px; text-align: center; font-family: system-ui; }
        .header-stat-label { font-size: 0.7rem; font-weight: 600; color: #6b7280; line-height: 1.2; margin-bottom: 4px; }
        .header-stat-value { font-size: 1rem; font-weight: 700; line-height: 1.3; }
        .header-stat-value.normal { color: #111827; }
        .header-solde-note-inline { font-size: 0.65rem; font-weight: 500; color: #9ca3af; line-height: 1.2; margin-left: 4px; white-space: nowrap; }
        .header-wrapper { display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; row-gap:12px; column-gap:16px; margin-bottom:12px; padding-bottom:12px; border-bottom:1px solid #e5e7eb; }
        .title-nav-container { display: flex; align-items: center; justify-content: center; gap: 4px; margin-bottom: 8px; }
        .solde-green { color: #065f46; }
        .solde-blue { color: #111827; }
        .solde-orange { color: #9a3412; }
        .solde-red { color: #7f1d1d; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- State ---
    #  BUDGET – INITIALISATION DU STATE
    if "budget_view_mode" not in st.session_state:
        st.session_state.budget_view_mode = "mois"
    if "budget_selected_month_key" not in st.session_state:
        st.session_state.budget_selected_month_key = None
    if "budget_selected_year" not in st.session_state:
        st.session_state.budget_selected_year = None

    # --- Calculs : préparation des mois disponibles ---
    df_all_dates = st.session_state.df.copy()
    df_all_dates["date"] = pd.to_datetime(df_all_dates["date"], errors="coerce")
    df_all_dates = df_all_dates.dropna(subset=["date"])

    MOIS_FR_LONG = {
        1: "Janvier",
        2: "Février",
        3: "Mars",
        4: "Avril",
        5: "Mai",
        6: "Juin",
        7: "Juillet",
        8: "Août",
        9: "Septembre",
        10: "Octobre",
        11: "Novembre",
        12: "Décembre",
    }

    df_all_dates["year"] = df_all_dates["date"].dt.year
    df_all_dates["month"] = df_all_dates["date"].dt.month
    df_all_dates["ym_key"] = df_all_dates["date"].dt.strftime("%Y-%m")

    mois_uniques = (
        df_all_dates.groupby(["ym_key", "year", "month"])
        .size()
        .reset_index()[["ym_key", "year", "month"]]
        .sort_values(["year", "month"], ascending=[False, False])
        .to_dict("records")
    )

    month_options = []
    for item in mois_uniques:
        y = int(item["year"])
        m = int(item["month"])
        label = f"{MOIS_FR_LONG[m]} {y}"
        month_options.append(
            {
                "key": f"{y}-{m:02d}",
                "label": label,
                "year": y,
                "month": m,
            }
        )

    # Default month
    today_y = date.today().year
    today_m = date.today().month
    today_key = f"{today_y}-{today_m:02d}"

    if any(opt["key"] == today_key for opt in month_options):
        default_month_key = today_key
    else:
        default_month_key = month_options[0]["key"] if month_options else today_key

    if st.session_state.budget_selected_month_key is None:
        st.session_state.budget_selected_month_key = default_month_key

    # --- Header avec sélecteurs ---
    with st.container():
        st.markdown('<div class="title-nav-container">', unsafe_allow_html=True)
        title_left, title_center, title_right = st.columns([0.33, 0.34, 0.33])

        # --- Colonne gauche : sélecteur de période ---
        with title_left:
            if st.session_state.budget_view_mode == "mois":
                labels_mois = [opt["label"] for opt in month_options]
                keys_mois = [opt["key"] for opt in month_options]

                if len(keys_mois) == 0:
                    # Aucun mois disponible : probablement aucun revenu/dépense daté
                    st.warning(
                        "⚠️ Ajoute au moins une ligne de revenu ou de dépense avec une date pour activer le suivi mensuel."
                    )
                    chosen_key = None
                else:
                    # Déterminer l’index courant si possible
                    current_key = st.session_state.budget_selected_month_key
                    if current_key not in keys_mois:
                        current_idx = 0
                    else:
                        current_idx = keys_mois.index(current_key)

                    chosen_idx = st.selectbox(
                        "Période",
                        range(len(keys_mois)),
                        index=current_idx,
                        format_func=lambda i: labels_mois[i],
                        key="budget_month_selector_ui",
                    )

                    # chosen_idx doit être un entier valide ici
                    chosen_key = keys_mois[chosen_idx]

                # Mise à jour de la clé sélectionnée seulement si elle est valide
                if chosen_key is not None:
                    st.session_state.budget_selected_month_key = chosen_key

            else:
                # --- Mode "année" ---
                years = sorted(
                    st.session_state.df["date"].dt.year.unique(), reverse=True
                )

                if years:
                    if (
                        st.session_state.budget_selected_year is None
                        or st.session_state.budget_selected_year not in years
                    ):
                        st.session_state.budget_selected_year = years[0]

                    selected_year = st.selectbox(
                        "Année",
                        years,
                        index=years.index(st.session_state.budget_selected_year),
                        key="budget_year_selector",
                    )
                    st.session_state.budget_selected_year = selected_year
                else:
                    st.warning(
                        "⚠️ Aucune donnée datée trouvée. Ajoute des opérations pour activer la vue annuelle."
                    )

        # --- Colonne centre : titre + switch vue ---
        with title_center:
            if st.session_state.budget_view_mode == "mois":
                chosen_key = st.session_state.budget_selected_month_key
                chosen_label = next(
                    (opt["label"] for opt in month_options if opt["key"] == chosen_key),
                    "Mois sélectionné",
                )
                titre_dashboard = f"Situation {chosen_label}"
            else:
                titre_dashboard = (
                    f"Situation année {st.session_state.budget_selected_year}"
                )

            st.markdown(
                f'<div style="text-align:center; font-weight:600; font-size:1rem; color:#111827; line-height:1.3;">{titre_dashboard}</div>',
                unsafe_allow_html=True,
            )

            view_choice = st.selectbox(
                "Vue",
                options=["mois", "annee"],
                index=0 if st.session_state.budget_view_mode == "mois" else 1,
                format_func=lambda x: "Mensuelle" if x == "mois" else "Annuelle",
                key="budget_view_mode_selector",
            )
            if view_choice != st.session_state.budget_view_mode:
                st.session_state.budget_view_mode = view_choice
                st.rerun()

        with title_right:
            st.write("")

        st.markdown("</div>", unsafe_allow_html=True)

    # --- Récupération finale de la période ---
    if st.session_state.budget_view_mode == "mois":
        y_str, m_str = st.session_state.budget_selected_month_key.split("-")
        target_year = int(y_str)
        target_month = int(m_str)
        df_month = slice_df_for_month(st.session_state.df, target_year, target_month)
        df_year = slice_df_for_year(st.session_state.df, target_year)
    else:
        target_year = int(st.session_state.budget_selected_year)
        df_year = slice_df_for_year(st.session_state.df, target_year)
        df_month = pd.DataFrame(
            columns=st.session_state.df.columns
        )  # vide pour cohérence

    # --- Calculs finaux ---
    s = compute_summary(df_month)
    s_year = compute_summary(df_year)

    month_basics = compute_month_basics(df_month)
    year_totals = compute_totals(df_year)
    year_timeseries = build_year_timeseries(df_year)

    month_forecast = predict_end_of_month(df_month)
    year_forecast = predict_end_of_year(df_year)

    # --- Alertes ---
    alerts = []
    if not df_month.empty:
        dep = df_month[df_month["type"] == "OUT"].copy()
        dep["abs"] = dep["montant"].abs()
        top3 = (
            dep.groupby("categorie")["abs"].sum().sort_values(ascending=False).head(3)
        )
        for cat, val in top3.items():
            alerts.append(
                {
                    "type": "spend",
                    "text": f"{cat} pèse <b>{val:,.0f} €</b> ce mois-ci".replace(
                        ",", " "
                    ),
                }
            )

    # --- Couleur solde ---
    solde_val = float(month_basics["solde"])
    solde_class = (
        "solde-green"
        if solde_val > 2000
        else (
            "solde-blue"
            if solde_val >= 0
            else "solde-orange" if solde_val >= -1000 else "solde-red"
        )
    )

    # --- VUE MENSUELLE ---
    if st.session_state.budget_view_mode == "mois":
        header_html = f"""
        <div class="header-wrapper" style="margin-top:8px;">
            <div class="header-stat-card">
                <div class="header-stat-label">Revenus du mois</div>
                <div class="header-stat-value normal">{month_basics['revenus']:.0f} €</div>
            </div>
            <div class="header-stat-card">
                <div class="header-stat-label">Dépenses du mois</div>
                <div class="header-stat-value normal">{month_basics['depenses']:.0f} €</div>
            </div>
            <div class="header-stat-card">
                <div class="header-stat-label">
                    Solde du mois
                    <span class="header-solde-note-inline">(au {date.today().strftime("%d/%m/%Y")})</span>
                </div>
                <div class="header-stat-value {solde_class}">{solde_val:,.0f} €</div>
            </div>
        </div>
        """.replace(
            ",", " "
        )
        st.markdown(header_html, unsafe_allow_html=True)

        left_col, right_col = st.columns([1, 1])
        with left_col:
            st.markdown("#### À surveiller ce mois-ci")
            if not alerts:
                st.success("Aucune alerte prioritaire ce mois-ci. Continue comme ça")
            else:
                for alert in alerts:
                    st.markdown(
                        f'<div class="alert-row spend">{alert["text"]}</div>',
                        unsafe_allow_html=True,
                    )
                if len(alerts) > 1:
                    st.caption("Les montants les plus lourds apparaissent en premier.")

        with right_col:
            st.markdown("#### Où part ton argent ? (règle 50 / 30 / 20)")
            revenu_mois = float(month_basics.get("revenus", 0.0))
            part_besoins_pct = float(s["split"].get("50", 0.0))
            part_envies_pct = float(s["split"].get("30", 0.0))
            part_epargne_pct = float(s["split"].get("20", 0.0))
            besoins_eur = revenu_mois * (part_besoins_pct / 100.0)
            envies_eur = revenu_mois * (part_envies_pct / 100.0)
            epargne_eur = revenu_mois * (part_epargne_pct / 100.0)

            def status_class_for_spend(pct, target, soft_cap):
                if pct <= target:
                    return "ok"
                if pct <= soft_cap:
                    return "warn"
                return "bad"

            def status_class_for_saving(pct, target_min, warn_min):
                if pct >= target_min:
                    return "ok"
                if pct >= warn_min:
                    return "warn"
                return "bad"

            status_besoins = status_class_for_spend(part_besoins_pct, 50, 55)
            status_envies = status_class_for_spend(part_envies_pct, 30, 35)
            status_epargne = status_class_for_saving(part_epargne_pct, 20, 10)

            st.markdown(
                f"""
                <div class="budget-card {status_besoins}" style="margin-bottom:8px;">
                    <div style="font-size:0.9rem; line-height:1.3;">
                        <span style="font-weight:600;">Besoins</span> • <span style="font-weight:700;">{part_besoins_pct:.1f}% • {besoins_eur:,.0f} €</span>
                    </div>
                    <div class="subline" style="margin-top:4px;">Objectif 50% max</div>
                </div>
            """.replace(
                    ",", " "
                ),
                unsafe_allow_html=True,
            )

            st.markdown(
                f"""
                <div class="budget-card {status_envies}" style="margin-bottom:8px;">
                    <div style="font-size:0.9rem; line-height:1.3;">
                        <span style="font-weight:600;">Envies</span> • <span style="font-weight:700;">{part_envies_pct:.1f}% • {envies_eur:,.0f} €</span>
                    </div>
                    <div class="subline" style="margin-top:4px;">Objectif 30% max</div>
                </div>
            """.replace(
                    ",", " "
                ),
                unsafe_allow_html=True,
            )

            st.markdown(
                f"""
                <div class="budget-card {status_epargne}">
                    <div style="font-size:0.9rem; line-height:1.3;">
                        <span style="font-weight:600;">Épargne</span> • <span style="font-weight:700;">{part_epargne_pct:.1f}% • {epargne_eur:,.0f} €</span>
                    </div>
                    <div class="subline" style="margin-top:4px;">Objectif 20% ou plus</div>
                </div>
            """.replace(
                    ",", " "
                ),
                unsafe_allow_html=True,
            )

        st.divider()

        st.markdown(
            f"### Dépenses {month_options[0]['label'] if month_options else 'Mois'} (vue graphique)"
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Top postes de dépenses**")
            if not s["by_cat"].empty:
                df_cat = s["by_cat"].copy().sort_values("montant", ascending=True)
                fig_cat = px.bar(df_cat, x="montant", y="categorie", orientation="h")
                fig_cat.update_layout(
                    margin=dict(l=20, r=10, t=20, b=20),
                    xaxis_title="Montant (€)",
                    yaxis_title="",
                    showlegend=False,
                )
                st.plotly_chart(fig_cat, use_container_width=True)
            else:
                st.info("Ajoute des données pour voir la répartition.")

        with c2:
            st.markdown("**Répartition des dépenses**")
            if not df_month.empty:
                depenses_mois = df_month[df_month["type"] == "OUT"].copy()
                donut_df = (
                    depenses_mois.groupby("categorie")["montant"].sum().reset_index()
                )
                donut_df["montant_abs"] = donut_df["montant"].abs()
                total_mois = donut_df["montant_abs"].sum()
                fig_donut = px.pie(
                    donut_df, names="categorie", values="montant_abs", hole=0.55
                )
                fig_donut.update_layout(
                    annotations=[
                        dict(
                            text=f"{total_mois:,.0f} €".replace(",", " "),
                            x=0.5,
                            y=0.5,
                            font=dict(size=16),
                            showarrow=False,
                        )
                    ],
                    showlegend=True,
                    margin=dict(l=20, r=20, t=20, b=20),
                )
                st.plotly_chart(fig_donut, use_container_width=True)
                st.caption("Au centre : total des dépenses du mois.")
            else:
                st.info("Aucune dépense saisie pour le mois courant.")

        st.divider()

        if st.session_state.budget_selected_month_key == today_key:
            st.markdown("### Si tu continues comme ça…")
            p1, p2, p3 = st.columns(3)
            p1.metric("Fin de mois — Revenus", f"{month_forecast['revenus']:.0f} €")
            p2.metric("Fin de mois — Dépenses", f"{month_forecast['depenses']:.0f} €")
            p3.metric("Fin de mois — Solde", f"{month_forecast['solde']:.0f} €")
            st.caption("Projection basée sur ton rythme actuel.")
            st.divider()

        st.markdown("#### Ton coach")
        coach_md = build_coach_text(
            month_summary=month_basics,
            month_forecast=month_forecast,
            year_forecast=year_forecast,
            split=s["split"],
            top_alerts=[a["text"] for a in alerts][:3],
        )
        st.markdown(coach_md, unsafe_allow_html=True)
        st.divider()

    # --- VUE ANNUELLE ---
    else:
        header_year_html = f"""
        <div class="header-wrapper" style="margin-top:8px;">
            <div class="header-stat-card">
                <div class="header-stat-label">Revenus cumulés {target_year}</div>
                <div class="header-stat-value normal">{year_totals['revenus']:.0f} €</div>
            </div>
            <div class="header-stat-card">
                <div class="header-stat-label">Dépenses cumulées {target_year}</div>
                <div class="header-stat-value normal">{year_totals['depenses']:.0f} €</div>
            </div>
            <div class="header-stat-card">
                <div class="header-stat-label">Solde {target_year}</div>
                <div class="header-stat-value {'solde-green' if year_totals['solde'] >= 0 else 'solde-red'}">
                    {year_totals['solde']:,.0f} €
                </div>
            </div>
        </div>
        """.replace(
            ",", " "
        )
        st.markdown(header_year_html, unsafe_allow_html=True)

        st.divider()

        st.markdown(f"### Revenus vs Dépenses {target_year} (par mois)")
        if not year_timeseries.empty:
            fig_year = px.line(
                year_timeseries,
                x="mois_label",
                y=["revenus", "depenses"],
                markers=True,
                labels={
                    "mois_label": "Mois",
                    "value": "Montant (€)",
                    "variable": "Série",
                },
            )
            fig_year.update_layout(
                legend_title_text="", margin=dict(l=20, r=20, t=20, b=20)
            )
            st.plotly_chart(fig_year, use_container_width=True)
            st.caption("Compare tes revenus et dépenses mois par mois.")
        else:
            st.info(f"Aucune donnée pour {target_year}.")

        st.divider()

        st.markdown("#### Ton coach (vision annuelle)")
        coach_year_md = build_coach_text_year(
            df_year=df_year,
            year_totals=year_totals,
            s_year_split=s_year["split"],
            year_forecast=year_forecast,
            target_year=target_year,
        )
        st.markdown(coach_year_md, unsafe_allow_html=True)
        st.divider()

    # === Simulateur d'épargne ===
    with st.expander("Simulation rapide d’épargne", expanded=False):
        c = st.columns(4)
        with c[0]:
            monthly = st.number_input(
                "Mensualité (€)", min_value=0.0, step=10.0, value=100.0
            )
        with c[1]:
            rate = st.number_input(
                "Taux annuel (%)", min_value=0.0, step=0.1, value=3.0
            )
        with c[2]:
            start = st.number_input(
                "Solde initial (€)", min_value=0.0, step=50.0, value=0.0
            )
        with c[3]:
            horizon = st.selectbox(
                "Horizon", [1, 3, 5, 10], index=2, format_func=lambda x: f"{x} an(s)"
            )
        v1 = savings_projection(monthly, rate, 1, start)
        vH = savings_projection(monthly, rate, int(horizon), start)
        s1, sH = st.columns(2)
        s1.metric("Dans 1 an", f"{v1:,.0f} €".replace(",", " "))
        sH.metric(f"Dans {horizon} an(s)", f"{vH:,.0f} €".replace(",", " "))
