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

# La URL de LeagueOfGraphs NO usa rol; conservamos "lane" por compatibilidad del comando.
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

# --- Utilidades ---
ROLES_ES = ("Superior", "Jungla", "Central", "Tirador", "Soporte")

def get_safe_champion_name_for_url(champion_name: str) -> str:
    """Normaliza el nombre al slug que usa League of Graphs."""
    name = champion_name.strip().lower()
    if name in {"jarvan iv", "jarvan 4", "jarvaniv"}: return "jarvaniv"
    if name in {"miss fortune", "missfortune"}: return "missfortune"
    if name in {"dr mundo", "dr. mundo", "doctor mundo", "drmundo"}: return "drmundo"
    if name in {"nunu & willump", "nunu y willump", "nunu-willump"}: return "nunu"
    # LoG usa "wukong"
    name = re.sub(r"['.\s]", "", name)  # kha'zix -> khazix, kai'sa -> kaisa
    return name

def strip_trailing_role(label: str) -> str:
    """Quita el sufijo de rol en español al final del nombre (p.ej. 'Briar Jungla' -> 'Briar')."""
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

def pick_wr_from_candidates(nums: List[float]) -> Optional[float]:
    """
    Si ya son porcentajes reales (1..100), elige el más cercano a 50.
    Si hay valores en 0..1, los considera como proporciones y multiplica x100.
    Filtra a (0.5..100).
    """
    norm = []
    for n in nums:
        if -1.001 <= n <= 1.001:
            n = n * 100.0
        if 0.5 <= n <= 100.0:
            norm.append(n)
    if not norm:
        return None
    return min(norm, key=lambda x: abs(x - 50.0))

def adv_to_wr(adv: float, section_is_win: bool) -> float:
    """
    Convierte ventaja (magnitud o valor en [-1,1]) a WR.
    - Si |adv| <= 1, se interpreta como fracción (e.g., -0.0979 -> -9.79 %)
    - 'gana más contra' suma; 'pierde más contra' resta.
    """
    if -1.001 <= adv <= 1.001:
        adv_pct = adv * 100.0
    else:
        adv_pct = adv
    return 50.0 + (adv_pct if section_is_win else -adv_pct)

def extract_name_from_tr(tr) -> Optional[str]:
    """Saca el nombre del campeón rival (preferencia: span.name)."""
    # 1) span.name (como en tu captura)
    span = tr.select_one("span.name")
    if span:
        t = span.get_text(" ", strip=True)
        if t:
            return strip_trailing_role(t)

    # 2) Enlace al campeón
    a = tr.select_one("td a[href]")
    if a:
        t = a.get_text(" ", strip=True)
        if t:
            return strip_trailing_role(t)

    # 3) alt de cualquier img
    for img in tr.find_all("img"):
        alt = img.get("alt")
        if alt:
            alt = strip_trailing_role(alt.strip())
            if alt:
                return alt

    # 4) Texto de la fila (último recurso)
    text = tr.get_text(" ", strip=True)
    m = re.search(r"([A-Za-zÁÉÍÓÚÜÑ' -]{2,})", text)
    if m:
        return strip_trailing_role(m.group(1).strip())

    return None

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
        # Busca h3 con “gana más contra” / “pierde más contra” y usa la tabla siguiente
        h3_targets = []
        for h3 in soup.find_all("h3"):
            txt = h3.get_text(" ", strip=True).lower()
            if "gana más contra" in txt or "pierde más contra" in txt:
                h3_targets.append(h3)

        logging.info(f"[LoL Scraper] h3 targets encontrados: {len(h3_targets)}")

        for h3 in h3_targets:
            titulo = h3.get_text(" ", strip=True).lower()
            section_is_win = "gana más contra" in titulo

            table = h3.find_next("table")
            if not table:
                # fallback: buscar dentro del box
                box = h3.find_parent("div", class_="boxContainer")
                if box:
                    table = box.select_one("table.data_table, table.sortable_table, table")
            if not table:
                logging.warning("[LoL Scraper] No se encontró <table> tras el h3 objetivo.")
                continue

            # Recorremos filas del cuerpo
            for tr in table.select("tbody tr"):
                nombre = extract_name_from_tr(tr)

                wr_candidates: List[float] = []
                adv_candidates: List[float] = []

                # 1) Todos los <progressbar> con data-value (tu captura)
                for pb in tr.select("progressbar[data-value]"):
                    v = parse_number(str(pb.get("data-value", "")))
                    if v is None:
                        continue
                    # Heurística: si el progressbar declara rango con negativo, trátalo como ventaja.
                    minv = parse_number(str(pb.get("data-minvalue", "0")))
                    maxv = parse_number(str(pb.get("data-maxvalue", "0")))
                    is_pct_flag = str(pb.get("data-ispercentage", "0")) == "1"

                    if minv is not None and maxv is not None and (minv < 0 or maxv < 0):
                        # viene como fracción o magnitud de ventaja
                        adv_candidates.append(v if abs(v) > 1.001 or not is_pct_flag else v)  # igual lo normalizamos después
                    else:
                        # Si es fracción, llevar a %
                        if -1.001 <= v <= 1.001:
                            wr_candidates.append(v * 100.0)
                        else:
                            wr_candidates.append(v)

                # 2) Porcentaje explícito en texto (por si el sitio lo incluye)
                for td in tr.find_all("td"):
                    text = td.get_text(" ", strip=True)
                    for g in re.findall(r"(-?\d+(?:[.,]\d+)?)\s*%", text):
                        v = parse_number(g)
                        if v is None:
                            continue
                        # Si es pequeño (<= 30), probablemente sea ventaja mostrada como % (no WR)
                        if abs(v) <= 30:
                            adv_candidates.append(v)
                        else:
                            wr_candidates.append(v)

                # 3) data-sort-value numérico
                for td in tr.find_all("td"):
                    dsv = td.get("data-sort-value")
                    if dsv is None:
                        continue
                    v = parse_number(str(dsv))
                    if v is None:
                        continue
                    if -1.001 <= v <= 1.001:
                        wr_candidates.append(v * 100.0)
                    elif abs(v) <= 30:
                        adv_candidates.append(v)
                    else:
                        wr_candidates.append(v)

                # Elegimos WR:
                wr = pick_wr_from_candidates(wr_candidates)

                # Si no hay WR pero sí ventaja, conviértela:
                if wr is None and adv_candidates:
                    # Toma la de mayor magnitud (más informativa)
                    adv = max(adv_candidates, key=lambda x: abs(x))
                    # Si era fracción, multiplicaremos dentro de adv_to_wr
                    wr = adv_to_wr(adv, section_is_win)

                if nombre and (wr is not None):
                    resultados.append({"champion": nombre, "win_rate_float": wr})
                else:
                    # Logs de diagnóstico por fila
                    if nombre:
                        logging.debug(f"[LoL Scraper] {nombre} — wr_cand={wr_candidates} adv_cand={adv_candidates} -> wr={wr}")
                    else:
                        logging.debug(f"[LoL Scraper] Fila sin nombre. wr_cand={wr_candidates} adv_cand={adv_candidates}")

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
        Uso: .matchups <campeon> [linea]  (ej: .matchups Zed mid)
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
                f"**{i+1}. vs {m.get('champion','???')}** — WR: **{m.get('win_rate_float',100.0):.2f}%** 👎"
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
