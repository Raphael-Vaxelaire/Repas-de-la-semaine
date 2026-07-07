import os
import json
import asyncio
import aiohttp
from datetime import datetime
import hikari
import lightbulb
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─── Config ───────────────────────────────────────────────
DISCORD_TOKEN  = os.environ["DISCORD_TOKEN"]
DISCORD_CH_ID  = int(os.environ["DISCORD_CHANNEL_ID"])
GEMINI_KEY     = os.environ["GEMINI_KEY"]
PROFILE_FILE   = "profile.json"

# ─── Profil utilisateur ───────────────────────────────────
DEFAULT_PROFILE = {
    "kcal": 3000,
    "proteines": 130,
    "eviter": [],
    "historique": [],
    "preferences": [],
    "dernier_menu": None,
}

def load_profile():
    if os.path.exists(PROFILE_FILE):
        with open(PROFILE_FILE) as f:
            p = json.load(f)
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

# ─── Prompt ───────────────────────────────────────────────
def build_prompt(profile: dict) -> str:
    eviter_str = ", ".join(profile["eviter"]) if profile["eviter"] else "aucun"
    histo_str  = ", ".join(profile["historique"][-14:]) if profile["historique"] else "aucun"
    prefs_str  = "; ".join(profile["preferences"]) if profile["preferences"] else "aucune"
    kcal       = profile["kcal"]
    prot       = profile["proteines"]

    return f"""Tu es un nutritionniste expert en batch cooking. Génère un menu de la semaine pour UNE personne.

PROFIL :
- Objectif : {kcal} kcal/jour, {prot} g de protéines/jour
- À NE JAMAIS utiliser : {eviter_str}
- Plats récents à ne pas répéter : {histo_str}
- Préférences : {prefs_str}

PRINCIPE : week-end on cuisine en grande quantité (batch), semaine on réchauffe (5 min max).
4 repas/jour : petit-déjeuner ({round(kcal*0.24)} kcal), déjeuner ({round(kcal*0.32)} kcal), collation ({round(kcal*0.12)} kcal), dîner ({round(kcal*0.32)} kcal).

Réponds UNIQUEMENT en JSON valide :
{{
  "batch_weekend": [
    {{"title": "Poulet rôti", "portions": 3, "time_min": 35, "ingr": [["Poulet", "600g", 7.20]], "steps": ["étape 1"], "prix_total": 8.50}}
  ],
  "jours": [
    {{
      "jour": "Samedi",
      "weekend": true,
      "petit": {{"title": "...", "time_min": 10, "kcal": {round(kcal*0.24)}, "prot": {round(prot*0.24)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}},
      "midi":  {{"title": "...", "time_min": 25, "kcal": {round(kcal*0.32)}, "prot": {round(prot*0.32)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}},
      "collation": {{"title": "...", "time_min": 3, "kcal": {round(kcal*0.12)}, "prot": {round(prot*0.12)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}},
      "soir": {{"title": "...", "time_min": 30, "kcal": {round(kcal*0.32)}, "prot": {round(prot*0.32)}, "ingr": [["Nom","qté",prix]], "steps": ["..."]}}
    }}
  ],
  "liste_courses": [
    {{"nom": "Poulet", "quantite": "600g", "prix_estime": 7.20, "categorie": "Viandes & Poissons"}}
  ],
  "budget_total": 75.50
}}

7 jours : Samedi, Dimanche, Lundi, Mardi, Mercredi, Jeudi, Vendredi.
Semaine (Lundi-Vendredi) : time_min 5, steps = réchauffage uniquement."""

# ─── Formater pour Discord ────────────────────────────────
SLOT_EMOJI = {"petit": "☀️", "midi": "🥗", "collation": "🍎", "soir": "🌙"}

