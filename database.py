"""
database.py
-----------
Role : couche d'acces unique a la base de donnees SQLite.

Ce module est le seul point d'entree autorise vers le fichier .db.
Aucun autre module ne doit ouvrir une connexion sqlite3 directement :
tous passent par get_connection() ou par les fonctions execute_*
definies ici. Cela garantit un comportement homogene (cles etrangeres
actives, formats de date, gestion des erreurs) et facilite la migration
future vers un autre moteur (PostgreSQL, par exemple) si la plateforme
doit etre deployee a plus grande echelle.

Nouveau en V2 : migration automatique et non destructive des bases
V1 deja en production (voir _migrer_schema_v1_vers_v2), et dossier de
stockage des photos de profil (PHOTOS_DIR).

Nouveau en V3 : migration V2 -> V3 (_migrer_schema_v2_vers_v3, voir
audit-isabee-v2.md : corbeille, paiement Mobile Money), dossier de
stockage des preuves de paiement (PREUVES_DIR), et suivi des versions
de schema appliquees (table schema_versions, voir
_enregistrer_version et database/migrations/).
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "source_isabee.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
DOCUMENTS_DIR = BASE_DIR / "data" / "documents"
PHOTOS_DIR = BASE_DIR / "data" / "photos"
PREUVES_DIR = BASE_DIR / "data" / "preuves_paiement"


def _ensure_directories() -> None:
    """Cree les dossiers de donnees s'ils n'existent pas encore."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    PREUVES_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_connection():
    """
    Fournit une connexion SQLite configuree correctement.

    A utiliser systematiquement avec un bloc 'with' :

        with get_connection() as conn:
            conn.execute(...)

    Le commit est realise automatiquement a la sortie du bloc si aucune
    exception n'a ete levee ; en cas d'erreur, un rollback est effectue.
    """
    _ensure_directories()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _colonnes_existantes(conn: sqlite3.Connection, table: str) -> set[str]:
    """
    Liste les colonnes actuelles d'une table. Utilise un acces
    positionnel (ligne[1]) plutot que par nom de colonne, afin de
    fonctionner quelle que soit la configuration de row_factory sur
    la connexion recue en parametre (cette fonction ne presuppose pas
    que l'appelant a regle conn.row_factory = sqlite3.Row).
    """
    return {ligne[1] for ligne in conn.execute(f"PRAGMA table_info({table})")}


