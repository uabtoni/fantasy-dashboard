"""
Scraper de FutbolFantasy (equipos de LaLiga) -> Supabase

Fuente de datos (verificado contra el HTML real del sitio, no adivinado):

1. https://www.futbolfantasy.com/analytics/laliga-fantasy/mercado
   Esta tabla se pinta con JavaScript (por eso hace falta Playwright, un
   navegador headless real -- con requests normal devuelve 0 filas).
   Cada fila es <tr class="elemento_jugador" data-nombre="..."
   data-posicion="..." data-valor="..."> y esos atributos ya traen,
   directamente y sin necesidad de parsear texto, el NOMBRE, la POSICIÓN
   y el PRECIO actual de cada jugador de LaLiga. Es la fuente principal:
   una sola carga para los ~374+ jugadores de toda la liga.

2. Página de alineación probable de cada equipo (team_url, con requests
   normal, sin Playwright) -> únicamente para el ESTADO (Disponible /
   Lesionado-Duda) vía el atributo data-lesion de cada tarjeta de
   jugador, que no viene en la tabla de mercado.
"""

import time
import random
import re
import logging
import os
import unicodedata
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = "https://www.futbolfantasy.com"
TEAMS_URL = BASE_URL
MERCADO_URL = f"{BASE_URL}/analytics/laliga-fantasy/mercado"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.google.com/"
}

POSICIONES_VALIDAS = {"Portero", "Defensa", "Mediocampista", "Delantero"}


def normalizar(texto):
    """minusculas, sin acentos, sin espacios sobrantes -> para cruzar nombres con fiabilidad"""
    texto = (texto or "").lower().strip()
    texto = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii')
    return " ".join(texto.split())


def get_html(url):
    try:
        time.sleep(random.uniform(1.0, 2.5))
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logging.error(f"Error al acceder a {url}: {e}")
        return None


