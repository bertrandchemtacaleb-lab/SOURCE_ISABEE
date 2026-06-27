-- =====================================================================
-- Migration 003_paiement_mobile_money
-- =====================================================================
-- Documentation de la migration appliquee par
-- database._migrer_schema_v2_vers_v3() et schema.sql. Ce fichier
-- n'est pas execute automatiquement (voir la note dans
-- 001_initial.sql).
--
-- Objectif : permettre le paiement par Mobile Money (Orange Money,
-- MTN MoMo), en complement du paiement en presentiel (jamais en
-- remplacement). Aucun numero ni titulaire n'est code en dur : ils
-- sont entierement configurables depuis l'interface d'administration
-- (voir admin.page_gestion_moyens_paiement et payments.py).
-- =====================================================================

ALTER TABLE payments ADD COLUMN operateur TEXT;
ALTER TABLE payments ADD COLUMN capture_preuve TEXT;

CREATE TABLE IF NOT EXISTS payment_methods (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nom_affiche         TEXT    NOT NULL,
    operateur           TEXT    NOT NULL CHECK (operateur IN ('orange_money', 'mtn_momo', 'autre')),
    titulaire           TEXT    NOT NULL,
    numero              TEXT    NOT NULL,
    actif               INTEGER NOT NULL DEFAULT 1 CHECK (actif IN (0, 1)),
    ordre_affichage     INTEGER NOT NULL DEFAULT 0,
    modifie_par         INTEGER,
    date_modification   TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (modifie_par) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_methods_actif ON payment_methods(actif);

-- Note : la colonne payments.operateur ne porte pas de contrainte
-- CHECK (ni sur une base migree, ni sur une base neuve) afin de ne
-- jamais bloquer une ligne de paiement presentiel existante
-- (operateur = NULL) ; sa validation ('orange_money' ou 'mtn_momo'
-- uniquement lorsqu'elle est utilisee) est assuree au niveau
-- applicatif par payments.demander_paiement_mobile_money.