def format_messages(menu: dict, profile: dict) -> list[str]:
    messages = []
    semaine = datetime.now().strftime("Semaine du %d/%m/%Y")

    # Message 1 : intro + batch
    m = f"# 🍽️ Menu de la semaine — {semaine}\n"
    m += f"**{profile['kcal']} kcal · {profile['proteines']}g protéines / jour**\n\n"
    m += "## 🍳 Ce week-end tu cuisines\n"
    for b in menu.get("batch_weekend", []):
        m += f"**{b['title']}** — {b['portions']} portions · {b['time_min']} min · {b.get('prix_total',0):.2f}€\n"
    messages.append(m)

    # Un message par jour
    for j in menu.get("jours", []):
        nom = j["jour"]
        tag = "🍳 Cuisine" if j.get("weekend") else "♻️ Batch"
        msg = f"## {'🟡' if j.get('weekend') else '🔵'} {nom} — {tag}\n"
        total_kcal = total_prot = 0
        for slot in ["petit", "midi", "collation", "soir"]:
            r = j.get(slot, {})
            if not r:
                continue
            k, p, t = r.get("kcal",0), r.get("prot",0), r.get("time_min",0)
            total_kcal += k
            total_prot += p
            time_str = f"⏱{t}min" if t > 5 else "♻️5min"
            msg += f"{SLOT_EMOJI[slot]} **{r.get('title','?')}** — {time_str} · {k}kcal · {p}g prot\n"
        msg += f"\n> **Total : {total_kcal} kcal · {total_prot}g protéines**\n"
        messages.append(msg)

    # Liste de courses
    cats = {}
    for item in menu.get("liste_courses", []):
        cats.setdefault(item.get("categorie","Autre"), []).append(item)
    mc = "## 🛒 Liste de courses\n"
    for cat, items in cats.items():
        mc += f"\n**{cat}**\n"
        for i in items:
            mc += f"• {i['nom']} — {i['quantite']} (~{i.get('prix_estime',0):.2f}€)\n"
    mc += f"\n💰 **Budget estimé : {menu.get('budget_total',0):.2f}€**\n"
    mc += "\n_Commandes : `!menu` `!eviter X` `!note texte` `!aide`_"
    messages.append(mc)

    return messages

# ─── Bot ──────────────────────────────────────────────────
bot = lightbulb.BotApp(token=DISCORD_TOKEN)
scheduler = AsyncIOScheduler()

async def send_menu(channel_id: int):
    profile = load_profile()
    ch = await bot.rest.fetch_channel(channel_id)
    await bot.rest.create_message(channel_id, "⏳ Génération du menu avec Gemini…")
    try:
        menu = await call_gemini(build_prompt(profile))
    except Exception as e:
        await bot.rest.create_message(channel_id, f"❌ Erreur Gemini : {e}")
        return

    # Mémoriser l'historique
    titres = [j.get(s,{}).get("title","") for j in menu.get("jours",[]) for s in ["petit","midi","collation","soir"] if j.get(s,{}).get("title")]
    profile["historique"] = (profile["historique"] + titres)[-28:]
    profile["dernier_menu"] = datetime.now().isoformat()
    save_profile(profile)

    for msg in format_messages(menu, profile):
        if msg.strip():
            await bot.rest.create_message(channel_id, msg[:1990])

@bot.listen(hikari.StartedEvent)
async def on_started(event):
    print(f"✅ Bot connecté")
    scheduler.add_job(lambda: asyncio.create_task(send_menu(DISCORD_CH_ID)),
                      "cron", day_of_week="sun", hour=8, minute=0)
    scheduler.start()
    print("⏰ Scheduler démarré — menu chaque dimanche à 8h")

# ─── Commandes ────────────────────────────────────────────
@bot.command
@lightbulb.command("menu", "Génère le menu de la semaine maintenant")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_menu(ctx):
    await ctx.respond("⏳ Génération en cours…")
    await send_menu(ctx.channel_id)

