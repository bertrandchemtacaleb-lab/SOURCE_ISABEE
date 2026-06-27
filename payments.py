"""
payments.py
-----------
Role : gerer le cycle de vie des paiements de ressources payantes.

Deux circuits de paiement, tous deux a validation manuelle par un
administrateur (aucune integration d'API de paiement automatisee) :

1. Presentiel (inchange depuis la V2) :
   a. l'utilisateur demande l'acces a un document payant (demander_paiement) ;
   b. il se rend au service competent et regle le montant en especes ;
   c. un administrateur constate l'encaissement et valide manuellement le
      paiement dans l'interface (valider_paiement), ce qui debloque le
      telechargement du document pour cet utilisateur uniquement.

2. Mobile Money (nouveau en V3, voir audit-isabee-v2.md) :
   a. l'utilisateur choisit un moyen de paiement configure (voir
      payment_methods / ajouter_moyen_paiement, jamais code en dur) et
      transfere le montant vers le numero affiche (un QR code facilite
      la saisie, voir qrcode_moyen_paiement) ;
   b. il joint une capture de la preuve de paiement
      (demander_paiement_mobile_money) ;
   c. un administrateur consulte la preuve et valide ou refuse
      manuellement (memes valider_paiement / refuser_paiement que pour
      le presentiel : un seul circuit de validation pour les deux
      canaux).

Ce module ne realise aucun affichage : il est consomme par app.py
(cote etudiant) et par admin.py (validation des paiements, gestion des
moyens de paiement Mobile Money).
"""

from datetime import datetime

from database import executer, recuperer_un, recuperer_tous
from models import Paiement, MoyenPaiement, OPERATEURS_PAIEMENT_VALIDES
from utils import journaliser, fichier_est_photo_valide, enregistrer_preuve_paiement, generer_qrcode_png

FORMAT_DATE_HEURE = "%Y-%m-%d %H:%M:%S"
OPERATEURS_MOBILE_MONEY = ("orange_money", "mtn_momo")


def demander_paiement(document_id: int, user_id: int) -> tuple[bool, str]:
    """
    Enregistre une demande de paiement pour un document payant.
    Le document reste indisponible au telechargement jusqu'a ce
    qu'un administrateur valide manuellement le paiement.
    """
    document = recuperer_un("SELECT * FROM subjects WHERE id = ?", (document_id,))
    if document is None:
        return False, "Document introuvable."
    if document["type_acces"] != "payant":
        return False, "Ce document est gratuit, aucun paiement n'est necessaire."

    existant = recuperer_un(
        "SELECT * FROM payments WHERE document_id = ? AND user_id = ?", (document_id, user_id)
    )
    if existant is not None:
        if existant["statut_paiement"] == "valide":
            return False, "Ce document a deja ete paye et valide."
        if existant["statut_paiement"] == "en_attente":
            return False, "Une demande de paiement est deja en attente de validation pour ce document."
        # statut "refuse" : on autorise une nouvelle demande en reactivant la ligne existante,
        # plutot que d'en creer une seconde (la table impose une seule ligne par couple document/utilisateur).
        executer(
            """
            UPDATE payments
            SET statut_paiement = 'en_attente', date_demande = ?,
                date_validation = NULL, valide_par = NULL, reference_caisse = NULL
            WHERE id = ?
            """,
            (datetime.now().strftime(FORMAT_DATE_HEURE), existant["id"]),
        )
        journaliser("Nouvelle demande de paiement", "succes", user_id=user_id, details=str(document_id))
        return True, "Nouvelle demande de paiement enregistree. Rendez-vous au service competent."

    executer(
        """
        INSERT INTO payments (document_id, user_id, montant, mode_paiement, statut_paiement)
        VALUES (?, ?, ?, 'presentiel', 'en_attente')
        """,
        (document_id, user_id, document["prix"]),
    )
    journaliser("Demande de paiement", "succes", user_id=user_id, details=str(document_id))
    return True, (
        "Demande de paiement enregistree. Rendez-vous au service competent pour regler "
        "le montant en presentiel, puis attendez la validation par un administrateur."
    )


def valider_paiement(paiement_id: int, valide_par: int, reference_caisse: str = "") -> tuple[bool, str]:
    """Valide manuellement un paiement, apres constat de l'encaissement en presentiel."""
    executer(
        """
        UPDATE payments
        SET statut_paiement = 'valide', date_validation = ?, valide_par = ?, reference_caisse = ?
        WHERE id = ?
        """,
        (datetime.now().strftime(FORMAT_DATE_HEURE), valide_par, reference_caisse or None, paiement_id),
    )
    journaliser("Validation paiement", "succes", user_id=valide_par, details=str(paiement_id))
    return True, "Paiement valide. Le document est desormais accessible a l'utilisateur."


