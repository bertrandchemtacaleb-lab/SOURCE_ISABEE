"""
archive_manager.py
-------------------
Role : gerer le cycle de vie complet des documents pedagogiques
(epreuves, examens, corriges, travaux pratiques, supports de cours)
stockes dans la table subjects.

Responsabilites :
- ajout d'un document avec son fichier PDF et sa regle d'acces
  (gratuit ou payant) ;
- modification des metadonnees ;
- suppression (fichier + enregistrement) ;
- circuit de validation avant publication, avec notification
  automatique de l'auteur ;
- recherche multicritere paginee (cycle, filiere, niveau, annee,
  type, enseignant) ;
- gestion des favoris et des telechargements.

Ce module ne realise aucun affichage : il est consomme par app.py
(espace etudiant/enseignant) et par admin.py (moderation).

Correctifs apportes en V2 (voir audit) :
- le fichier televerse est desormais reellement valide (extension,
  taille, signature binaire) avant tout enregistrement sur le disque,
  via utils.fichier_est_pdf_valide. En V1, cette fonction existait
  mais n'etait jamais appelee : seul le filtre d'extension du widget
  Streamlit agissait, et un fichier renomme passait sans controle.
- rechercher_documents() accepte desormais une pagination (limite,
  decalage) : en V1, une recherche sans filtre renvoyait l'integralite
  de la table, ce qui devient inexploitable a l'echelle d'un
  etablissement.

Nouveau en V3 (corbeille, voir audit-isabee-v2.md) :
- supprimer_document() ne supprime plus immediatement et
  definitivement un document : il le deplace vers la corbeille
  (suppression reversible). Le fichier PDF reste sur le disque tant
  que le document est dans la corbeille.
- toutes les fonctions de listage destinees a un affichage normal
  (rechercher_documents, documents_recents, documents_en_attente,
  favoris_utilisateur) excluent desormais les documents en corbeille,
  afin qu'un document "supprime" disparaisse bien de la bibliotheque
  comme avant -- seul son emplacement de stockage a change.
- voir documents_dans_corbeille, restaurer_document,
  supprimer_definitivement et vider_corbeille pour la gestion de la
  corbeille (reservee a l'administration, voir admin.py).
"""

from datetime import datetime

from database import executer, recuperer_un, recuperer_tous
from models import Document, PRIX_DOCUMENT_PAYANT_DEFAUT
from utils import enregistrer_pdf, supprimer_fichier, journaliser, fichier_est_pdf_valide
import communication

FORMAT_DATE_HEURE = "%Y-%m-%d %H:%M:%S"


def ajouter_document(titre: str, description: str, type_document: str, cycle: str,
                      filiere: str, niveau: str, annee_academique: str,
                      enseignant_id: int | None, fichier_televerse,
                      ajoute_par: int, type_acces: str = "gratuit",
                      prix: int = 0, taille_max_pdf_mo: int = 25) -> tuple[bool, str]:
    """
    Enregistre un nouveau document. Le document est cree avec le statut
    'en_attente' : il ne sera visible des etudiants qu'apres validation
    par un enseignant, un contributeur habilite ou un administrateur.

    Le fichier est verifie (extension, taille, signature binaire
    reelle) avant tout enregistrement sur le disque. Un document
    payant sans prix renseigne recoit le prix par defaut de la
    plateforme plutot que d'etre enregistre a 0 FCFA par erreur.
    """
    valide, message_erreur = fichier_est_pdf_valide(fichier_televerse, taille_max_pdf_mo)
    if not valide:
        return False, message_erreur

    if type_acces == "payant" and prix <= 0:
        prix = PRIX_DOCUMENT_PAYANT_DEFAUT

    chemin_fichier, taille_ko = enregistrer_pdf(fichier_televerse)
    executer(
        """
        INSERT INTO subjects (titre, description, type_document, cycle, filiere, niveau,
                               annee_academique, enseignant_id, chemin_fichier,
                               taille_fichier_ko, type_acces, prix, mode_paiement,
                               statut, ajoute_par)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'presentiel', 'en_attente', ?)
        """,
        (titre, description, type_document, cycle, filiere, niveau, annee_academique,
         enseignant_id, chemin_fichier, taille_ko, type_acces,
         prix if type_acces == "payant" else 0, ajoute_par),
    )
    journaliser("Ajout document", "succes", user_id=ajoute_par, details=titre)
    return True, "Document soumis. Il sera visible apres validation."


