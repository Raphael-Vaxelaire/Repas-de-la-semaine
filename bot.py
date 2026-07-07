import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─── Config ───────────────────────────────────────────────
DISCORD_TOKEN  = os.environ["DISCORD_TOKEN"]
DISCORD_CH_ID  = int(os.environ["DISCORD_CHANNEL_ID"])
GEMINI_KEY     = os.environ["GEMINI_KEY"]
PROFILE_FILE   = "profile.json"

# ─── Profil utilisateur (persistant) ──────────────────────
DEFAULT_PROFILE = {
    "kcal": 3000,
    "proteines": 130,
    "eviter": [],          # ingrédients à ne jamais proposer
    "historique": [],      # titres des plats des 2 dernières semaines
    "preferences": [],     # notes libres mémorisées ("j'adore le saumon", etc.)
    "dernier_menu": None,
}

def load_profile():
    if os.path.exists(PROFILE_FILE):
        with open(PROFILE_FILE) as f:
            p = json.load(f)
        # S'assurer que toutes les clés existent
        for k, v in DEFAULT_PROFILE.items():
            p.setdefault(k, v)
        return p
    return dict(DEFAULT_PROFILE)

def save_profile(p):
    with open(PROFILE_FILE, "w") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)

# ─── Appel Gemini ─────────────────────────────────────────
async def call_gemini(prompt: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.85}
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Gemini erreur {resp.status}: {text[:200]}")
            data = await resp.json()
    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

# ─── Prompt génération menu ───────────────────────────────
def build_prompt(profile: dict) -> str:
    eviter_str  = ", ".join(profile["eviter"]) if profile["eviter"] else "aucun"
    histo_str   = ", ".join(profile["historique"][-14:]) if profile["historique"] else "aucun"
    prefs_str   = "; ".join(profile["preferences"]) if profile["preferences"] else "aucune"
    kcal        = profile["kcal"]
    prot        = profile["proteines"]

    return f"""Tu es un nutritionniste expert en batch cooking / meal prep. Génère un menu de la semaine pour UNE personne.

PROFIL :
- Objectif : {kcal} kcal/jour, {prot} g de protéines/jour
- Ingrédients à NE JAMAIS utiliser : {eviter_str}
- Plats déjà mangés récemment (à ne pas répéter) : {histo_str}
- Préférences et notes : {prefs_str}

PRINCIPE BATCH COOKING :
- Samedi et dimanche : on cuisine en grande quantité (3-4 bases)
- Lundi à vendredi : on réchauffe et décline ces bases (max 5 min par repas)
- 4 repas par jour : petit-déjeuner, déjeuner, collation, dîner

RÉPARTITION CALORIQUE PAR REPAS :
- Petit-déjeuner : ~{round(kcal*0.24)} kcal, ~{round(prot*0.24)} g protéines
- Déjeuner       : ~{round(kcal*0.32)} kcal, ~{round(prot*0.32)} g protéines
- Collation      : ~{round(kcal*0.12)} kcal, ~{round(prot*0.12)} g protéines
- Dîner          : ~{round(kcal*0.32)} kcal, ~{round(prot*0.32)} g protéines

Réponds UNIQUEMENT en JSON valide, sans texte autour :
{{
  "batch_weekend": [
    {{
      "title": "Poulet rôti aux herbes",
      "portions": 3,
      "time_min": 35,
      "ingr": [["Filets de poulet", "600 g", 7.20], ["Huile d'olive", "2 c.à.s.", 0.30]],
      "steps": ["Préchauffer le four à 200°C.", "Assaisonner et enfourner 25 min."],
      "prix_total": 8.50
    }}
  ],
  "jours": [
    {{
      "jour": "Samedi",
      "weekend": true,
      "petit": {{"title": "...", "time_min": 10, "kcal": {round(kcal*0.24)}, "prot": {round(prot*0.24)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}},
      "midi":  {{"title": "...", "time_min": 25, "kcal": {round(kcal*0.32)}, "prot": {round(prot*0.32)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}},
      "collation": {{"title": "...", "time_min": 3, "kcal": {round(kcal*0.12)}, "prot": {round(prot*0.12)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}},
      "soir":  {{"title": "...", "time_min": 30, "kcal": {round(kcal*0.32)}, "prot": {round(prot*0.32)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}}
    }}
  ],
  "liste_courses": [
    {{"nom": "Filets de poulet", "quantite": "600 g", "prix_estime": 7.20, "categorie": "Viandes & Poissons"}},
    {{"nom": "Riz complet", "quantite": "400 g", "prix_estime": 1.80, "categorie": "Féculents"}}
  ],
  "budget_total": 75.50
}}

Les 7 jours sont : Samedi, Dimanche, Lundi, Mardi, Mercredi, Jeudi, Vendredi.
Week-end = cuisine normale. Semaine = réchauffage batch (time_min: 5, steps courts).
Prix en euros grande surface française. Variété maximale entre les jours."""