def refuser_paiement(paiement_id: int, refuse_par: int) -> tuple[bool, str]:
    """Refuse un paiement (encaissement non constate ou litige)."""
    executer(
        """
        UPDATE payments SET statut_paiement = 'refuse', date_validation = ?, valide_par = ?
        WHERE id = ?
        """,
        (datetime.now().strftime(FORMAT_DATE_HEURE), refuse_par, paiement_id),
    )
    journaliser("Refus paiement", "succes", user_id=refuse_par, details=str(paiement_id))
    return True, "Paiement refuse."


def statut_paiement_utilisateur(document_id: int, user_id: int) -> str | None:
    """Retourne le statut de paiement de cet utilisateur pour ce document, ou None si aucune demande."""
    ligne = recuperer_un(
        "SELECT statut_paiement FROM payments WHERE document_id = ? AND user_id = ?",
        (document_id, user_id),
    )
    return ligne["statut_paiement"] if ligne else None


def utilisateur_a_acces(type_acces: str, document_id: int, user_id: int) -> bool:
    """
    Determine si un utilisateur peut telecharger un document : toujours
    vrai pour un document gratuit, vrai pour un document payant
    uniquement si le paiement de cet utilisateur a ete valide.
    """
    if type_acces != "payant":
        return True
    return statut_paiement_utilisateur(document_id, user_id) == "valide"


def paiements_en_attente_detailles() -> list[dict]:
    """
    Paiements en attente de validation, avec les informations du
    document et de l'utilisateur deja jointes, pretes pour
    l'affichage administrateur.
    """
    lignes = recuperer_tous(
        """
        SELECT p.*, s.titre AS titre_document,
               u.nom AS nom_utilisateur, u.prenom AS prenom_utilisateur,
               u.matricule AS matricule_utilisateur
        FROM payments p
        JOIN subjects s ON s.id = p.document_id
        JOIN users u ON u.id = p.user_id
        WHERE p.statut_paiement = 'en_attente'
        ORDER BY p.date_demande
        """
    )
    return [dict(l) for l in lignes]


def paiements_utilisateur(user_id: int) -> list[Paiement]:
    """Historique des demandes de paiement d'un utilisateur, plus recentes en premier."""
    lignes = recuperer_tous(
        "SELECT * FROM payments WHERE user_id = ? ORDER BY date_demande DESC", (user_id,)
    )
    return [Paiement.depuis_ligne(l) for l in lignes]


def nombre_paiements_valides() -> int:
    ligne = recuperer_un("SELECT COUNT(*) AS total FROM payments WHERE statut_paiement = 'valide'")
    return ligne["total"] if ligne else 0


def nombre_paiements_en_attente() -> int:
    ligne = recuperer_un("SELECT COUNT(*) AS total FROM payments WHERE statut_paiement = 'en_attente'")
    return ligne["total"] if ligne else 0


def nombre_ressources_payantes() -> int:
    ligne = recuperer_un("SELECT COUNT(*) AS total FROM subjects WHERE type_acces = 'payant'")
    return ligne["total"] if ligne else 0


# =====================================================================
# Moyens de paiement Mobile Money (nouveau en V3)
#
# Entierement configurables depuis l'interface d'administration (voir
# admin.page_gestion_moyens_paiement) : aucun numero ni titulaire n'est
# jamais code en dur dans l'application.
# =====================================================================

def ajouter_moyen_paiement(nom_affiche: str, operateur: str, titulaire: str,
                            numero: str, modifie_par: int) -> tuple[bool, str]:
    """Enregistre un nouveau moyen de paiement Mobile Money."""
    if operateur not in OPERATEURS_PAIEMENT_VALIDES:
        return False, "Operateur invalide."
    if not nom_affiche or not nom_affiche.strip():
        return False, "Le nom affiche est obligatoire."
    if not titulaire or not titulaire.strip():
        return False, "Le titulaire du compte est obligatoire."
    if not numero or not numero.strip():
        return False, "Le numero est obligatoire."

    executer(
        """
        INSERT INTO payment_methods (nom_affiche, operateur, titulaire, numero, modifie_par)
        VALUES (?, ?, ?, ?, ?)
        """,
        (nom_affiche.strip(), operateur, titulaire.strip(), numero.strip(), modifie_par),
    )
    journaliser("Ajout moyen de paiement", "succes", user_id=modifie_par, details=nom_affiche)
    return True, "Moyen de paiement ajoute."


