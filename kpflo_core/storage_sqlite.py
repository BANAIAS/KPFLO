# kpflo_core/storage_sqlite.py
# ---------------------------------------------------------
# Accès SQLite : schéma, migrations, users, transactions,
# exports CSV et utilitaires (batch insert, mois glissants).
# ---------------------------------------------------------

from __future__ import annotations

import io
import os
import sqlite3
from calendar import monthrange
from datetime import date, datetime
from typing import Optional, List, Dict, Tuple

import bcrypt
import pandas as pd

DB_PATH = os.path.join("data", "kpflo.db")


# ---------- Connexion & introspection ----------


def _connect() -> sqlite3.Connection:
    """Ouvre une connexion SQLite (et crée le dossier data si besoin)."""
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _table_has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    """Vérifie l’existence d’une colonne dans une table."""
    cur = con.execute(f"PRAGMA table_info({table});")
    return any(row[1] == col for row in cur.fetchall())


# ---------- Initialisation / migrations ----------


def init_db() -> None:
    """Crée les tables/colonnes manquantes et rattache un user par défaut."""
    os.makedirs("data", exist_ok=True)
    with _connect() as con:
        # users
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                prenom TEXT,
                email TEXT,
                password_hash TEXT,
                color TEXT,
                created_at TEXT NOT NULL
            );
        """
        )

        # migrations users
        if not _table_has_column(con, "users", "nom"):
            if _table_has_column(con, "users", "name"):
                con.execute("ALTER TABLE users RENAME COLUMN name TO nom;")
            else:
                con.execute("ALTER TABLE users ADD COLUMN nom TEXT;")
        if not _table_has_column(con, "users", "prenom"):
            con.execute("ALTER TABLE users ADD COLUMN prenom TEXT;")
        if not _table_has_column(con, "users", "email"):
            con.execute("ALTER TABLE users ADD COLUMN email TEXT;")
        if not _table_has_column(con, "users", "password_hash"):
            con.execute("ALTER TABLE users ADD COLUMN password_hash TEXT;")
        if not _table_has_column(con, "users", "color"):
            con.execute("ALTER TABLE users ADD COLUMN color TEXT;")
        if not _table_has_column(con, "users", "created_at"):
            con.execute("ALTER TABLE users ADD COLUMN created_at TEXT;")

        con.execute("CREATE INDEX IF NOT EXISTS idx_users_nom ON users(nom);")

        # transactions
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                type TEXT CHECK(type IN ('IN','OUT')) NOT NULL,
                categorie TEXT NOT NULL,
                libelle TEXT NOT NULL,
                montant REAL NOT NULL,
                recurrent INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
        """
        )

        # migration: user_id
        if not _table_has_column(con, "transactions", "user_id"):
            con.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER;")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, date);"
        )

        # backfill password_hash vides
        default_hash = bcrypt.hashpw("default123".encode(), bcrypt.gensalt()).decode()
        con.execute(
            "UPDATE users SET password_hash = COALESCE(password_hash, ?) "
            "WHERE password_hash IS NULL OR password_hash = '';",
            (default_hash,),
        )
        con.commit()

        # user par défaut
        uid = ensure_default_user(con)

        # rattache l’historique sans user
        con.execute(
            "UPDATE transactions SET user_id = ? WHERE user_id IS NULL;", (uid,)
        )
        con.commit()


def ensure_default_user(con: sqlite3.Connection | None = None) -> int:
    """Retourne l’id du user 'Mon budget' (le crée si absent)."""
    close = False
    if con is None:
        con = _connect()
        close = True
    try:
        cur = con.execute("SELECT id FROM users WHERE nom = ?;", ("Mon budget",))
        row = cur.fetchone()
        if row:
            return row[0]
        now = datetime.utcnow().isoformat()
        hash_pw = bcrypt.hashpw("default123".encode(), bcrypt.gensalt()).decode()
        con.execute(
            "INSERT INTO users(nom, created_at, password_hash) VALUES(?, ?, ?);",
            ("Mon budget", now, hash_pw),
        )
        con.commit()
        cur = con.execute("SELECT id FROM users WHERE nom = ?;", ("Mon budget",))
        return cur.fetchone()[0]
    finally:
        if close:
            con.close()


# ---------- Users ----------


