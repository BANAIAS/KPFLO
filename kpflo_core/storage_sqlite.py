# kpflo_core/storage_sqlite.py
import sqlite3
import os
from datetime import datetime
import pandas as pd
from typing import Optional
import bcrypt  # Pour hashing passwords

DB_PATH = os.path.join("data", "kpflo.db")

def _connect():
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect(DB_PATH)

def _table_has_column(con, table, col) -> bool:
    cur = con.execute(f"PRAGMA table_info({table});")
    return any(row[1] == col for row in cur.fetchall())

def init_db():
    os.makedirs("data", exist_ok=True)
    with _connect() as con:
        # 1) users - Création avec nouvelles colonnes
        con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            prenom TEXT,
            email TEXT,
            password_hash TEXT,
            color TEXT,
            created_at TEXT NOT NULL
        );
        """)

        # Migration colonnes manquantes pour users
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

        # Index sur nom
        con.execute("CREATE INDEX IF NOT EXISTS idx_users_nom ON users(nom);")

        # 2) transactions
        con.execute("""
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
        """)

        # 3) migration : ajouter user_id
        if not _table_has_column(con, "transactions", "user_id"):
            con.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER;")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, date);")
        else:
            con.execute("CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, date);")

        # Pour users sans password_hash
        default_hash = bcrypt.hashpw("default123".encode(), bcrypt.gensalt()).decode()
        con.execute("""
        UPDATE users SET password_hash = COALESCE(password_hash, ?)
        WHERE password_hash IS NULL OR password_hash = '';
        """, (default_hash,))
        con.commit()  # Commit pour users

        # 4) ensure default user
        uid = ensure_default_user(con)

        # 5) rattacher historique
        con.execute("UPDATE transactions SET user_id = ? WHERE user_id IS NULL;", (uid,))
        con.commit()  # Commit pour transactions

def ensure_default_user(con=None):
    close = False
    if con is None:
        con = _connect()
        close = True
    try:
        cur = con.execute("SELECT id FROM users WHERE nom = ?;", ("Mon budget",))
        row = cur.fetchone()
        if row:
            return row[0]
        # Create default
        now = datetime.utcnow().isoformat()
        default_pw = "default123"
        hash_pw = bcrypt.hashpw(default_pw.encode(), bcrypt.gensalt()).decode()
        con.execute("INSERT INTO users(nom, created_at, password_hash) VALUES(?, ?, ?);", ("Mon budget", now, hash_pw))
        con.commit()  # Commit explicite
        cur = con.execute("SELECT id FROM users WHERE nom = ?;", ("Mon budget",))
        return cur.fetchone()[0]
    finally:
        if close:
            con.close()

def create_user(nom: str, prenom: str = "", email: str = "", password: str = ""):
    nom = str(nom).strip()
    if not nom:
        raise ValueError("Nom utilisateur vide.")
    if not password:
        raise ValueError("Mot de passe requis.")
    hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with _connect() as con:
        now = datetime.utcnow().isoformat()
        con.execute("""
        INSERT INTO users(nom, prenom, email, password_hash, created_at)
        VALUES(?, ?, ?, ?, ?);
        """, (nom, prenom.strip(), email.strip(), hash_pw, now))
        con.commit()  # Commit explicite
        cur = con.execute("SELECT id FROM users WHERE nom = ?;", (nom,))
        return cur.fetchone()[0]

def validate_user(nom: str, password: str) -> Optional[int]:
    with _connect() as con:
        cur = con.execute("SELECT id, password_hash FROM users WHERE nom = ?;", (nom,))
        row = cur.fetchone()
        if row and bcrypt.checkpw(password.encode(), row[1].encode()):
            return row[0]
    return None

def list_users() -> pd.DataFrame:
    with _connect() as con:
        df = pd.read_sql_query(
            "SELECT id, nom, prenom, email, color, created_at FROM users ORDER BY nom ASC;",
            con
        )
    return df

def rename_user(user_id: int, new_nom: str, new_prenom: str = None, new_email: str = None):
    new_nom = str(new_nom).strip()
    if not new_nom:
        raise ValueError("Nouveau nom vide.")
    with _connect() as con:
        updates = ["nom = ?"]
        params = [new_nom]
        if new_prenom is not None:
            updates.append("prenom = ?")
            params.append(new_prenom.strip())
        if new_email is not None:
            updates.append("email = ?")
            params.append(new_email.strip())
        sql = f"UPDATE users SET {', '.join(updates)} WHERE id=?;"
        params.append(user_id)
        con.execute(sql, params)
        con.commit()  # Commit explicite

def delete_user(user_id: int):
    with _connect() as con:
        cur = con.execute("SELECT COUNT(*) FROM transactions WHERE user_id=?;", (user_id,))
        n = cur.fetchone()[0]
        if n > 0:
            raise RuntimeError("Impossible de supprimer un utilisateur qui a des transactions.")
        con.execute("DELETE FROM users WHERE id=?;", (user_id,))
        con.commit()  # Commit explicite

def insert_transaction(date_val, type_val, categorie, libelle, montant, recurrent=False, user_id: Optional[int] = None):
    if type_val not in ("IN", "OUT"):
        raise ValueError("type doit être 'IN' ou 'OUT'.")
    if not libelle or not str(libelle).strip():
        raise ValueError("libellé vide.")
    if user_id is None:
        with _connect() as con:
            user_id = ensure_default_user(con)
    with _connect() as con:
        con.execute("""
        INSERT INTO transactions(date, type, categorie, libelle, montant, recurrent, created_at, user_id)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(pd.to_datetime(date_val).date()),
            type_val,
            str(categorie),
            str(libelle).strip(),
            float(montant),
            1 if recurrent else 0,
            datetime.utcnow().isoformat(),
            int(user_id),
        ))
        con.commit()  # Commit explicite

def fetch_all_df(user_id: Optional[int] = None) -> pd.DataFrame:
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        df = pd.read_sql_query(
            "SELECT date, type, categorie, libelle, montant, recurrent FROM transactions WHERE user_id=? ORDER BY date DESC, rowid DESC;",
            con, params=(int(user_id),), parse_dates=["date"]
        )
    if not df.empty:
        df["recurrent"] = df["recurrent"].astype(bool)
    return df

def export_csv(path="data/kpflo_export.csv", user_id: Optional[int] = None):
    df = fetch_all_df(user_id=user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    return path

def delete_transaction(tx_id: int, user_id: Optional[int] = None):
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        con.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?;", (tx_id, int(user_id)))
        con.commit()  # Commit explicite

def update_transaction(tx_id: int, date_val, type_val, categorie, libelle, montant, recurrent=False, user_id: Optional[int] = None):
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        con.execute("""
        UPDATE transactions SET date=?, type=?, categorie=?, libelle=?, montant=?, recurrent=?
        WHERE id = ? AND user_id = ?;
        """, (str(pd.to_datetime(date_val).date()), type_val, categorie, libelle, float(montant), 1 if recurrent else 0, tx_id, int(user_id)))
        con.commit()  # Commit explicite

def fetch_all_df_with_id(user_id: Optional[int] = None) -> pd.DataFrame:
    """Version avec id pour edits"""
    with _connect() as con:
        if user_id is None:
            user_id = ensure_default_user(con)
        df = pd.read_sql_query(
            "SELECT id, date, type, categorie, libelle, montant, recurrent FROM transactions WHERE user_id=? ORDER BY date DESC;",
            con, params=(int(user_id),), parse_dates=["date"]
        )
    if not df.empty:
        df["recurrent"] = df["recurrent"].astype(bool)
    return df