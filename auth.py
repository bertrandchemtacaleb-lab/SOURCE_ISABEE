"""
auth.py
-------
Role : gerer l'authentification des utilisateurs et leur session.

Connexion par matricule et mot de passe.

Principes de securite appliques :
- chaque mot de passe est associe a un sel (salt) unique, genere
  aleatoirement a la creation du compte ;
- le mot de passe n'est jamais stocke en clair : seul le resultat du
  hachage SHA-256(sel + mot_de_passe), etire sur NB_ITERATIONS_HACHAGE
  iterations, est conserve en base ;
- le nombre de tentatives successives infructueuses est limite afin
  de ralentir les attaques par force brute ;
- la session est maintenue via st.session_state, propre a chaque
  utilisateur connecte au serveur Streamlit, et expire automatiquement
  apres une periode d'inactivite ;
- une politique minimale de robustesse est imposee a tout nouveau
  mot de passe.

Limite assumee : SHA-256 avec sel et etirement est le mecanisme
impose par le cahier des charges. Pour un deploiement expose sur
Internet, un algorithme dedie aux mots de passe (Argon2id ou bcrypt),
au cout de calcul reglable et resistant au calcul sur GPU, reste
preferable a un hachage rapide comme SHA-256. Ce point est documente
dans l'audit.

Correctifs de securite apportes en V2 (voir audit) :
- la protection anti-brute-force ne repose plus sur un compteur en
  st.session_state (reinitialisable simplement en ouvrant un nouvel
  onglet ou une fenetre de navigation privee) : elle interroge
  desormais le journal systeme (table logs), qui persiste cote
  serveur independamment du navigateur du client.
- la session expire automatiquement apres une periode d'inactivite
  (aucune expiration n'existait en V1).
- toute creation ou reinitialisation de mot de passe doit desormais
  passer la verification mot_de_passe_est_robuste().

Nouveau en V3 : mot de passe oublie (demander_reinitialisation_mot_de_passe,
reinitialiser_mot_de_passe_par_jeton), via un jeton a usage unique et a
duree de vie limitee, sur le meme principe de securite que les mots de
passe eux-memes (seul le hachage du jeton est stocke, jamais le jeton
en clair).
"""

import hashlib
import secrets
from datetime import datetime, timedelta

import streamlit as st

from database import recuperer_un, executer
from models import Utilisateur
from utils import journaliser, adresse_ip_client

NB_ITERATIONS_HACHAGE = 150_000
NB_TENTATIVES_MAX = 5
FENETRE_BLOCAGE_MINUTES = 15
DUREE_MAX_INACTIVITE_MINUTES = 30
LONGUEUR_MIN_MOT_DE_PASSE = 8
DUREE_VALIDITE_JETON_RESET_MINUTES = 30

FORMAT_DATE_HEURE = "%Y-%m-%d %H:%M:%S"


def generer_sel() -> str:
    """Genere un sel cryptographiquement aleatoire, propre a un compte."""
    return secrets.token_hex(16)


def hacher_mot_de_passe(mot_de_passe: str, sel: str) -> str:
    """
    Calcule le hachage SHA-256 d'un mot de passe avec son sel.
    Le hachage est applique de maniere repetee (etirement de cle) afin
    de ralentir les attaques par dictionnaire, tout en restant base sur
    l'algorithme SHA-256 impose par le cahier des charges.
    """
    valeur = (sel + mot_de_passe).encode("utf-8")
    for _ in range(NB_ITERATIONS_HACHAGE):
        valeur = hashlib.sha256(valeur).digest()
    return valeur.hex()


def mot_de_passe_correct(mot_de_passe: str, sel: str, hachage_attendu: str) -> bool:
    """Compare un mot de passe fourni au hachage stocke, en temps constant."""
    calcule = hacher_mot_de_passe(mot_de_passe, sel)
    return secrets.compare_digest(calcule, hachage_attendu)


def mot_de_passe_est_robuste(mot_de_passe: str) -> tuple[bool, str]:
    """
    Verifie qu'un mot de passe respecte une politique minimale : au
    moins 8 caracteres, au moins une lettre et au moins un chiffre.

    Cette politique reste volontairement simple (aucune regle plus
    stricte n'a ete demandee), mais elle ferme une faille reelle de la
    V1, qui n'imposait absolument aucune contrainte, pas meme une
    longueur minimale. A appeler par users.py avant toute creation de
    compte ou reinitialisation de mot de passe.
    """
    if len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
        return False, f"Le mot de passe doit contenir au moins {LONGUEUR_MIN_MOT_DE_PASSE} caracteres."
    if not any(c.isalpha() for c in mot_de_passe):
        return False, "Le mot de passe doit contenir au moins une lettre."
    if not any(c.isdigit() for c in mot_de_passe):
        return False, "Le mot de passe doit contenir au moins un chiffre."
    return True, ""


