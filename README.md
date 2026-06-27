# SOURCE ISABEE — Version 3

Plateforme institutionnelle de gestion des ressources pédagogiques de
l'ISABEE : bibliothèque de documents par filière/cycle/niveau,
monétisation des ressources payantes (paiement en présentiel **ou par
Mobile Money — Orange Money, MTN MoMo**), espace communautaire
(messagerie, annonces, commentaires, notifications), corbeille
(suppression réversible des documents), inscription libre et mot de
passe oublié, et administration complète.

Cette V3 est une évolution strictement additive de la V2 : voir
`audit-isabee-v2.md` pour l'audit complet ayant guidé ces ajouts.
Rien n'a été supprimé, déplacé ni cassé.

## Installation

```bash
pip install -r requirements.txt
streamlit run app.py
```

Aucun compte n'existe à l'installation : le premier lancement affiche
un écran de configuration initiale pour créer le compte administrateur.
Une base SQLite déjà en production (V1 ou V2) est migrée automatiquement
et sans perte de données au premier démarrage (voir `database.py`).

## Nouveautés V3

- **Corbeille** : un document supprimé est désormais déplacé vers la
  corbeille (réversible) au lieu d'être effacé immédiatement. Voir
  Administration → Corbeille (restauration ou suppression définitive).
- **Paiement Mobile Money** : en complément du paiement en présentiel
  (inchangé), un étudiant peut régler via Orange Money ou MTN MoMo,
  joindre une capture de la preuve de paiement, et obtenir un QR code
  facilitant le transfert. Les numéros et titulaires sont **entièrement
  configurables** depuis Administration → Moyens de paiement — jamais
  codés en dur dans le code.
- **Connexion Google / Microsoft** : en complément de la connexion par
  matricule (jamais en remplacement). Repose sur l'authentification
  OIDC native de Streamlit (`st.login`/`st.user`, Streamlit ≥ 1.42 +
  Authlib). **Configuration requise** — voir « Activer la connexion
  Google / Microsoft » ci-dessous. Sans cette configuration, les
  boutons restent simplement invisibles et le reste de l'application
  fonctionne normalement.
- **Inscription libre** : un visiteur peut créer lui-même un compte
  (rôle `etudiant`, actif immédiatement). Une première connexion
  Google/Microsoft crée également un compte de la même façon si
  l'e-mail n'est pas déjà enregistré ; si l'e-mail correspond à un
  compte existant (matricule ou Google/Microsoft), c'est ce compte qui
  est utilisé — jamais de doublon. Seul un administrateur peut ensuite
  promouvoir un compte vers un rôle supérieur.
- **Mot de passe oublié** : réinitialisation par jeton à usage unique
  et à durée de vie limitée (30 minutes). En l'absence d'envoi d'e-mail
  configuré, le jeton est affiché directement à l'écran ; câbler un
  envoi SMTP réel est la suite naturelle (voir Limites connues).
- **Suivi de versions de schéma** (`schema_versions`, voir
  `database/migrations/`) : chaque évolution de schéma est désormais
  documentée et tracée, en plus d'être appliquée de façon idempotente.