# ─── Formater le message Discord ──────────────────────────
JOURS_EMOJI = {"Samedi":"🟡","Dimanche":"🟠","Lundi":"🔵","Mardi":"🔵","Mercredi":"🔵","Jeudi":"🔵","Vendredi":"🔵"}
SLOT_EMOJI  = {"petit":"☀️","midi":"🥗","collation":"🍎","soir":"🌙"}

def format_menu_discord(menu: dict, profile: dict) -> list[str]:
    """Retourne une liste de messages (Discord limite à 2000 chars par message)."""
    messages = []
    now = datetime.now()
    semaine = now.strftime("Semaine du %d/%m/%Y")

    # ── Message 1 : intro + batch cooking ──
    m1 = f"# 🍽️ Menu de la semaine — {semaine}\n"
    m1 += f"**Objectif :** {profile['kcal']} kcal · {profile['proteines']} g protéines / jour\n\n"

    m1 += "## 🍳 Ce week-end tu cuisines\n"
    batch = menu.get("batch_weekend", [])
    prix_batch = 0
    for b in batch:
        prix_batch += b.get("prix_total", 0)
        ingr_short = " · ".join(i[0] for i in b["ingr"][:3])
        m1 += f"**{b['title']}** ({b['portions']} portions, {b['time_min']} min)\n"
        m1 += f"> {ingr_short}{'…' if len(b['ingr'])>3 else ''}\n"
    messages.append(m1)

    # ── Messages 2-8 : un par jour ──
    for jour_data in menu.get("jours", []):
        j = jour_data["jour"]
        emoji = JOURS_EMOJI.get(j, "📅")
        is_we = jour_data.get("weekend", False)
        tag = "🍳 Cuisine" if is_we else "♻️ Batch"

        msg = f"## {emoji} {j} — {tag}\n"
        total_kcal = 0
        total_prot = 0

        for slot in ["petit","midi","collation","soir"]:
            repas = jour_data.get(slot, {})
            if not repas:
                continue
            se = SLOT_EMOJI[slot]
            titre = repas.get("title","?")
            t = repas.get("time_min", 0)
            k = repas.get("kcal", 0)
            p = repas.get("prot", 0)
            total_kcal += k
            total_prot += p
            time_str = f"⏱ {t} min" if t > 5 else "♻️ 5 min"
            msg += f"{se} **{titre}** — {time_str} · {k} kcal · {p}g prot\n"

        msg += f"\n> **Total jour : {total_kcal} kcal · {total_prot} g protéines**\n"
        messages.append(msg)

    # ── Dernier message : liste de courses + budget ──
    courses = menu.get("liste_courses", [])
    budget  = menu.get("budget_total", 0)

    cats = {}
    for item in courses:
        cat = item.get("categorie","Autre")
        cats.setdefault(cat, []).append(item)

    m_courses = "## 🛒 Liste de courses\n"
    for cat, items in cats.items():
        m_courses += f"\n**{cat}**\n"
        for item in items:
            m_courses += f"• {item['nom']} — {item['quantite']} (~{item['prix_estime']:.2f} €)\n"
    m_courses += f"\n💰 **Budget total estimé : {budget:.2f} €**\n"
    m_courses += "\n---\n_Réponds à ce message pour mémoriser tes préférences : \"pas de X\", \"j'adore Y\", \"changer les calories à Z\"_"
    messages.append(m_courses)

    return messages

# ─── Bot Discord ──────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    scheduler.start()
    print("⏰ Scheduler démarré")

# ── Tâche hebdo : dimanche 8h ──
@scheduler.scheduled_job("cron", day_of_week="sun", hour=8, minute=0)
async def envoyer_menu_auto():
    await generer_et_envoyer()