def _nombre_echecs_recents(matricule: str) -> int:
    """
    Compte les tentatives de connexion infructueuses pour ce matricule
    au cours des FENETRE_BLOCAGE_MINUTES dernieres minutes, en
    interrogeant le journal systeme plutot qu'un compteur local au
    navigateur. C'est ce qui rend le blocage effectif independamment
    du nombre d'onglets ou de sessions de navigation ouverts par
    l'attaquant.
    """
    seuil = (datetime.now() - timedelta(minutes=FENETRE_BLOCAGE_MINUTES)).strftime(FORMAT_DATE_HEURE)
    ligne = recuperer_un(
        """
        SELECT COUNT(*) AS nombre FROM logs
        WHERE action = 'Connexion' AND resultat = 'echec'
          AND matricule = ? AND date_heure >= ?
        """,
        (matricule, seuil),
    )
    return ligne["nombre"] if ligne else 0


def authentifier(matricule: str, mot_de_passe: str) -> tuple[bool, str]:
    """
    Verifie les identifiants fournis et ouvre la session si valides.
    Retourne (succes, message).
    """
    if _nombre_echecs_recents(matricule) >= NB_TENTATIVES_MAX:
        journaliser("Connexion", "echec", matricule=matricule,
                    details="Nombre maximal de tentatives atteint.")
        return False, (
            f"Trop de tentatives infructueuses. Reessayez dans quelques minutes "
            f"ou contactez un administrateur."
        )

    ligne = recuperer_un("SELECT * FROM users WHERE matricule = ?", (matricule,))
    if ligne is None or not mot_de_passe_correct(mot_de_passe, ligne["sel"], ligne["mot_de_passe_hash"]):
        journaliser("Connexion", "echec", matricule=matricule, details="Identifiants invalides.")
        return False, "Matricule ou mot de passe incorrect."

    if ligne["statut"] == "suspendu":
        journaliser("Connexion", "echec", user_id=ligne["id"], matricule=matricule,
                    details="Compte suspendu.")
        return False, "Ce compte a ete suspendu. Contactez un administrateur."

    executer("UPDATE users SET derniere_connexion = ? WHERE id = ?",
              (datetime.now().strftime(FORMAT_DATE_HEURE), ligne["id"]))

    st.session_state["utilisateur_connecte"] = Utilisateur.depuis_ligne(ligne)
    st.session_state["derniere_activite"] = datetime.now().isoformat()
    journaliser("Connexion", "succes", user_id=ligne["id"], matricule=matricule)
    return True, "Connexion reussie."


def deconnecter() -> None:
    """Termine la session de l'utilisateur courant."""
    utilisateur = utilisateur_courant()
    if utilisateur is not None:
        journaliser("Deconnexion", "succes", user_id=utilisateur.id, matricule=utilisateur.matricule)
    for cle in ("utilisateur_connecte", "derniere_activite"):
        st.session_state.pop(cle, None)


def utilisateur_courant() -> Utilisateur | None:
    """Retourne l'utilisateur actuellement connecte, ou None."""
    return st.session_state.get("utilisateur_connecte")


def est_connecte() -> bool:
    return utilisateur_courant() is not None


def a_le_role(*roles_autorises: str) -> bool:
    """Verifie que l'utilisateur connecte possede l'un des roles donnes."""
    utilisateur = utilisateur_courant()
    return utilisateur is not None and utilisateur.role in roles_autorises


def exiger_role(*roles_autorises: str) -> bool:
    """
    Bloque l'affichage de la page courante si l'utilisateur connecte
    n'a pas l'un des roles requis. A appeler en tete de chaque page
    sensible (admin.py, settings.py, etc.).
    """
    if not a_le_role(*roles_autorises):
        st.error("Acces refuse : cette section ne vous est pas accessible.")
        st.stop()
    return True


