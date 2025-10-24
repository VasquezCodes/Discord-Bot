# --- bot/cogs/league.py ---
# Scraper y comando de LoL que obtiene matchups desde League of Graphs.

import discord
from discord.ext import commands
import logging
from typing import Optional, List, Dict
import requests
from bs4 import BeautifulSoup
import re
import asyncio

# --- Configuración ---
NUM_MATCHUPS_TO_SHOW = 5

# Mantengo el parseo de "lane" para compatibilidad con tu comando,
# pero LeagueOfGraphs no lo necesita en la URL.
DEFAULT_LANE = "mid"
LANE_MAP = {
    "top": "top", "superior": "top",
    "jungle": "jungle", "jg": "jungle", "jungla": "jungle",
    "mid": "mid", "middle": "mid", "medio": "mid",
    "adc": "adc", "bottom": "adc", "bot": "adc", "inferior": "adc",
    "support": "support", "sup": "support", "soporte": "support"
}

LEAGUEGRAPHS_BASE_URL = "https://www.leagueofgraphs.com/es/champions/counters/{champion}"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.google.com/",
}
# --- Fin Configuración ---


# --- Utilidades ---
ROLES_ES = ("Superior", "Jungla", "Central", "Tirador", "Soporte")

def get_safe_champion_name_for_url(champion_name: str) -> str:
    """
    Normaliza el nombre al slug que usa League of Graphs.
    """
    name = champion_name.strip().lower()
    if name in {"jarvan iv", "jarvan 4", "jarvaniv"}: return "jarvaniv"
    if name in {"miss fortune", "missfortune"}: return "missfortune"
    if name in {"dr mundo", "dr. mundo", "doctor mundo", "drmundo"}: return "drmundo"
    if name in {"nunu & willump", "nunu y willump", "nunu-willump"}: return "nunu"
    # LoG usa "wukong" (no "monkeyking")
    # quitar apóstrofes, puntos y espacios (kha'zix -> khazix, kai'sa -> kaisa)
    name = re.sub(r"['.\s]", "", name)
    return name

def strip_trailing_role(label: str) -> str:
    """
    Quita el sufijo de rol en español al final del nombre (p.ej. 'Kassadin Central' -> 'Kassadin').
    """
    label = label.strip()
    for role in ROLES_ES:
        if label.endswith(" " + role):
            return label[: -(len(role) + 1)]
    return label

def parse_number(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "."))
    except Exception:
        return None

def choose_wr_candidate(nums: List[float]) -> Optional[float]:
    """
    De varios números encontrados en la fila, elige el más plausible como WR:
    - Normaliza valores [0,1] -> [0,100]
    - Filtra a [1, 100]
    - Devuelve el más cercano a 50
    """
    norm = []
    for n in nums:
        if n <= 1.0:
            n = n * 100.0
        if 0.5 <= n <= 100.0:
            norm.append(n)
    if not norm:
        return None
    return min(norm, key=lambda x: abs(x - 50.0))
# --- Fin utilidades ---


# --- Scraper League of Graphs ---
def scrape_leagueofgraphs_matchups(champion_name: str, lane: str) -> Optional[List[Dict]]:
    """
    Devuelve TODOS los matchups de League of Graphs para el campeón dado.
    Retorna lista de dicts: [{'champion': str, 'win_rate_float': float}]
    """
    safe_name = get_safe_champion_name_for_url(champion_name)
    url = LEAGUEGRAPHS_BASE_URL.format(champion=safe_name)
    logging.info(f"[LoL Scraper] LeagueOfGraphs URL: {url}")

    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        if r.status_code in (403, 404):
            logging.error(f"[LoL Scraper] HTTP {r.status_code} para {url}")
            return None
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"[LoL Scraper] Error de red: {e}")
        return None
    except Exception as e:
        logging.error(f"[LoL Scraper] Error inesperado: {e}")
        return None

    soup = BeautifulSoup(r.content, "lxml")
    resultados: List[Dict] = []

    try:
        # Busca h3 con los títulos de interés y toma la tabla inmediatamente posterior
        h3_targets = []
        for h3 in soup.find_all("h3"):
            txt = h3.get_text(" ", strip=True).lower()
            if "gana más contra" in txt or "pierde más contra" in txt:
                h3_targets.append(h3)

        logging.info(f"[LoL Scraper] h3 targets encontrados: {len(h3_targets)}")

        for h3 in h3_targets:
            table = h3.find_next("table")
            if not table:
                logging.warning("[LoL Scraper] No se encontró <table> tras el h3 objetivo.")
                continue

            # Recorremos filas del cuerpo
            for tr in table.select("tbody tr"):
                # --- Nombre del campeón rival ---
                nombre: Optional[str] = None

                a = tr.select_one("td a[href*='/champions/']")
                if a:
                    nombre = strip_trailing_role(a.get_text(" ", strip=True))

                if not nombre:
                    img = tr.find("img")
                    if img and img.get("alt"):
                        nombre = strip_trailing_role(img["alt"].strip())

                # --- Candidatos a WinRate ---
                wr_candidates: List[float] = []

                # 1) Cualquier elemento con data-value (progress bar u otro)
                node = tr.select_one("[data-value]")
                if node and node.has_attr("data-value"):
                    v = parse_number(str(node["data-value"]))
                    if v is not None:
                        wr_candidates.append(v)

                # 2) Porcentaje en texto dentro de cualquier td
                for td in tr.find_all("td"):
                    text = td.get_text(" ", strip=True)
                    m = re.findall(r"(\d+(?:[.,]\d+)?)\s*%", text)
                    for g in m:
                        v = parse_number(g)
                        if v is not None:
                            wr_candidates.append(v)

                # 3) data-sort-value en celdas numéricas
                for td in tr.find_all("td"):
                    dsv = td.get("data-sort-value")
                    if dsv is not None:
                        v = parse_number(str(dsv))
                        if v is not None:
                            wr_candidates.append(v)

                wr = choose_wr_candidate(wr_candidates)

                if nombre and (wr is not None):
                    resultados.append({"champion": nombre, "win_rate_float": wr})
                else:
                    # Log de diagnóstico por fila
                    if nombre and not wr_candidates:
                        logging.debug(f"[LoL Scraper] Sin WR en fila para {nombre}")
                    elif nombre:
                        logging.debug(f"[LoL Scraper] WR candidatos {wr_candidates} -> elegido {wr} para {nombre}")
                    else:
                        logging.debug(f"[LoL Scraper] Fila sin nombre utilizable: {tr.get_text(' ', strip=True)[:120]}")

    except Exception:
        logging.exception("[LoL Scraper] Error al parsear HTML de LeagueOfGraphs.")
        return None

    if not resultados:
        logging.warning("[LoL Scraper] No se extrajeron matchups válidos de LeagueOfGraphs.")
        return []

    logging.info(f"[LoL Scraper] Extraídos {len(resultados)} matchups.")
    return resultados