def _migrer_schema_v1_vers_v2(conn: sqlite3.Connection) -> None:
    """
    Ajoute aux bases V1 deja existantes les colonnes introduites en V2,
    sans aucune perte de donnees. Idempotent : ne fait rien si les
    colonnes sont deja presentes, que ce soit parce que la base vient
    d'etre creee a neuf via schema.sql, ou parce que cette migration a
    deja ete executee a un demarrage precedent.

    Limite assumee : SQLite ne permet pas d'ajouter une contrainte
    CHECK a une table existante sans la reconstruire entierement.
    Les nouvelles contraintes definies dans schema.sql (cycle,
    type_acces...) s'appliquent donc pleinement aux bases creees a
    neuf, mais pas retroactivement aux lignes d'une base V1 migree.
    La validation cote application (models.py, formulaires de saisie)
    reste donc la garantie principale pour ces bases migrees.
    """
    colonnes_a_ajouter: dict[str, list[tuple[str, str]]] = {
        "users": [
            ("photo", "TEXT"),
            ("theme", "TEXT NOT NULL DEFAULT 'clair'"),
            ("langue", "TEXT NOT NULL DEFAULT 'fr'"),
        ],
        "subjects": [
            ("type_acces", "TEXT NOT NULL DEFAULT 'gratuit'"),
            ("prix", "INTEGER NOT NULL DEFAULT 0"),
            ("mode_paiement", "TEXT NOT NULL DEFAULT 'presentiel'"),
        ],
    }
    tables_existantes = {
        ligne[0] for ligne in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, colonnes in colonnes_a_ajouter.items():
        if table not in tables_existantes:
            continue
        existantes = _colonnes_existantes(conn, table)
        for nom_colonne, definition in colonnes:
            if nom_colonne not in existantes:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {nom_colonne} {definition}")

    # Cet index porte sur une colonne qui n'existe pas forcement encore
    # au moment ou schema.sql s'execute sur une base V1 preexistante
    # (CREATE TABLE IF NOT EXISTS ne modifie pas une table deja
    # presente). Il est donc cree ici, une fois la colonne garantie
    # presente ci-dessus, plutot que dans schema.sql. Idempotent et
    # sans effet sur une base fraichement creee, ou la colonne existe
    # deja et l'index est simplement ignore (IF NOT EXISTS).
    if "subjects" in tables_existantes:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subjects_type_acces ON subjects(type_acces)")


def _migrer_schema_v2_vers_v3(conn: sqlite3.Connection) -> None:
    """
    Ajoute aux bases V2 deja existantes les colonnes introduites en V3
    (corbeille, paiement Mobile Money), sans aucune perte de donnees.
    Suit exactement le meme principe que _migrer_schema_v1_vers_v2 :
    idempotent, et n'agit que sur les colonnes manquantes des tables
    deja presentes.

    Les nouvelles tables de la V3 (payment_methods,
    password_reset_tokens, schema_versions) n'ont pas besoin de ce
    traitement : elles sont creees par schema.sql via
    CREATE TABLE IF NOT EXISTS, deja naturellement idempotent et sans
    risque sur une base existante.
    """
    colonnes_a_ajouter: dict[str, list[tuple[str, str]]] = {
        "subjects": [
            ("supprime", "INTEGER NOT NULL DEFAULT 0"),
            ("supprime_le", "TEXT"),
            ("supprime_par", "INTEGER"),
        ],
        "payments": [
            ("operateur", "TEXT"),
            ("capture_preuve", "TEXT"),
        ],
    }
    tables_existantes = {
        ligne[0] for ligne in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, colonnes in colonnes_a_ajouter.items():
        if table not in tables_existantes:
            continue
        existantes = _colonnes_existantes(conn, table)
        for nom_colonne, definition in colonnes:
            if nom_colonne not in existantes:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {nom_colonne} {definition}")

    if "subjects" in tables_existantes:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subjects_supprime ON subjects(supprime)")


def _enregistrer_version(conn: sqlite3.Connection, version: str, description: str) -> None:
    """
    Enregistre dans schema_versions qu'une migration a ete appliquee,
    sans erreur si elle l'avait deja ete a un demarrage precedent
    (INSERT OR IGNORE, la colonne version est UNIQUE). A appeler une
    fois la migration Python correspondante executee avec succes.
    """
    conn.execute(
        "INSERT OR IGNORE INTO schema_versions (version, description) VALUES (?, ?)",
        (version, description),
    )


def initialiser_base() -> None:
    """
    Cree les tables si elles n'existent pas, a partir de schema.sql,
    puis applique les migrations V1 -> V2 puis V2 -> V3 sur les
    tables deja existantes. Doit etre appelee une fois au demarrage
    de l'application (app.py).
    """
    _ensure_directories()
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.executescript(schema_sql)
        _migrer_schema_v1_vers_v2(conn)
        _migrer_schema_v2_vers_v3(conn)
        _enregistrer_version(
            conn, "001_initial",
            "Schema de reference V2 (point de depart du suivi de versions). "
            "Voir database/migrations/001_initial.sql.",
        )
        _enregistrer_version(
            conn, "002_corbeille",
            "Ajout de la corbeille (suppression reversible des documents). "
            "Voir database/migrations/002_corbeille.sql.",
        )
        _enregistrer_version(
            conn, "003_paiement_mobile_money",
            "Ajout des moyens de paiement Mobile Money configurables. "
            "Voir database/migrations/003_paiement_mobile_money.sql.",
        )
        _enregistrer_version(
            conn, "004_mot_de_passe_oublie",
            "Ajout de la reinitialisation de mot de passe par jeton. "
            "Voir database/migrations/004_mot_de_passe_oublie.sql.",
        )


def executer(requete: str, parametres: tuple = ()) -> int:
    """
    Execute une requete de modification (INSERT/UPDATE/DELETE).
    Retourne l'id de la derniere ligne inseree (utile pour les INSERT).
    """
    with get_connection() as conn:
        curseur = conn.execute(requete, parametres)
        return curseur.lastrowid


def recuperer_un(requete: str, parametres: tuple = ()) -> sqlite3.Row | None:
    """Execute une requete SELECT et retourne une seule ligne (ou None)."""
    with get_connection() as conn:
        return conn.execute(requete, parametres).fetchone()


def recuperer_tous(requete: str, parametres: tuple = ()) -> list[sqlite3.Row]:
    """Execute une requete SELECT et retourne toutes les lignes."""
    with get_connection() as conn:
        return conn.execute(requete, parametres).fetchall()