def session_expiree(duree_max_minutes: int = DUREE_MAX_INACTIVITE_MINUTES) -> bool:
    """
    Indique si la session de l'utilisateur connecte a expire par
    inactivite. Retourne toujours False si personne n'est connecte :
    ce n'est pas a cette fonction de decider qu'il faut afficher la
    page de connexion, seulement de detecter l'expiration d'une
    session existante.
    """
    derniere = st.session_state.get("derniere_activite")
    if derniere is None:
        return False
    try:
        instant = datetime.fromisoformat(derniere)
    except ValueError:
        return True
    return (datetime.now() - instant) > timedelta(minutes=duree_max_minutes)


def actualiser_activite() -> None:
    """
    Repousse l'expiration de la session courante. A appeler a chaque
    chargement de page tant que la session est valide (voir
    verifier_session_active, qui s'en charge automatiquement).
    """
    if est_connecte():
        st.session_state["derniere_activite"] = datetime.now().isoformat()


def verifier_session_active(duree_max_minutes: int = DUREE_MAX_INACTIVITE_MINUTES) -> None:
    """
    Garde de session, a appeler en tete de chaque page protegee,
    immediatement apres avoir verifie que l'utilisateur est connecte
    (est_connecte()). Deconnecte automatiquement et arrete le rendu de
    la page si la session a expire par inactivite ; sinon, repousse
    l'expiration et laisse la page se poursuivre normalement.

    Le parametre duree_max_minutes permet a l'appelant (app.py) de
    fournir une duree configurable depuis les parametres systeme
    (cle "expiration_session_minutes"), sans que ce module ait besoin
    d'importer settings.py.

    Exemple d'utilisation dans app.py :

        if not auth.est_connecte():
            afficher_page_connexion()
            st.stop()
        auth.verifier_session_active(duree_max_minutes=duree_configuree)
        # ... suite du rendu de la page ...
    """
    if not est_connecte():
        return
    if session_expiree(duree_max_minutes):
        utilisateur = utilisateur_courant()
        journaliser("Expiration de session", "succes",
                    user_id=utilisateur.id if utilisateur else None,
                    matricule=utilisateur.matricule if utilisateur else None)
        deconnecter()
        st.warning("Votre session a expire par inactivite. Veuillez vous reconnecter.")
        st.stop()
    actualiser_activite()


# =====================================================================
# Mot de passe oublie (nouveau en V3)
#
# Le jeton n'est jamais stocke en clair en base : seul son hachage
# SHA-256 l'est (table password_reset_tokens), selon le meme principe
# que les mots de passe eux-memes. Il est a usage unique et expire
# apres DUREE_VALIDITE_JETON_RESET_MINUTES.
# =====================================================================

def _hacher_jeton(jeton: str) -> str:
    return hashlib.sha256(jeton.encode("utf-8")).hexdigest()


def demander_reinitialisation_mot_de_passe(email: str) -> tuple[bool, str, str | None]:
    """
    Genere un jeton de reinitialisation pour le compte associe a cet
    e-mail. Retourne (succes, message, jeton) : le jeton n'est
    retourne qu'une seule fois ici (il n'est jamais retrouvable
    ensuite, seul son hachage est conserve) et doit etre transmis a
    l'utilisateur immediatement -- par e-mail si une messagerie
    sortante est configuree, ou communique autrement par un
    administrateur sinon (voir page_connexion, onglet "Mot de passe
    oublie").

    Ne revele jamais si l'e-mail correspond ou non a un compte
    existant : le meme message est retourne dans les deux cas, afin
    de ne pas permettre a un tiers de decouvrir par cette page quelles
    adresses sont enregistrees sur la plateforme (enumeration de
    comptes). Le jeton retourne est None si l'e-mail ne correspond a
    aucun compte.
    """
    message_generique = (
        "Si un compte est associe a cette adresse, un jeton de "
        "reinitialisation a ete genere ci-dessous."
    )
    ligne = recuperer_un("SELECT id FROM users WHERE email = ?", (email,))
    if ligne is None:
        return True, message_generique, None

    jeton = secrets.token_urlsafe(32)
    expiration = (datetime.now() + timedelta(minutes=DUREE_VALIDITE_JETON_RESET_MINUTES)).strftime(FORMAT_DATE_HEURE)
    executer(
        "INSERT INTO password_reset_tokens (user_id, jeton_hash, date_expiration) VALUES (?, ?, ?)",
        (ligne["id"], _hacher_jeton(jeton), expiration),
    )
    journaliser("Demande de reinitialisation mot de passe", "succes", user_id=ligne["id"])
    return True, message_generique, jeton