def modifier_moyen_paiement(moyen_id: int, modifie_par: int, **champs) -> tuple[bool, str]:
    """
    Met a jour un ou plusieurs champs d'un moyen de paiement
    (nom_affiche, operateur, titulaire, numero, actif, ordre_affichage).
    Exemple : modifier_moyen_paiement(3, admin_id, actif=0) pour
    desactiver temporairement un moyen de paiement sans le supprimer.
    """
    champs_autorises = {"nom_affiche", "operateur", "titulaire", "numero", "actif", "ordre_affichage"}
    a_mettre_a_jour = {k: v for k, v in champs.items() if k in champs_autorises}
    if not a_mettre_a_jour:
        return False, "Aucun champ valide a mettre a jour."
    if "operateur" in a_mettre_a_jour and a_mettre_a_jour["operateur"] not in OPERATEURS_PAIEMENT_VALIDES:
        return False, "Operateur invalide."

    assignations = ", ".join(f"{c} = ?" for c in a_mettre_a_jour)
    valeurs = list(a_mettre_a_jour.values()) + [datetime.now().strftime(FORMAT_DATE_HEURE), modifie_par, moyen_id]
    executer(
        f"UPDATE payment_methods SET {assignations}, date_modification = ?, modifie_par = ? WHERE id = ?",
        tuple(valeurs),
    )
    journaliser("Modification moyen de paiement", "succes", user_id=modifie_par, details=str(moyen_id))
    return True, "Moyen de paiement mis a jour."


def supprimer_moyen_paiement(moyen_id: int, supprime_par: int) -> tuple[bool, str]:
    """
    Supprime definitivement un moyen de paiement. Sans incidence sur
    les paiements deja enregistres (payments.operateur conserve sa
    valeur, independamment de la configuration encore presente ou non
    dans payment_methods) : preferez modifier_moyen_paiement(actif=0)
    pour retirer temporairement un moyen de paiement de l'affichage
    sans perdre son historique de configuration.
    """
    executer("DELETE FROM payment_methods WHERE id = ?", (moyen_id,))
    journaliser("Suppression moyen de paiement", "succes", user_id=supprime_par, details=str(moyen_id))
    return True, "Moyen de paiement supprime."


def moyens_paiement_actifs() -> list[MoyenPaiement]:
    """Moyens de paiement actifs, dans l'ordre d'affichage configure : pour la page de paiement etudiant."""
    lignes = recuperer_tous(
        "SELECT * FROM payment_methods WHERE actif = 1 ORDER BY ordre_affichage, id"
    )
    return [MoyenPaiement.depuis_ligne(l) for l in lignes]


def tous_les_moyens_paiement() -> list[MoyenPaiement]:
    """Tous les moyens de paiement, actifs ou non : pour la gestion administrateur."""
    lignes = recuperer_tous("SELECT * FROM payment_methods ORDER BY ordre_affichage, id")
    return [MoyenPaiement.depuis_ligne(l) for l in lignes]


def qrcode_moyen_paiement(moyen: MoyenPaiement, montant: int | None = None) -> bytes:
    """
    Genere un QR code (image PNG) encodant le numero et le titulaire
    du moyen de paiement (et le montant si fourni), affichable
    directement par l'etudiant pour simplifier la saisie du transfert
    Mobile Money sur son telephone.
    """
    contenu = f"{moyen.libelle_operateur} - {moyen.numero} - {moyen.titulaire}"
    if montant:
        contenu += f" - {montant} FCFA"
    return generer_qrcode_png(contenu)


def demander_paiement_mobile_money(document_id: int, user_id: int, operateur: str,
                                    fichier_capture) -> tuple[bool, str]:
    """
    Variante de demander_paiement pour un reglement par Mobile Money :
    enregistre en plus l'operateur utilise et la capture de la preuve
    de paiement (capture d'ecran ou photo du recu), que
    l'administrateur consultera avant de valider manuellement (meme
    circuit de validation que le paiement en presentiel, voir
    valider_paiement / refuser_paiement).

    Reutilise entierement demander_paiement pour la creation/mise a
    jour de la ligne de paiement (verification du document, gestion
    d'une demande deja en attente ou refusee...), puis y ajoute
    l'operateur et la preuve : aucune regle de validation n'est
    dupliquee entre les deux circuits de paiement.
    """
    if operateur not in OPERATEURS_MOBILE_MONEY:
        return False, "Operateur Mobile Money invalide."

    valide, message_erreur = fichier_est_photo_valide(fichier_capture)
    if not valide:
        return False, message_erreur

    succes, message = demander_paiement(document_id, user_id)
    if not succes:
        return succes, message

    chemin_preuve = enregistrer_preuve_paiement(fichier_capture)
    executer(
        "UPDATE payments SET operateur = ?, capture_preuve = ? WHERE document_id = ? AND user_id = ?",
        (operateur, chemin_preuve, document_id, user_id),
    )
    journaliser("Demande de paiement Mobile Money", "succes", user_id=user_id,
                details=f"document {document_id} via {operateur}")
    return True, (
        "Preuve de paiement enregistree. Votre paiement sera verifie et "
        "valide par un administrateur."
    )