def modifier_document(document_id: int, modifie_par: int, **champs) -> tuple[bool, str]:
    champs_autorises = {
        "titre", "description", "type_document", "cycle", "filiere",
        "niveau", "annee_academique", "enseignant_id", "type_acces", "prix",
    }
    a_mettre_a_jour = {k: v for k, v in champs.items() if k in champs_autorises}
    if not a_mettre_a_jour:
        return False, "Aucun champ valide a mettre a jour."

    assignations = ", ".join(f"{champ} = ?" for champ in a_mettre_a_jour)
    valeurs = list(a_mettre_a_jour.values()) + [document_id]
    executer(f"UPDATE subjects SET {assignations} WHERE id = ?", tuple(valeurs))
    journaliser("Modification document", "succes", user_id=modifie_par, details=str(document_id))
    return True, "Document mis a jour."


def supprimer_document(document_id: int, supprime_par: int) -> tuple[bool, str]:
    """
    Deplace un document vers la corbeille (suppression reversible),
    au lieu de l'effacer immediatement et definitivement comme en V2.
    Le fichier PDF reste sur le disque tant que le document est dans
    la corbeille : seule supprimer_definitivement l'efface vraiment.
    Voir aussi restaurer_document.
    """
    document = obtenir_document(document_id)
    if document is None:
        return False, "Document introuvable."
    if document.supprime:
        return False, "Ce document est deja dans la corbeille."
    executer(
        "UPDATE subjects SET supprime = 1, supprime_le = ?, supprime_par = ? WHERE id = ?",
        (datetime.now().strftime(FORMAT_DATE_HEURE), supprime_par, document_id),
    )
    journaliser("Mise en corbeille document", "succes", user_id=supprime_par, details=document.titre)
    return True, "Document deplace vers la corbeille."


def documents_dans_corbeille() -> list[dict]:
    """
    Documents actuellement dans la corbeille, avec l'identite de qui
    les a supprimes, pretes pour l'affichage administrateur (voir
    admin.page_gestion_corbeille).
    """
    lignes = recuperer_tous(
        """
        SELECT s.*, u.nom AS nom_suppresseur, u.prenom AS prenom_suppresseur
        FROM subjects s
        LEFT JOIN users u ON u.id = s.supprime_par
        WHERE s.supprime = 1
        ORDER BY s.supprime_le DESC
        """
    )
    return [dict(l) for l in lignes]


def nombre_documents_corbeille() -> int:
    ligne = recuperer_un("SELECT COUNT(*) AS total FROM subjects WHERE supprime = 1")
    return ligne["total"] if ligne else 0


def restaurer_document(document_id: int, restaure_par: int) -> tuple[bool, str]:
    """Sort un document de la corbeille et lui rend sa visibilite normale."""
    document = obtenir_document(document_id)
    if document is None:
        return False, "Document introuvable."
    if not document.supprime:
        return False, "Ce document n'est pas dans la corbeille."
    executer(
        "UPDATE subjects SET supprime = 0, supprime_le = NULL, supprime_par = NULL WHERE id = ?",
        (document_id,),
    )
    journaliser("Restauration document", "succes", user_id=restaure_par, details=document.titre)
    return True, "Document restaure."