def create_user(nom: str, prenom: str = "", email: str = "", password: str = "") -> int:
    """Crée un utilisateur et retourne son id."""
    nom = str(nom).strip()
    if not nom:
        raise ValueError("Nom utilisateur vide.")
    if not password:
        raise ValueError("Mot de passe requis.")
    hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with _connect() as con:
        now = datetime.utcnow().isoformat()
        con.execute(
            "INSERT INTO users(nom, prenom, email, password_hash, created_at) VALUES(?, ?, ?, ?, ?);",
            (nom, prenom.strip(), email.strip(), hash_pw, now),
        )
        con.commit()
        cur = con.execute("SELECT id FROM users WHERE nom = ?;", (nom,))
        return cur.fetchone()[0]


def validate_user(nom: str, password: str) -> Optional[int]:
    """Vérifie le couple (nom, mot de passe) et retourne l’id si OK."""
    with _connect() as con:
        cur = con.execute("SELECT id, password_hash FROM users WHERE nom = ?;", (nom,))
        row = cur.fetchone()
        if row and bcrypt.checkpw(password.encode(), row[1].encode()):
            return row[0]
    return None


def list_users() -> pd.DataFrame:
    """Liste les utilisateurs (id, nom, prénom, email, color, created_at)."""
    with _connect() as con:
        df = pd.read_sql_query(
            "SELECT id, nom, prenom, email, color, created_at FROM users ORDER BY nom ASC;",
            con,
        )
    return df


def rename_user(
    user_id: int,
    new_nom: str,
    new_prenom: str | None = None,
    new_email: str | None = None,
) -> None:
    """Met à jour le nom (et optionnellement prénom/email) d’un utilisateur."""
    new_nom = str(new_nom).strip()
    if not new_nom:
        raise ValueError("Nouveau nom vide.")
    with _connect() as con:
        updates, params = ["nom = ?"], [new_nom]
        if new_prenom is not None:
            updates.append("prenom = ?")
            params.append(new_prenom.strip())
        if new_email is not None:
            updates.append("email = ?")
            params.append(new_email.strip())
        sql = f"UPDATE users SET {', '.join(updates)} WHERE id=?;"
        params.append(user_id)
        con.execute(sql, params)
        con.commit()


def delete_user(user_id: int) -> None:
    """Supprime un utilisateur sans transactions, sinon lève une erreur."""
    with _connect() as con:
        cur = con.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id=?;", (user_id,)
        )
        if cur.fetchone()[0] > 0:
            raise RuntimeError(
                "Impossible de supprimer un utilisateur qui a des transactions."
            )
        con.execute("DELETE FROM users WHERE id=?;", (user_id,))
        con.commit()


# ---------- Transactions (CRUD) ----------


