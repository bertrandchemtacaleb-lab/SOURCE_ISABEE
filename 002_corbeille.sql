-- =====================================================================
-- Migration 002_corbeille
-- =====================================================================
-- Documentation de la migration appliquee par
-- database._migrer_schema_v2_vers_v3() (et, pour une base creee a
-- neuf, directement par schema.sql). Ce fichier n'est pas execute
-- automatiquement (voir la note dans 001_initial.sql) : il decrit
-- fidelement ce que fait le code Python correspondant.
--
-- Objectif : suppression reversible des documents (corbeille), au
-- lieu d'une suppression immediate et definitive comme en V2.
-- =====================================================================

ALTER TABLE subjects ADD COLUMN supprime INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subjects ADD COLUMN supprime_le TEXT;
ALTER TABLE subjects ADD COLUMN supprime_par INTEGER;

CREATE INDEX IF NOT EXISTS idx_subjects_supprime ON subjects(supprime);

-- Note : sur une base creee a neuf (schema.sql), la colonne supprime
-- porte en plus une contrainte CHECK (supprime IN (0, 1)) et
-- supprime_par une contrainte de cle etrangere vers users(id). Ces
-- contraintes ne sont pas re-appliquees retroactivement sur une base
-- V2 deja en production (SQLite ne permet pas d'ajouter une
-- contrainte CHECK ou FOREIGN KEY a une colonne existante sans
-- reconstruire la table) ; la validation des valeurs (0 ou 1
-- uniquement) reste alors garantie au niveau applicatif, par
-- archive_manager.py, exactement comme pour les colonnes ajoutees
-- lors de la migration V1 -> V2 (voir database.py).