async def generer_et_envoyer(channel=None):
    """Génère le menu et l'envoie sur Discord."""
    profile = load_profile()
    ch = channel or bot.get_channel(DISCORD_CH_ID)
    if not ch:
        print(f"❌ Channel {DISCORD_CH_ID} introuvable")
        return

    await ch.send("⏳ Génération de ton menu de la semaine avec Gemini…")

    try:
        prompt = build_prompt(profile)
        menu   = await call_gemini(prompt)
    except Exception as e:
        await ch.send(f"❌ Erreur Gemini : {e}")
        return

    # Mémoriser les titres dans l'historique
    nouveaux_titres = []
    for jour in menu.get("jours", []):
        for slot in ["petit","midi","collation","soir"]:
            t = jour.get(slot, {}).get("title")
            if t:
                nouveaux_titres.append(t)
    profile["historique"] = (profile["historique"] + nouveaux_titres)[-28:]  # garder 2 semaines
    profile["dernier_menu"] = datetime.now().isoformat()
    save_profile(profile)

    # Envoyer les messages
    messages = format_menu_discord(menu, profile)
    for msg in messages:
        # Découper si > 2000 chars
        while len(msg) > 1900:
            await ch.send(msg[:1900])
            msg = msg[1900:]
        if msg.strip():
            await ch.send(msg)

    await ch.send("✅ Menu envoyé ! Réponds à ce message pour mémoriser tes préférences.")

# ─── Commandes manuelles ──────────────────────────────────
@bot.command(name="menu")
async def cmd_menu(ctx):
    """!menu — génère le menu maintenant sans attendre dimanche"""
    await generer_et_envoyer(ctx.channel)

@bot.command(name="profil")
async def cmd_profil(ctx):
    """!profil — affiche ton profil actuel"""
    p = load_profile()
    msg  = "## 👤 Ton profil\n"
    msg += f"**Calories :** {p['kcal']} kcal/jour\n"
    msg += f"**Protéines :** {p['proteines']} g/jour\n"
    msg += f"**À éviter :** {', '.join(p['eviter']) if p['eviter'] else 'rien'}\n"
    msg += f"**Préférences :** {'; '.join(p['preferences']) if p['preferences'] else 'aucune'}\n"
    msg += f"**Dernier menu :** {p['dernier_menu'] or 'jamais'}\n"
    await ctx.send(msg)

@bot.command(name="calories")
async def cmd_calories(ctx, valeur: int):
    """!calories 3000 — change ton objectif calorique"""
    p = load_profile()
    p["kcal"] = valeur
    save_profile(p)
    await ctx.send(f"✅ Objectif mis à jour : **{valeur} kcal/jour**")

@bot.command(name="proteines")
async def cmd_proteines(ctx, valeur: int):
    """!proteines 130 — change ton objectif protéines"""
    p = load_profile()
    p["proteines"] = valeur
    save_profile(p)
    await ctx.send(f"✅ Objectif mis à jour : **{valeur} g protéines/jour**")

@bot.command(name="eviter")
async def cmd_eviter(ctx, *, aliment: str):
    """!eviter brocoli — ajoute un aliment à ne plus jamais proposer"""
    p = load_profile()
    aliment = aliment.strip().lower()
    if aliment not in p["eviter"]:
        p["eviter"].append(aliment)
        save_profile(p)
        await ctx.send(f"✅ **{aliment}** ajouté à la liste des aliments à éviter.")
    else:
        await ctx.send(f"ℹ️ **{aliment}** est déjà dans ta liste.")

@bot.command(name="okeviter")
async def cmd_okeviter(ctx, *, aliment: str):
    """!okeviter brocoli — retire un aliment de la liste à éviter"""
    p = load_profile()
    aliment = aliment.strip().lower()
    if aliment in p["eviter"]:
        p["eviter"].remove(aliment)
        save_profile(p)
        await ctx.send(f"✅ **{aliment}** retiré de la liste à éviter.")
    else:
        await ctx.send(f"ℹ️ **{aliment}** n'était pas dans ta liste.")

@bot.command(name="note")
async def cmd_note(ctx, *, texte: str):
    """!note j'adore le saumon — mémorise une préférence"""
    p = load_profile()
    p["preferences"].append(texte.strip())
    save_profile(p)
    await ctx.send(f"✅ Mémorisé : _{texte}_")

@bot.command(name="aide")
async def cmd_aide(ctx):
    """!aide — liste toutes les commandes"""
    msg = """## 🤖 Commandes disponibles

**`!menu`** — Génère le menu de la semaine maintenant
**`!profil`** — Affiche tes paramètres actuels
**`!calories 3000`** — Change ton objectif calorique
**`!proteines 130`** — Change ton objectif protéines
**`!eviter brocoli`** — Ne plus jamais proposer un aliment
**`!okeviter brocoli`** — Remettre un aliment dans les suggestions
**`!note j'adore le saumon`** — Mémorise une préférence
**`!aide`** — Affiche cette aide

Le menu est envoyé automatiquement **chaque dimanche à 8h**.
"""
    await ctx.send(msg)

# ─── Lancement ────────────────────────────────────────────
bot.run(DISCORD_TOKEN)