def reinitialiser_mot_de_passe_par_jeton(jeton: str, nouveau_mot_de_passe: str) -> tuple[bool, str]:
    """
    Verifie un jeton de reinitialisation (existant, non expire, non
    deja utilise) et, si valide, met a jour le mot de passe
    correspondant puis invalide definitivement le jeton (usage
    unique). Retourne (succes, message).
    """
    if not jeton:
        return False, "Veuillez saisir le jeton recu."

    jeton_hash = _hacher_jeton(jeton)
    ligne = recuperer_un(
        """
        SELECT * FROM password_reset_tokens
        WHERE jeton_hash = ? AND utilise = 0 AND date_expiration >= ?
        """,
        (jeton_hash, datetime.now().strftime(FORMAT_DATE_HEURE)),
    )
    if ligne is None:
        return False, "Ce jeton de reinitialisation est invalide, deja utilise ou a expire."

    # Import tardif (et non en tete de module) : users.py importe deja
    # generer_sel/hacher_mot_de_passe depuis auth.py. Importer users au
    # niveau du module creerait une dependance circulaire ; importer ici,
    # au moment de l'appel, l'evite sans aucun effet de bord.
    from users import reinitialiser_mot_de_passe

    succes, message = reinitialiser_mot_de_passe(ligne["user_id"], nouveau_mot_de_passe)
    if succes:
        executer("UPDATE password_reset_tokens SET utilise = 1 WHERE id = ?", (ligne["id"],))
        journaliser("Reinitialisation mot de passe via jeton", "succes", user_id=ligne["user_id"])
    return succes, message


# =====================================================================
# Connexion Google / Microsoft (nouveau en V3)
#
# Repose sur l'authentification OIDC native de Streamlit (st.login,
# st.logout, st.user -- necessite Streamlit >= 1.42 et le paquet
# Authlib, voir requirements.txt), configuree via
# .streamlit/secrets.toml (voir secrets.toml.example fourni avec le
# projet). Toutes les fonctions ci-dessous degradent silencieusement
# (aucune erreur, aucun plantage) si cette configuration est absente :
# la connexion par matricule continue alors de fonctionner exactement
# comme avant, seule la connexion Google/Microsoft reste invisible.
# =====================================================================

def fournisseurs_oidc_configures() -> set[str]:
    """
    Fournisseurs OIDC (Google, Microsoft) configures dans
    .streamlit/secrets.toml. Retourne un ensemble vide, sans jamais
    lever d'erreur, si aucune authentification OIDC n'est configuree :
    le matricule reste alors le seul moyen de connexion affiche, comme
    avant l'introduction de cette fonctionnalite.
    """
    try:
        section_auth = st.secrets.get("auth", {})
    except Exception:
        return set()
    return {nom for nom in ("google", "microsoft") if nom in section_auth}


def oidc_utilisateur_connecte() -> bool:
    """
    Vrai si la session courante est deja authentifiee via Google ou
    Microsoft (st.user.is_logged_in), sans jamais lever d'erreur si
    l'authentification OIDC n'est pas configuree.
    """
    try:
        return bool(st.user.is_logged_in)
    except Exception:
        return False


def connecter_via_oidc(email: str, prenom: str, nom: str) -> tuple[bool, str]:
    """
    Etablit la session ISABEE (st.session_state["utilisateur_connecte"])
    pour un utilisateur deja authentifie par Google ou Microsoft (voir
    page_connexion et main() dans app.py, qui appellent cette fonction
    une fois st.user.is_logged_in confirme). Retrouve le compte par
    e-mail, ou le cree automatiquement si c'est la premiere connexion
    par ce moyen (voir users.connecter_ou_creer_compte_oidc : role
    systematiquement "etudiant", exactement comme pour l'inscription
    libre par matricule -- seul un administrateur peut promouvoir un
    role superieur).
    """
    # Import tardif : meme raison que pour reinitialiser_mot_de_passe_par_jeton
    # ci-dessus (eviter une dependance circulaire avec users.py).
    from users import connecter_ou_creer_compte_oidc

    succes, message, utilisateur = connecter_ou_creer_compte_oidc(email, prenom, nom)
    if not succes or utilisateur is None:
        return False, message

    st.session_state["utilisateur_connecte"] = utilisateur
    st.session_state["derniere_activite"] = datetime.now().isoformat()
    journaliser("Connexion via Google/Microsoft", "succes",
                user_id=utilisateur.id, matricule=utilisateur.matricule)
    return True, message
