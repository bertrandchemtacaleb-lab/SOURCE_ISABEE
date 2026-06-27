-- =====================================================================
-- Migration 004_mot_de_passe_oublie
-- =====================================================================
-- Documentation de la migration appliquee par schema.sql (table
-- entierement nouvelle : aucune colonne a ajouter a une table
-- existante, donc rien a faire cote _migrer_schema_v2_vers_v3 --
-- CREATE TABLE IF NOT EXISTS est deja naturellement idempotent). Ce
-- fichier n'est pas execute automatiquement (voir la note dans
-- 001_initial.sql).
--
-- Objectif : permettre a un utilisateur de reinitialiser son mot de
-- passe via un jeton a usage unique et a duree de vie limitee, sans
-- intervention d'un administrateur (voir auth.py et page_connexion,
-- onglet "Mot de passe oublie").
-- =====================================================================

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    jeton_hash          TEXT    NOT NULL,
    date_creation       TEXT    NOT NULL DEFAULT (datetime('now')),
    date_expiration     TEXT    NOT NULL,
    utilise             INTEGER NOT NULL DEFAULT 0 CHECK (utilise IN (0, 1)),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens(user_id);

-- Note de securite : seul le hachage SHA-256 du jeton est stocke
-- (colonne jeton_hash), jamais le jeton en clair, sur le meme
-- principe que les mots de passe eux-memes. Le jeton n'est donc
-- retrouvable qu'au moment de sa generation
-- (auth.demander_reinitialisation_mot_de_passe), jamais ensuite.