# --- Cog de Discord ---
class LeagueCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="matchups", aliases=["counters", "c", "m"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def get_matchups(self, ctx: commands.Context, *, query: str):
        """
        Muestra los mejores y peores matchups para un campeón.
        Uso: .matchups <campeon> [linea]  (ej: .matchups Kai'Sa adc)
        * La línea hoy no afecta la URL de LeagueOfGraphs, se mantiene por compatibilidad.
        """
        query = query.strip()
        champion_name: Optional[str] = None
        target_lane: Optional[str] = None
        posibles_lineas = list(LANE_MAP.keys())
        palabras = query.split()
        for i in range(len(palabras), 0, -1):
            posible_lane = " ".join(palabras[i - 1:]).lower()
            if posible_lane in posibles_lineas:
                target_lane = LANE_MAP[posible_lane]
                champion_name = " ".join(palabras[:i - 1])
                break
        if champion_name is None:
            champion_name = query
            target_lane = DEFAULT_LANE
        if not champion_name:
            await ctx.send(f"⚠️ ¡No especificaste un campeón! Uso: `{ctx.prefix}matchups <campeon> [linea]`")
            return

        logging.info(f".matchups => Campeón='{champion_name}', Línea='{target_lane}'")

        async with ctx.typing():
            loop = asyncio.get_event_loop()
            all_matchups = await loop.run_in_executor(
                None,
                scrape_leagueofgraphs_matchups,
                champion_name,
                target_lane  # no se usa en la URL
            )

        embed_title = f"Matchups para {champion_name.capitalize()} ({target_lane.capitalize()})"

        if all_matchups is None:
            embed = discord.Embed(
                title=embed_title,
                description="❌ Error al buscar en League of Graphs.",
                color=discord.Color.red()
            )
        elif not all_matchups:
            embed = discord.Embed(
                title=embed_title,
                description="❓ No se encontraron datos de matchups.",
                color=discord.Color.orange()
            )
        else:
            orden_desc = sorted(all_matchups, key=lambda x: x.get('win_rate_float', -1.0), reverse=True)
            mejores = orden_desc[:NUM_MATCHUPS_TO_SHOW]

            orden_asc = sorted(all_matchups, key=lambda x: x.get('win_rate_float', 101.0))
            peores = orden_asc[:NUM_MATCHUPS_TO_SHOW]

            embed = discord.Embed(title=embed_title, color=discord.Color.blue())

            mejores_lines = [
                f"**{i+1}. vs {m.get('champion','???')}** — WR: **{m.get('win_rate_float',0.0):.2f}%** 👍"
                for i, m in enumerate(mejores)
            ]
            embed.add_field(
                name=f"✅ Top {len(mejores_lines)} Mejores Matchups",
                value="\n".join(mejores_lines) or "—",
                inline=False
            )

            peores_lines = [
                f"**{i+1}. vs {m.get('champion','???')}** — WR: **{m.get('win_rate_float',100.0)::.2f}%** 👎"
                for i, m in enumerate(peores)
            ]
            embed.add_field(
                name=f"❌ Top {len(peores_lines)} Peores Matchups (Counters)",
                value="\n".join(peores_lines) or "—",
                inline=False
            )

            embed.set_footer(text=f"Datos de League of Graphs | Matchups analizados: {len(all_matchups)}")

        await ctx.send(embed=embed)

    @get_matchups.error
    async def matchups_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            if getattr(error, "param", None) and error.param.name == 'query':
                await ctx.send(f"⚠️ ¡Especifica un campeón! Uso: `{ctx.prefix}matchups <campeon> [linea]`")
            else:
                await ctx.send("⚠️ Faltan argumentos.")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Espera {error.retry_after:.1f} segundos.", delete_after=5)
        else:
            logging.error(f"Error inesperado en comando .matchups: {error}")
            await ctx.send("❌ Ocurrió un error inesperado.")


# --- Setup del COG ---
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueCog(bot))
    logging.info("Cog de League of Legends cargado.")