def supprimer_definitivement(document_id: int, supprime_par: int) -> tuple[bool, str]:
    """
    Efface irreversiblement un document deja present dans la
    corbeille (fichier PDF + enregistrement en base). Reserve a
    l'administration : a la difference de supprimer_document (mise en
    corbeille, reversible), cette action ne peut plus etre annulee.
    """
    document = obtenir_document(document_id)
    if document is None:
        return False, "Document introuvable."
    if not document.supprime:
        return False, "Seul un document deja present dans la corbeille peut etre supprime definitivement."
    supprimer_fichier(document.chemin_fichier)
    executer("DELETE FROM subjects WHERE id = ?", (document_id,))
    journaliser("Suppression definitive document", "succes", user_id=supprime_par, details=document.titre)
    return True, "Document supprime definitivement."


def vider_corbeille(supprime_par: int) -> int:
    """
    Supprime definitivement tous les documents actuellement en
    corbeille. Retourne le nombre de documents traites.
    """
    documents = documents_dans_corbeille()
    for d in documents:
        supprimer_definitivement(d["id"], supprime_par)
    return len(documents)


def valider_document(document_id: int, valide_par: int) -> tuple[bool, str]:
    document = obtenir_document(document_id)
    executer(
        "UPDATE subjects SET statut = 'valide', valide_par = ?, date_validation = ? WHERE id = ?",
        (valide_par, datetime.now().strftime(FORMAT_DATE_HEURE), document_id),
    )
    journaliser("Validation document", "succes", user_id=valide_par, details=str(document_id))
    if document and document.ajoute_par:
        communication.creer_notification(
            document.ajoute_par,
            f'Votre document "{document.titre}" a ete valide et publie.',
            "validation", document_id,
        )
    return True, "Document valide et publie."


def rejeter_document(document_id: int, rejete_par: int, motif: str) -> tuple[bool, str]:
    document = obtenir_document(document_id)
    executer(
        """
        UPDATE subjects SET statut = 'rejete', valide_par = ?, date_validation = ?, motif_rejet = ?
        WHERE id = ?
        """,
        (rejete_par, datetime.now().strftime(FORMAT_DATE_HEURE), motif, document_id),
    )
    journaliser("Rejet document", "succes", user_id=rejete_par, details=f"{document_id} - {motif}")
    if document and document.ajoute_par:
        communication.creer_notification(
            document.ajoute_par,
            f'Votre document "{document.titre}" a ete rejete. Motif : {motif}',
            "validation", document_id,
        )
    return True, "Document rejete."


def obtenir_document(document_id: int) -> Document | None:
    ligne = recuperer_un("SELECT * FROM subjects WHERE id = ?", (document_id,))
    return Document.depuis_ligne(ligne) if ligne else None


def documents_en_attente() -> list[Document]:
    lignes = recuperer_tous(
        "SELECT * FROM subjects WHERE statut = 'en_attente' AND supprime = 0 ORDER BY date_ajout"
    )
    return [Document.depuis_ligne(l) for l in lignes]


def documents_recents(limite: int = 10) -> list[Document]:
    lignes = recuperer_tous(
        "SELECT * FROM subjects WHERE statut = 'valide' AND supprime = 0 ORDER BY date_ajout DESC LIMIT ?",
        (limite,),
    )
    return [Document.depuis_ligne(l) for l in lignes]


