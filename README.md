# Nourri Bot — Menu hebdo sur Discord

Bot Discord qui envoie automatiquement ton menu de la semaine chaque dimanche à 8h,
généré par Gemini en fonction de tes calories, protéines et préférences mémorisées.

---

## Étape 1 — Créer le bot Discord (5 min)

1. Va sur https://discord.com/developers/applications
2. Clique **"New Application"** → donne un nom (ex: "Nourri")
3. Dans le menu gauche, clique **"Bot"**
4. Clique **"Add Bot"** → confirme
5. Sous "TOKEN", clique **"Reset Token"** → copie le token (tu en auras besoin)
6. Active **"Message Content Intent"** (dans la section Privileged Gateway Intents)
7. Dans le menu gauche, clique **"OAuth2"** → **"URL Generator"**
8. Coche **"bot"** dans Scopes, puis dans Bot Permissions coche :
   - Send Messages
   - Read Message History
9. Copie l'URL générée, colle-la dans ton navigateur → invite le bot sur ton serveur Discord

---

## Étape 2 — Récupérer l'ID du channel Discord

1. Dans Discord, active le **mode développeur** :
   Paramètres → Avancés → Mode développeur = ON
2. Fais un clic droit sur le channel où tu veux recevoir les menus
3. Clique **"Copier l'identifiant"**

---

## Étape 3 — Déployer sur Railway (gratuit)

1. Va sur https://railway.app → crée un compte gratuit (avec GitHub)
2. Clique **"New Project"** → **"Deploy from GitHub repo"**
3. Connecte ton GitHub et crée un repo avec les fichiers de ce dossier
   (ou clique "Empty Project" et utilise l'interface Railway pour upload)
4. Dans ton projet Railway, va dans **"Variables"** et ajoute :

```
DISCORD_TOKEN     = ton_token_du_bot
DISCORD_CHANNEL_ID = l_id_du_channel
GEMINI_KEY        = ta_cle_gemini
```

5. Railway détecte automatiquement Python et installe requirements.txt
6. Le bot démarre tout seul !

---

## Commandes disponibles dans Discord

| Commande | Description |
|----------|-------------|
| `!menu` | Génère le menu maintenant (sans attendre dimanche) |
| `!profil` | Affiche tes paramètres actuels |
| `!calories 3000` | Change ton objectif calorique |
| `!proteines 130` | Change ton objectif protéines |
| `!eviter brocoli` | Ne plus jamais proposer cet aliment |
| `!okeviter brocoli` | Remettre un aliment dans les suggestions |
| `!note j'adore le saumon` | Mémorise une préférence |
| `!aide` | Affiche l'aide complète |

Le menu arrive automatiquement **chaque dimanche à 8h**.

---

## Ce que le bot mémorise automatiquement

- Les plats des 2 dernières semaines (pour ne pas répéter)
- Les aliments à éviter (via `!eviter`)
- Tes préférences (via `!note`)
- Ton objectif calorique et protéines

Tout est sauvegardé dans `profile.json` sur le serveur Railway.