@bot.command
@lightbulb.command("profil", "Affiche ton profil")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_profil(ctx):
    p = load_profile()
    msg  = f"## 👤 Ton profil\n"
    msg += f"**Calories :** {p['kcal']} kcal/jour\n"
    msg += f"**Protéines :** {p['proteines']} g/jour\n"
    msg += f"**À éviter :** {', '.join(p['eviter']) if p['eviter'] else 'rien'}\n"
    msg += f"**Préférences :** {'; '.join(p['preferences']) if p['preferences'] else 'aucune'}\n"
    msg += f"**Dernier menu :** {p['dernier_menu'] or 'jamais'}\n"
    await ctx.respond(msg)

@bot.command
@lightbulb.command("calories", "Change ton objectif calorique")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_calories(ctx):
    args = ctx.event.message.content.split()
    if len(args) < 2 or not args[1].isdigit():
        await ctx.respond("Usage : `!calories 3000`"); return
    p = load_profile()
    p["kcal"] = int(args[1])
    save_profile(p)
    await ctx.respond(f"✅ Objectif : **{p['kcal']} kcal/jour**")

@bot.command
@lightbulb.command("proteines", "Change ton objectif protéines")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_proteines(ctx):
    args = ctx.event.message.content.split()
    if len(args) < 2 or not args[1].isdigit():
        await ctx.respond("Usage : `!proteines 130`"); return
    p = load_profile()
    p["proteines"] = int(args[1])
    save_profile(p)
    await ctx.respond(f"✅ Objectif : **{p['proteines']} g protéines/jour**")

@bot.command
@lightbulb.command("eviter", "Ajoute un aliment à ne plus jamais proposer")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_eviter(ctx):
    parts = ctx.event.message.content.split(maxsplit=1)
    if len(parts) < 2:
        await ctx.respond("Usage : `!eviter brocoli`"); return
    p = load_profile()
    aliment = parts[1].strip().lower()
    if aliment not in p["eviter"]:
        p["eviter"].append(aliment)
        save_profile(p)
        await ctx.respond(f"✅ **{aliment}** ajouté à la liste à éviter.")
    else:
        await ctx.respond(f"ℹ️ **{aliment}** est déjà dans ta liste.")

@bot.command
@lightbulb.command("okeviter", "Retire un aliment de la liste à éviter")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_okeviter(ctx):
    parts = ctx.event.message.content.split(maxsplit=1)
    if len(parts) < 2:
        await ctx.respond("Usage : `!okeviter brocoli`"); return
    p = load_profile()
    aliment = parts[1].strip().lower()
    if aliment in p["eviter"]:
        p["eviter"].remove(aliment)
        save_profile(p)
        await ctx.respond(f"✅ **{aliment}** retiré de la liste à éviter.")
    else:
        await ctx.respond(f"ℹ️ **{aliment}** n'était pas dans ta liste.")

@bot.command
@lightbulb.command("note", "Mémorise une préférence")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_note(ctx):
    parts = ctx.event.message.content.split(maxsplit=1)
    if len(parts) < 2:
        await ctx.respond("Usage : `!note j'adore le saumon`"); return
    p = load_profile()
    p["preferences"].append(parts[1].strip())
    save_profile(p)
    await ctx.respond(f"✅ Mémorisé : _{parts[1].strip()}_")

@bot.command
@lightbulb.command("aide", "Affiche toutes les commandes")
@lightbulb.implements(lightbulb.PrefixCommand)
async def cmd_aide(ctx):
    await ctx.respond("""## 🤖 Commandes
`!menu` — Génère le menu maintenant
`!profil` — Tes paramètres actuels
`!calories 3000` — Change l'objectif calorique
`!proteines 130` — Change l'objectif protéines
`!eviter brocoli` — Ne plus jamais proposer cet aliment
`!okeviter brocoli` — Remettre un aliment
`!note j'adore le saumon` — Mémorise une préférence
`!aide` — Cette aide

⏰ Menu automatique chaque **dimanche à 8h**""")

# ─── Lancement ────────────────────────────────────────────
bot.run()