def _construire_conditions(terme: str, cycle: str, filiere: str, niveau: str, annee: str,
                            type_document: str, enseignant_id: int | None,
                            uniquement_valides: bool) -> tuple[list[str], list]:
    """
    Factorise la construction des criteres communs a
    rechercher_documents et compter_documents.

    Exclut toujours les documents en corbeille (supprime = 0) : ces
    deux fonctions servent a l'affichage normal (bibliotheque,
    gestion des documents), jamais a la corbeille elle-meme, qui
    dispose de sa propre fonction dediee (documents_dans_corbeille).
    """
    conditions = ["supprime = 0"]
    parametres: list = []

    if uniquement_valides:
        conditions.append("statut = 'valide'")
    if terme:
        conditions.append("(titre LIKE ? OR description LIKE ?)")
        parametres += [f"%{terme}%", f"%{terme}%"]
    if cycle:
        conditions.append("cycle = ?")
        parametres.append(cycle)
    if filiere:
        conditions.append("filiere = ?")
        parametres.append(filiere)
    if niveau:
        conditions.append("niveau = ?")
        parametres.append(niveau)
    if annee:
        conditions.append("annee_academique = ?")
        parametres.append(annee)
    if type_document:
        conditions.append("type_document = ?")
        parametres.append(type_document)
    if enseignant_id:
        conditions.append("enseignant_id = ?")
        parametres.append(enseignant_id)

    return conditions, parametres


def rechercher_documents(terme: str = "", cycle: str = "", filiere: str = "",
                          niveau: str = "", annee: str = "", type_document: str = "",
                          enseignant_id: int | None = None,
                          uniquement_valides: bool = True,
                          limite: int | None = 20, decalage: int = 0) -> list[Document]:
    """
    Recherche multicritere parmi les documents. Tous les criteres sont
    optionnels et combinables (ET logique). Par defaut, seuls les
    documents valides sont retournes (consultation cote etudiant).

    Paginee par defaut (20 resultats) : passer limite=None pour
    desactiver la pagination (usage interne uniquement, a eviter pour
    tout affichage destine a un utilisateur).
    """
    conditions, parametres = _construire_conditions(
        terme, cycle, filiere, niveau, annee, type_document, enseignant_id, uniquement_valides
    )
    clause_where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    requete = f"SELECT * FROM subjects {clause_where} ORDER BY date_ajout DESC"
    if limite is not None:
        requete += " LIMIT ? OFFSET ?"
        parametres = parametres + [limite, decalage]
    lignes = recuperer_tous(requete, tuple(parametres))
    return [Document.depuis_ligne(l) for l in lignes]


def compter_documents(terme: str = "", cycle: str = "", filiere: str = "",
                       niveau: str = "", annee: str = "", type_document: str = "",
                       enseignant_id: int | None = None,
                       uniquement_valides: bool = True) -> int:
    """Nombre total de resultats pour les memes criteres que rechercher_documents, pour la pagination."""
    conditions, parametres = _construire_conditions(
        terme, cycle, filiere, niveau, annee, type_document, enseignant_id, uniquement_valides
    )
    clause_where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    ligne = recuperer_un(f"SELECT COUNT(*) AS total FROM subjects {clause_where}", tuple(parametres))
    return ligne["total"] if ligne else 0


def enregistrer_telechargement(document_id: int, user_id: int, adresse_ip: str) -> None:
    executer(
        "INSERT INTO downloads (document_id, user_id, adresse_ip) VALUES (?, ?, ?)",
        (document_id, user_id, adresse_ip),
    )
    journaliser("Telechargement document", "succes", user_id=user_id, details=str(document_id))


def basculer_favori(document_id: int, user_id: int) -> bool:
    """Ajoute ou retire un document des favoris. Retourne le nouvel etat (True = favori)."""
    existe = recuperer_un(
        "SELECT id FROM favorites WHERE user_id = ? AND document_id = ?",
        (user_id, document_id),
    )
    if existe:
        executer("DELETE FROM favorites WHERE id = ?", (existe["id"],))
        return False
    executer("INSERT INTO favorites (user_id, document_id) VALUES (?, ?)", (user_id, document_id))
    return True


def favoris_utilisateur(user_id: int) -> list[Document]:
    lignes = recuperer_tous(
        """
        SELECT s.* FROM subjects s
        JOIN favorites f ON f.document_id = s.id
        WHERE f.user_id = ? AND s.supprime = 0
        ORDER BY f.date_ajout DESC
        """,
        (user_id,),
    )
    return [Document.depuis_ligne(l) for l in lignes]