def fetch_teams():
    html = get_html(TEAMS_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    teams_links = []
    candidates = soup.select("a.team") or soup.select('a[href*="/laliga/equipos/"]')

    for link in candidates:
        href = link.get('href')
        if not href or "/equipos/" not in href:
            continue
        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        full_url = full_url.split("?")[0].rstrip("/")
        if full_url not in teams_links:
            teams_links.append(full_url)

    primera_division = teams_links[:20]
    logging.info(f"Se han filtrado {len(primera_division)} equipos de Primera División.")
    return primera_division


def fetch_status_map(team_url):
    """{nombre_normalizado: estado} sacado de la página de alineación probable del equipo."""
    html = get_html(team_url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    status_map = {}
    player_cards = soup.select("a.juggador") or soup.select('a[href*="/jugadores/"]')

    for row in player_cards:
        nombre_tag = row.select_one(".truncate-name")
        nombre = nombre_tag.text.strip() if nombre_tag else row.get_text(strip=True)
        if not nombre:
            continue
        lesion_code = row.get('data-lesion', '-1')
        estado = "Disponible" if lesion_code == "-1" else "Lesionado/Duda"
        status_map[normalizar(nombre)] = estado

    return status_map


def fetch_all_market_data():
    """
    Único fetch con Playwright a la tabla de Mercado de LaLiga Fantasy
    Oficial (SIN filtrar por equipo). Lee directamente los atributos
    data-nombre / data-posicion / data-valor de cada <tr
    class="elemento_jugador">. Devuelve una lista de dicts con nombre,
    equipo, posición y precio para TODOS los jugadores de LaLiga.
    """
    jugadores = []
    vistos = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])

        # No necesitamos que las imágenes/fuentes/CSS de fondo lleguen a
        # descargarse de verdad (solo leemos la URL en el atributo src del
        # HTML), así que las bloqueamos: en un runner de GitHub Actions
        # (datacenter) estos recursos pueden ir mucho más lentos que desde
        # una conexión doméstica y son la causa más probable de que
        # "wait_until=load" se quedara colgado hasta el timeout.
        page.route(
            re.compile(r"\.(png|jpg|jpeg|gif|svg|webp|woff2?|ttf)(\?.*)?$", re.IGNORECASE),
            lambda route: route.abort()
        )

        logging.info(f"Cargando {MERCADO_URL} con navegador headless...")

        cargado = False
        ultimo_error = None
        for intento in range(1, 4):
            try:
                # domcontentloaded en vez de load: no esperamos anuncios,
                # analíticas ni recursos de terceros, solo el HTML/JS base.
                page.goto(MERCADO_URL, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_selector("tr.elemento_jugador", timeout=30000)
                cargado = True
                break
            except Exception as e:
                ultimo_error = e
                logging.warning(f"Intento {intento}/3 fallido al cargar la tabla de mercado: {e}")
                page.wait_for_timeout(5000 * intento)  # backoff antes de reintentar

        if not cargado:
            logging.error(f"No se pudo cargar la tabla de mercado tras 3 intentos: {ultimo_error}")
            browser.close()
            return []

        page.wait_for_timeout(2000)

        # El banner de cookies no bloquea los datos (ya están en el DOM
        # aunque el banner tape la pantalla), pero lo cerramos por si
        # interfiere con el botón "Siguiente" de la paginación.
        for selector in ["button:has-text('Aceptar todo')", "text=Aceptar todo"]:
            try:
                boton = page.query_selector(selector)
                if boton:
                    boton.click(timeout=3000)
                    page.wait_for_timeout(1000)
                    logging.info("Banner de cookies cerrado.")
                    break
            except Exception:
                continue

        pagina = 1
        while True:
            page.wait_for_timeout(800)
            filas = page.query_selector_all("tr.elemento_jugador")
            nuevos = 0

            for fila in filas:
                data_id = fila.get_attribute("data-id")
                if not data_id or data_id in vistos:
                    continue

                posicion_raw = fila.get_attribute("data-posicion") or ""
                if posicion_raw not in POSICIONES_VALIDAS:
                    continue  # descarta entrenadores u otras filas no-jugador

                valor_raw = fila.get_attribute("data-valor") or "0"
                try:
                    precio = int(valor_raw)
                except ValueError:
                    precio = 0

                # Diferencia oficial respecto al mercado anterior (la que
                # el propio sitio muestra en la columna "Diferencia").
                diff_raw = fila.get_attribute("data-diferencia1")
                try:
                    diferencia = int(diff_raw) if diff_raw is not None else 0
                except ValueError:
                    diferencia = 0

                nombre_tag = fila.query_selector(".player-name span")
                nombre = nombre_tag.inner_text().strip() if nombre_tag else (fila.get_attribute("data-nombre") or "").title()

                equipo_tag = fila.query_selector(".player-equipo span")
                equipo = equipo_tag.inner_text().strip() if equipo_tag else ""

                posicion = "Centrocampista" if posicion_raw == "Mediocampista" else posicion_raw

                # Foto del jugador: viene en un <img class="player-foto">
                # dentro de la propia fila. Pedimos una versión más grande
                # (400x400) cambiando el tramo de la URL, ya que la tabla
                # solo carga miniaturas de 80x80.
                foto_url = None
                foto_tag = fila.query_selector("img.player-foto")
                if foto_tag:
                    src = foto_tag.get_attribute("src") or ""
                    if src:
                        foto_url = src.replace("/thumb/80x80/", "/thumb/400x400/")

                # Próximo rival y probabilidad de titularidad: ya vienen
                # en esta misma fila, en el título del bloque ".rival-probability"
                # (ej. "Jornada 2 · Próximo rival: Elche (Fuera)") y en un
                # span interno con la probabilidad ("80%").
                next_rival, rival_home, probabilidad = None, None, None
                rival_el = fila.query_selector(".rival-probability")
                if rival_el:
                    titulo = rival_el.get_attribute("title") or ""
                    m = re.search(r'Próximo rival:\s*(.+?)\s*\((Casa|Fuera)\)', titulo)
                    if m:
                        next_rival = m.group(1).strip()
                        rival_home = (m.group(2) == "Casa")
                    prob_el = rival_el.query_selector("[class*='prob-']")
                    if prob_el:
                        prob_texto = prob_el.inner_text().strip().replace("%", "")
                        if prob_texto.isdigit():
                            probabilidad = int(prob_texto)

                vistos.add(data_id)
                nuevos += 1
                jugadores.append({
                    "nombre": nombre,
                    "team": equipo,
                    "position": posicion,
                    "price": precio,
                    "price_diff": diferencia,
                    "next_rival": next_rival,
                    "rival_home": rival_home,
                    "start_probability": probabilidad,
                    "photo_url": foto_url,
                })

            logging.info(f"Mercado - página {pagina}: {nuevos} jugadores nuevos (total: {len(jugadores)})")

            siguiente = page.query_selector("a:has-text('Siguiente')")
            if not siguiente:
                logging.info("Sin botón de siguiente página: fin de la paginación.")
                break
            clase = siguiente.get_attribute("class") or ""
            if "disabled" in clase:
                logging.info("Botón de siguiente página deshabilitado: fin de la paginación.")
                break
            try:
                siguiente.click()
                pagina += 1
                if pagina > 50:
                    logging.warning("Límite de seguridad de 50 páginas alcanzado.")
                    break
            except Exception as e:
                logging.info(f"No se pudo avanzar de página ({e}): fin de la paginación.")
                break

        browser.close()

    logging.info(f"Mercado: {len(jugadores)} jugadores obtenidos en total.")
    return jugadores


def scrape_all_data():
    logging.info("Descargando tabla de Mercado (nombre + posición + precio) para toda La Liga...")
    jugadores_mercado = fetch_all_market_data()

    team_urls = fetch_teams()

    logging.info("Descargando estado (lesión/disponible) de cada equipo...")
    status_map = {}
    for url in team_urls:
        status_map.update(fetch_status_map(url))

    all_players = []
    for j in jugadores_mercado:
        estado = status_map.get(normalizar(j["nombre"]), "Disponible")
        player_id = f'{j["team"]}-{j["nombre"]}'.lower().replace(" ", "-")

        all_players.append({
            "id": player_id,
            "name": j["nombre"],
            "team": j["team"],
            "position": j["position"],
            "price": j["price"],
            "price_diff": j.get("price_diff", 0),
            "points": 0,
            "status": estado,
            "url": "",
            "stats": {},
            "next_rival": j.get("next_rival"),
            "rival_home": j.get("rival_home"),
            "start_probability": j.get("start_probability"),
            "photo_url": j.get("photo_url"),
        })

    return all_players


def update_database(players_list):
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

    if not SUPABASE_URL or not SUPABASE_KEY:
        logging.warning("SUPABASE_URL / SUPABASE_KEY no configuradas como variables de entorno. "
                         "No se sube nada, solo se muestra en consola.")
        return

    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

        logging.info("Subiendo jugadores a Supabase...")
        for player in players_list:
            supabase.table('players').upsert(player).execute()
        logging.info("¡Jugadores actualizados!")

        # Histórico de precios: una fila nueva por jugador en cada
        # ejecución (no se sobrescribe nada), para poder pintar la
        # evolución del precio más adelante.
        logging.info("Guardando histórico de precios...")
        historial = [
            {"player_id": p["id"], "price": p["price"]}
            for p in players_list if p["price"] and p["price"] > 0
        ]
        # Insertamos en bloques de 200 para no exceder límites de tamaño de payload
        BLOQUE = 200
        for i in range(0, len(historial), BLOQUE):
            supabase.table('price_history').insert(historial[i:i + BLOQUE]).execute()
        logging.info(f"Histórico guardado: {len(historial)} registros.")

        logging.info("¡Base de datos actualizada con éxito!")
    except Exception as e:
        logging.error(f"Error al subir a Supabase: {e}")


if __name__ == "__main__":
    logging.info("Iniciando extracción de FutbolFantasy...")
    datos_jugadores = scrape_all_data()
    logging.info(f"Extracción completada. Total de jugadores: {len(datos_jugadores)}")

    if datos_jugadores:
        con_precio = sum(1 for p in datos_jugadores if p["price"] > 0)
        con_posicion = sum(1 for p in datos_jugadores if p["position"] != "Desconocida")
        logging.info(f"Con precio > 0: {con_precio}/{len(datos_jugadores)}")
        logging.info(f"Con posición conocida: {con_posicion}/{len(datos_jugadores)}")

        print("\nEjemplo de 3 jugadores extraídos:")
        for p in datos_jugadores[-3:]:
            print(p)

        update_database(datos_jugadores)