Toujours non couvert (voir l'audit pour le détail complet) : connexion
persistante, page de profil enrichie (biographie, capture webcam),
historique de versions de documents, calendrier académique, FAQ, et les
autres fonctionnalités listées comme manquantes dans `audit-isabee-v2.md`.

## Activer la connexion Google / Microsoft

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Le `client_id` Google est déjà renseigné dans le modèle. Il vous reste
à :

1. Récupérer le **client_secret** correspondant depuis [Google Cloud
   Console](https://console.cloud.google.com/) (APIs et services →
   Identifiants → votre client OAuth 2.0), et le coller dans
   `.streamlit/secrets.toml`.
2. Déclarer `http://localhost:8501/oauth2callback` (ou l'URL de votre
   déploiement) dans « URI de redirection autorisés » pour ce client.
3. Pour Microsoft : créer une inscription d'application dans le portail
   Azure (Azure Active Directory → Inscriptions d'applications),
   décommenter le bloc `[auth.microsoft]` du modèle, et y renseigner
   `client_id`, `client_secret` et votre `{tenant}`.

`.streamlit/secrets.toml` est volontairement exclu du suivi Git
(`.gitignore`) : ne le partagez jamais publiquement, il contient des
secrets. Tant qu'il n'existe pas (ou qu'un bloc `[auth.google]` /
`[auth.microsoft]` n'est pas rempli), l'application fonctionne
normalement par matricule uniquement.



## Arborescence

```
app.py                  point d'entree, routage, pages communes
admin.py                pages reservees a l'administration
auth.py                 authentification, session, mots de passe, mot de passe oublie, connexion OIDC
database.py             acces SQLite, migrations automatiques V1->V2 et V2->V3
database/migrations/    documentation versionnee des migrations de schema
.streamlit/secrets.toml.example  modele de configuration Google/Microsoft (a copier et completer)
models.py               structures de donnees, filieres/cycles/niveaux ISABEE
schema.sql               schema de la base (cree a neuf)
users.py                gestion des comptes, profil personnel, inscription libre, comptes OIDC
archive_manager.py      cycle de vie des documents, corbeille
payments.py              paiements en presentiel et Mobile Money
communication.py        messagerie, annonces, notifications, commentaires
statistics.py            indicateurs et series pour le tableau de bord
settings.py              parametres systeme (admin)
utils.py                 fonctions transverses (securite, fichiers, dates, QR code)
icons.py                 icones vectorielles locales (style Lucide)
assets/style.css          theme clair (Menutech)
assets/style-sombre.css   theme sombre (complement)
assets/logo.png           logo CBT Technology
audit-isabee-v2.md        audit complet V2 ayant guide les ajouts de cette V3
```

## Filières ISABEE

Les 16 filières officielles (`models.FILIERES_ISABEE`) s'appliquent
aux cycles Licence et Ingénieur. Le cycle Master (Master I / Master II)
ne suit pas ce découpage : aucune liste officielle de mentions n'a
encore été communiquée, le champ filière reste donc en saisie libre
pour ce cycle. À mettre à jour dans `models.py` dès que cette liste
sera disponible.

## Sécurité — ce qui est couvert et ce qui ne l'est pas

Couvert : mots de passe hachés (SHA-256 salé, étiré), politique de
mot de passe minimale, anti-brute-force persistant côté serveur
(indépendant du navigateur), expiration de session par inactivité,
validation réelle des fichiers PDF par signature binaire (pas
seulement l'extension), échappement HTML systématique des données
utilisateur avant tout rendu HTML personnalisé, journal système,
réinitialisation de mot de passe par jeton à usage unique haché (jamais
stocké en clair), résistant à l'énumération de comptes.

Non couvert, à la charge d'un déploiement de production : HTTPS (à
gérer par le reverse proxy), sauvegarde et restauration de la base
SQLite, supervision/alerting, envoi d'e-mail réel pour le mot de passe
oublié (le jeton est affiché directement faute de SMTP configuré), et
migration vers un algorithme de hachage dédié aux mots de passe
(Argon2id ou bcrypt) si la plateforme est un jour exposée directement
sur Internet sans contrôle d'accès réseau en amont. La connexion
Google/Microsoft est disponible mais optionnelle (voir « Activer la
connexion Google / Microsoft ») : sans configuration, seule la
connexion par matricule est active.

## Limites connues de cette V3

- Pas de tests automatisés ni de CI/CD (les modifications de cette V3
  ont été vérifiées manuellement contre une base SQLite réelle avant
  livraison, mais aucun test n'est inclus dans le dépôt).
- Le mot de passe oublié génère un jeton mais ne l'envoie pas par
  e-mail (aucune configuration SMTP demandée à ce stade) : le jeton est
  affiché à l'écran, ce qui suffit pour un usage interne/pilote mais
  pas pour un déploiement public.
- Pas de connexion persistante ("se souvenir de moi") sur la connexion
  par matricule (la connexion Google/Microsoft, elle, persiste 30 jours
  via le cookie d'identite gere par Streamlit).
- Les icônes ne sont pas littéralement importées de `lucide-react`
  (incompatible avec un backend Python) : `icons.py` fournit un jeu
  de SVG locaux dans le même esprit visuel.
- Le thème sombre et la langue sont stockés et appliqués (CSS, pour
  le thème), mais la traduction complète de tous les libellés de
  l'interface n'est pas encore réalisée.
- Master n'a pas de liste de filières officielle (voir plus haut).
- Page de profil enrichie (biographie, capture webcam), historique de
  versions de documents, calendrier académique, FAQ et les autres
  éléments listés dans `audit-isabee-v2.md` restent à construire.

## Compte de test

Aucun compte de démonstration n'est pré-rempli. Créez le premier
compte administrateur via l'écran de configuration initiale, puis
utilisez Gestion des comptes pour créer les comptes enseignants et
contributeurs (les étudiants peuvent désormais s'inscrire eux-mêmes).