def insert_transaction(
    date_val,
    type_val: str,
    categorie: str,
    libelle: str,
    montant: float,
    recurrent: bool = False,
    user_id: Optional[int] = None,
) -> None:
    """Insère une transaction (IN/OUT) pour un utilisateur (par défaut : 'Mon budget')."""
    if type_val not in ("IN", "OUT"):
        raise ValueError("type doit être 'IN' ou 'OUT'.")
    if not libelle or not str(libelle).strip():
        raise ValueError("libellé vide.")
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        con.execute(
            """
            INSERT INTO transactions(date, type, categorie, libelle, montant, recurrent, created_at, user_id)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(pd.to_datetime(date_val).date()),
                type_val,
                str(categorie),
                str(libelle).strip(),
                float(montant),
                1 if recurrent else 0,
                datetime.utcnow().isoformat(),
                int(user_id),
            ),
        )
        con.commit()


def fetch_all_df(user_id: Optional[int] = None) -> pd.DataFrame:
    """Récupère les transactions d’un user (tri date DESC)."""
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        df = pd.read_sql_query(
            "SELECT date, type, categorie, libelle, montant, recurrent "
            "FROM transactions WHERE user_id=? ORDER BY date DESC, rowid DESC;",
            con,
            params=(int(user_id),),
            parse_dates=["date"],
        )
    if not df.empty:
        df["recurrent"] = df["recurrent"].astype(bool)
    return df


def export_csv(
    path: str = "data/kpflo_export.csv", user_id: Optional[int] = None
) -> str:
    """Exporte les transactions d’un user en CSV et retourne le chemin."""
    df = fetch_all_df(user_id=user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    return path


def delete_transaction(tx_id: int, user_id: Optional[int] = None) -> None:
    """Supprime une transaction par id (sécurisée par user_id)."""
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        con.execute(
            "DELETE FROM transactions WHERE id = ? AND user_id = ?;",
            (tx_id, int(user_id)),
        )
        con.commit()


def update_transaction(
    tx_id: int,
    date_val,
    type_val: str,
    categorie: str,
    libelle: str,
    montant: float,
    recurrent: bool = False,
    user_id: Optional[int] = None,
) -> None:
    """Met à jour une transaction existante (sécurisée par user_id)."""
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        con.execute(
            """
            UPDATE transactions SET date=?, type=?, categorie=?, libelle=?, montant=?, recurrent=?
            WHERE id = ? AND user_id = ?;
            """,
            (
                str(pd.to_datetime(date_val).date()),
                type_val,
                str(categorie),
                str(libelle),
                float(montant),
                1 if recurrent else 0,
                tx_id,
                int(user_id),
            ),
        )
        con.commit()


def fetch_all_df_with_id(user_id: Optional[int] = None) -> pd.DataFrame:
    """Version avec id (utile pour l’édition ciblée)."""
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        df = pd.read_sql_query(
            "SELECT id, date, type, categorie, libelle, montant, recurrent "
            "FROM transactions WHERE user_id=? ORDER BY date DESC;",
            con,
            params=(int(user_id),),
            parse_dates=["date"],
        )
    if not df.empty:
        df["recurrent"] = df["recurrent"].astype(bool)
    return df


# ---------- Utilitaires (batch / calendrier / export unifié) ----------


def bulk_insert_transactions(rows: List[Tuple]) -> None:
    """Insère en lot des transactions via executemany."""
    if not rows:
        return
    con = _connect()
    try:
        cur = con.cursor()
        cur.executemany(
            """
            INSERT INTO transactions
            (date, type, categorie, libelle, montant, recurrent, created_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        con.commit()
    finally:
        con.close()


def month_iter(end_month: date, months: int = 36) -> List[date]:
    """Retourne une liste de 1ers du mois glissants sur N mois (fin incluse)."""
    end = date(end_month.year, end_month.month, 1)
    from dateutil.relativedelta import (
        relativedelta,
    )  # import local pour éviter les cycles

    return [end - relativedelta(months=i) for i in range(months - 1, -1, -1)]


def _users_map_sqlite() -> Dict[int, str]:
    """Construit {user_id: 'Prénom Nom'} depuis la base."""
    dfu = list_users()
    return {
        int(r["id"]): (f'{r.get("prenom","")} {r["nom"]}').strip() or f'ID {r["id"]}'
        for _, r in dfu.iterrows()
    }


def build_export_or_template_csv_sqlite(months: int = 36) -> bytes:
    """Exporte toutes transactions (tous users) ou génère un modèle 36 mois."""
    users_map = _users_map_sqlite()
    rows: List[Dict] = []
    non_empty = False

    for uid in users_map.keys():
        df = fetch_all_df(user_id=uid)  # date,type,categorie,libelle,montant,recurrent
        if df is not None and not df.empty:
            non_empty = True
            for _, r in df.iterrows():
                rows.append(
                    {
                        "user_id": uid,
                        "user_name": users_map[uid],
                        "date": pd.to_datetime(r["date"]).date(),
                        "kind": (
                            "revenu" if str(r["type"]).upper() == "IN" else "depense"
                        ),
                        "category": r.get("categorie", ""),
                        "label": r.get("libelle", ""),
                        "amount": float(r.get("montant", 0) or 0),
                    }
                )

    if not non_empty:
        months_list = month_iter(date.today(), months=months)
        for uid, uname in users_map.items():
            for d in months_list:
                for kind in ("revenu", "depense", "epargne"):
                    rows.append(
                        {
                            "user_id": uid,
                            "user_name": uname,
                            "date": d,
                            "kind": kind,
                            "category": "" if kind != "epargne" else "Épargne",
                            "label": "",
                            "amount": 0.0,
                        }
                    )

    df = pd.DataFrame(
        rows,
        columns=["user_id", "user_name", "date", "kind", "category", "label", "amount"],
    )
    df["date"] = pd.to_datetime(df["date"]).dt.date
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
