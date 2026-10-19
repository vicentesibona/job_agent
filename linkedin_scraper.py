#!/usr/bin/env python3
"""
Scraper de empleos de LinkedIn para Santiago de Chile.

Busca ofertas laborales para roles de Ciencia de Datos y Business Intelligence,
extrayendo identificador, título, empresa, ubicación, fecha de creación de la
publicación, descripción completa, responsabilidades, requisitos y metadatos relevantes.
Guarda los datos de forma incremental en una base de datos SQLite local y genera
adicionalmente archivos CSV y JSON.
"""

import argparse
import csv
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# Lista de User-Agents realistas para alternar entre peticiones
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

DEFAULT_KEYWORDS = [
    "Científico de Datos",
    "Data Scientist",
    "Business Intelligence",
    "Inteligencia de Negocios",
]

# Configuración de secciones para clasificar responsabilidades y requisitos
SECTION_PATTERNS = [
    (
        "responsabilidades",
        r"(?:responsabilidad(?:es)?|funciones(?:\s+del cargo|\s+principales)?|tu rol|el rol|tareas(?:\s+principales)?|misi[oó]n|qu[eé] har[aá]s|what you.?ll do|what you will do|responsibilities|duties|tu trabajo|tu d[ií]a a d[ií]a|desaf[ií]os)",
    ),
    (
        "requisitos",
        r"(?:requisit(?:os|o)?|requerimiento(?:s)?|perfil(?:\s+del cargo|\s+buscado|\s+deseado)?|calificacion(?:es)?|skills?|habilidades?|competencias?|conocimientos?|qu[eé] buscamos|lo que buscamos|what we are looking for|what we.?re looking for|looking for|requirements|qualifications|lo que necesitas|must have|experiencia requerida|calificaciones clave)",
    ),
    (
        "beneficios",
        r"(?:beneficio(?:s)?|ofrecemos|qu[eé] ofrecemos|lo que ofrecemos|benefits|compensaci[oó]n|nice to have|how we do make your work|lo que te ofrecemos)",
    ),
    (
        "otros",
        r"(?:sobre nosotros|acerca de|about us|qui[eé]nes somos|proceso de selecci[oó]n|c[oó]mo postular|igualdad|diversidad|apply now|postula)",
    ),
]


def init_database(db_path: str) -> sqlite3.Connection:
    """Inicializa la base de datos SQLite local y crea la tabla e índices si no existen."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")  # Mayor concurrencia y durabilidad
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS empleos (
        identificador TEXT PRIMARY KEY,
        empleo TEXT NOT NULL,
        empresa TEXT,
        ubicacion TEXT,
        url TEXT,
        fecha_creacion TEXT,            -- Fecha exacta o estimada de publicación (YYYY-MM-DD)
        fecha_publicacion_texto TEXT,   -- Texto relativo original (ej: 'Hace 16 horas')
        nivel_experiencia TEXT,
        tipo_empleo TEXT,
        funcion_laboral TEXT,
        industrias TEXT,
        num_postulantes TEXT,
        criterio_busqueda TEXT,
        fecha_extraccion TEXT,          -- Timestamp del scraping (YYYY-MM-DD HH:MM:SS)
        responsabilidades TEXT,
        requisitos TEXT,
        descripcion TEXT
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_empleos_fecha_creacion ON empleos(fecha_creacion);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_empleos_empresa ON empleos(empresa);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_empleos_criterio ON empleos(criterio_busqueda);")
    conn.commit()
    return conn


def save_job_to_db(job: Dict[str, Any], conn: sqlite3.Connection) -> None:
    """Inserta o actualiza un registro de empleo en la base de datos local SQLite."""
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO empleos (
        identificador, empleo, empresa, ubicacion, url,
        fecha_creacion, fecha_publicacion_texto, nivel_experiencia,
        tipo_empleo, funcion_laboral, industrias, num_postulantes,
        criterio_busqueda, fecha_extraccion, responsabilidades,
        requisitos, descripcion
    ) VALUES (
        :identificador, :empleo, :empresa, :ubicacion, :url,
        :fecha_creacion, :fecha_publicacion_texto, :nivel_experiencia,
        :tipo_empleo, :funcion_laboral, :industrias, :num_postulantes,
        :criterio_busqueda, :fecha_extraccion, :responsabilidades,
        :requisitos, :descripcion
    )
    ON CONFLICT(identificador) DO UPDATE SET
        empleo = excluded.empleo,
        empresa = excluded.empresa,
        ubicacion = excluded.ubicacion,
        url = excluded.url,
        fecha_creacion = excluded.fecha_creacion,
        fecha_publicacion_texto = excluded.fecha_publicacion_texto,
        nivel_experiencia = excluded.nivel_experiencia,
        tipo_empleo = excluded.tipo_empleo,
        funcion_laboral = excluded.funcion_laboral,
        industrias = excluded.industrias,
        num_postulantes = excluded.num_postulantes,
        criterio_busqueda = excluded.criterio_busqueda,
        fecha_extraccion = excluded.fecha_extraccion,
        responsabilidades = excluded.responsabilidades,
        requisitos = excluded.requisitos,
        descripcion = excluded.descripcion;
    """, job)
    conn.commit()


def get_random_headers() -> Dict[str, str]:
    """Genera cabeceras HTTP simulando un navegador moderno."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


def make_request(url: str, session: requests.Session, max_retries: int = 3, base_delay: float = 1.5) -> Optional[requests.Response]:
    """Realiza una petición HTTP con reintentos y retroceso exponencial en caso de 429 o error temporal."""
    for attempt in range(max_retries):
        try:
            resp = session.get(url, headers=get_random_headers(), timeout=12)
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 429:
                wait_time = (attempt + 1) * 6 + random.uniform(1.0, 3.0)
                print(f"⚠️  Rate limit (HTTP 429). Esperando {wait_time:.1f}s antes de reintentar...")
                time.sleep(wait_time)
            elif resp.status_code in [404, 410]:
                return None
            else:
                print(f"⚠️  HTTP {resp.status_code} al solicitar {url}. Reintento {attempt + 1}/{max_retries}...")
                time.sleep((attempt + 1) * 2)
        except requests.RequestException as e:
            print(f"⚠️  Error de conexión ({e}). Reintento {attempt + 1}/{max_retries}...")
            time.sleep((attempt + 1) * 2)
    return None


def calculate_relative_date(text: str) -> str:
    """Calcula una fecha ISO (YYYY-MM-DD) a partir de un texto relativo en español o inglés."""
    text_lower = text.lower().strip()
    today = datetime.now().date()

    if any(u in text_lower for u in ["hora", "hour", "minuto", "minute", "segundo", "second", "just now", "recién", "hoy", "today"]):
        return today.isoformat()

    if "ayer" in text_lower or "yesterday" in text_lower:
        return (today - timedelta(days=1)).isoformat()

    m_days = re.search(r"(\d+)\s*(?:d[ií]a|day)", text_lower)
    if m_days:
        return (today - timedelta(days=int(m_days.group(1)))).isoformat()

    m_weeks = re.search(r"(\d+)\s*(?:semana|week)", text_lower)
    if m_weeks:
        return (today - timedelta(weeks=int(m_weeks.group(1)))).isoformat()

    m_months = re.search(r"(\d+)\s*(?:mes|month)", text_lower)
    if m_months:
        return (today - timedelta(days=int(m_months.group(1)) * 30)).isoformat()

    return today.isoformat()


def extract_creation_date(time_elem: Optional[BeautifulSoup], fallback_text: str = "") -> Tuple[str, str]:
    """
    Extrae la fecha de creación en formato ISO (YYYY-MM-DD) y el texto descriptivo.
    LinkedIn proporciona comúnmente el atributo 'datetime' en la etiqueta <time> de la tarjeta.
    """
    if not time_elem:
        texto = fallback_text.strip()
        fecha_iso = calculate_relative_date(texto) if texto else datetime.now().strftime("%Y-%m-%d")
        return fecha_iso, texto

    texto = time_elem.get_text(strip=True) or fallback_text.strip()
    fecha_iso = time_elem.get("datetime", "").strip()

    # Si no viene el atributo datetime, se estima a partir del texto relativo
    if not fecha_iso:
        fecha_iso = calculate_relative_date(texto)

    return fecha_iso, texto


def parse_description_sections(desc_div: Optional[BeautifulSoup]) -> Tuple[str, str, str]:
    """
    Extrae la descripción completa y segmenta heurísticamente las
    responsabilidades y los requisitos si están presentes en el texto.
    """
    if not desc_div:
        return "", "", ""

    full_text = desc_div.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in full_text.split("\n") if line.strip()]

    sections: Dict[str, List[str]] = {"responsabilidades": [], "requisitos": []}
    current_sec: Optional[str] = None

    for line in lines:
        matched_sec: Optional[str] = None
        if len(line) < 100:
            for sec_name, pattern in SECTION_PATTERNS:
                if re.search(r"(?:^|[\#\*\-•\s])" + pattern, line, re.IGNORECASE):
                    if line.endswith(":") or line.endswith("?") or len(line) < 55 or line.isupper():
                        matched_sec = sec_name
                        break

        if matched_sec:
            current_sec = matched_sec
            continue

        if current_sec in sections:
            sections[current_sec].append(line)

    resp_text = "\n".join(sections["responsabilidades"]).strip()
    req_text = "\n".join(sections["requisitos"]).strip()

    return full_text, resp_text, req_text


def fetch_job_detail(job_id: str, session: requests.Session, base_delay: float) -> Dict[str, Any]:
    """Obtiene los detalles enriquecidos de un empleo mediante la API pública de LinkedIn."""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    resp = make_request(url, session, base_delay=base_delay)

    details: Dict[str, Any] = {
        "descripcion": "",
        "responsabilidades": "",
        "requisitos": "",
        "nivel_experiencia": "",
        "tipo_empleo": "",
        "funcion_laboral": "",
        "industrias": "",
        "num_postulantes": "",
        "fecha_publicacion_detallada": "",
    }

    if not resp:
        return details

    soup = BeautifulSoup(resp.text, "html.parser")

    # Descripción completa y secciones
    desc_div = soup.find("div", class_="show-more-less-html__markup")
    full_desc, resp_desc, req_desc = parse_description_sections(desc_div)
    details["descripcion"] = full_desc
    details["responsabilidades"] = resp_desc
    details["requisitos"] = req_desc

    # Criterios del empleo (Seniority level, Employment type, etc.)
    for item in soup.find_all("li", class_="description__job-criteria-item"):
        hdr = item.find("h3")
        val = item.find("span")
        if hdr and val:
            h_text = hdr.get_text(strip=True).lower()
            v_text = val.get_text(strip=True)
            if "seniority" in h_text or "nivel" in h_text:
                details["nivel_experiencia"] = v_text
            elif "employment" in h_text or "tipo" in h_text:
                details["tipo_empleo"] = v_text
            elif "job function" in h_text or "función" in h_text:
                details["funcion_laboral"] = v_text
            elif "industr" in h_text:
                details["industrias"] = v_text

    # Número de postulantes
    num_app = soup.find("span", class_="num-applicants__caption")
    if num_app:
        details["num_postulantes"] = num_app.get_text(strip=True)

    # Fecha detallada
    posted_ago = soup.find("span", class_="posted-time-ago__text")
    if posted_ago:
        details["fecha_publicacion_detallada"] = posted_ago.get_text(strip=True)

    return details


def search_jobs_page(
    keyword: str,
    location: str,
    start_offset: int,
    session: requests.Session,
    base_delay: float,
) -> List[Dict[str, Any]]:
    """Consulta una página de resultados en la búsqueda de empleos de LinkedIn."""
    encoded_kw = quote(keyword)
    encoded_loc = quote(location)
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={encoded_kw}&location={encoded_loc}&start={start_offset}"

    resp = make_request(url, session, base_delay=base_delay)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.find_all("li")
    results = []

    for card in cards:
        # Identificador del empleo
        job_id = None
        entity_div = card.find("div", {"data-entity-urn": True})
        if entity_div:
            m = re.search(r"jobPosting:(\d+)", entity_div["data-entity-urn"])
            if m:
                job_id = m.group(1)

        # Enlace del empleo
        link_elem = card.find("a", class_="base-card__full-link")
        link = link_elem["href"].split("?")[0] if link_elem and "href" in link_elem.attrs else ""

        if not job_id and link:
            m = re.search(r"-(\d{8,})", link)
            if m:
                job_id = m.group(1)

        if not job_id:
            continue

        # Título del empleo
        title_elem = card.find("h3", class_="base-search-card__title")
        title = title_elem.get_text(strip=True) if title_elem else "Sin título"

        # Empresa
        company_elem = card.find("h4", class_="base-search-card__subtitle")
        company = company_elem.get_text(strip=True) if company_elem else "Empresa no especificada"

        # Ubicación
        loc_elem = card.find("span", class_="job-search-card__location")
        loc = loc_elem.get_text(strip=True) if loc_elem else location

        # Fecha de creación y fecha en texto (de la etiqueta <time>)
        time_elem = card.find("time")
        fecha_creacion, fecha_pub_texto = extract_creation_date(time_elem)

        results.append({
            "identificador": job_id,
            "empleo": title,
            "empresa": company,
            "ubicacion": loc,
            "url": link or f"https://www.linkedin.com/jobs/view/{job_id}",
            "fecha_creacion": fecha_creacion,
            "fecha_publicacion_texto": fecha_pub_texto,
            "criterio_busqueda": keyword,
        })

    return results


def scrape_linkedin_jobs(
    keywords: List[str],
    location: str,
    total_target: int,
    base_delay: float,
    db_path: str,
) -> List[Dict[str, Any]]:
    """
    Coordina la búsqueda a través de las distintas palabras clave,
    recopilando, deduplicando y guardando incrementalmente cada empleo en la BD local.
    """
    session = requests.Session()
    conn = init_database(db_path)
    collected_jobs: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    # Cargar IDs ya presentes en la base de datos para no duplicar trabajo previo
    cursor = conn.cursor()
    cursor.execute("SELECT identificador FROM empleos;")
    for row in cursor.fetchall():
        seen_ids.add(row[0])

    print(f"\n🚀 Iniciando búsqueda de {total_target} empleos en '{location}'")
    print(f"📁 Base de datos local: {db_path} ({len(seen_ids)} empleos ya almacenados previamente)")
    print(f"📋 Términos de búsqueda: {', '.join(keywords)}\n")

    kw_idx = 0
    offsets = {kw: 0 for kw in keywords}
    consecutive_empty = {kw: 0 for kw in keywords}

    while len(collected_jobs) < total_target:
        active_keywords = [kw for kw in keywords if consecutive_empty[kw] < 3]
        if not active_keywords:
            print("⚠️ No hay más resultados disponibles para las palabras clave especificadas.")
            break

        current_kw = active_keywords[kw_idx % len(active_keywords)]
        offset = offsets[current_kw]

        print(f"🔍 Buscando '{current_kw}' (offset {offset})...")
        cards = search_jobs_page(current_kw, location, offset, session, base_delay)
        offsets[current_kw] += 10

        if not cards:
            consecutive_empty[current_kw] += 1
            kw_idx += 1
            time.sleep(base_delay + random.uniform(0.5, 1.2))
            continue

        consecutive_empty[current_kw] = 0

        for card in cards:
            if len(collected_jobs) >= total_target:
                break

            job_id = card["identificador"]
            if job_id in seen_ids:
                continue

            seen_ids.add(job_id)

            # Pausa aleatoria para evitar bloqueos
            delay = base_delay + random.uniform(0.4, 1.5)
            time.sleep(delay)

            # Extraer detalles completos
            print(f"  [{len(collected_jobs) + 1}/{total_target}] Extrayendo detalles ID {job_id}: {card['empleo'][:45]} | {card['empresa'][:25]}")
            details = fetch_job_detail(job_id, session, base_delay)

            # Usar fecha de creación obtenida de la tarjeta o calcularla
            fecha_publicacion_texto = details["fecha_publicacion_detallada"] or card["fecha_publicacion_texto"]
            fecha_creacion = card["fecha_creacion"]
            if not fecha_creacion or fecha_creacion == datetime.now().strftime("%Y-%m-%d"):
                if details["fecha_publicacion_detallada"]:
                    fecha_creacion = calculate_relative_date(details["fecha_publicacion_detallada"])

            job_record = {
                "identificador": job_id,
                "empleo": card["empleo"],
                "empresa": card["empresa"],
                "ubicacion": card["ubicacion"],
                "url": card["url"],
                "fecha_creacion": fecha_creacion,
                "fecha_publicacion_texto": fecha_publicacion_texto,
                "nivel_experiencia": details["nivel_experiencia"],
                "tipo_empleo": details["tipo_empleo"],
                "funcion_laboral": details["funcion_laboral"],
                "industrias": details["industrias"],
                "num_postulantes": details["num_postulantes"],
                "criterio_busqueda": card["criterio_busqueda"],
                "fecha_extraccion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "responsabilidades": details["responsabilidades"],
                "requisitos": details["requisitos"],
                "descripcion": details["descripcion"],
            }

            # Guardado incremental inmediato en la base de datos local
            save_job_to_db(job_record, conn)
            collected_jobs.append(job_record)

        kw_idx += 1

    conn.close()
    return collected_jobs


def save_to_csv(jobs: List[Dict[str, Any]], filepath: str) -> None:
    """Guarda la lista de empleos en formato CSV con codificación UTF-8 con BOM para Excel."""
    if not jobs:
        print("⚠️ No hay empleos para guardar en CSV.")
        return

    fieldnames = list(jobs[0].keys())
    with open(filepath, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(jobs)
    print(f"💾 Guardado CSV exitosamente en: {filepath} ({len(jobs)} registros)")


def save_to_json(jobs: List[Dict[str, Any]], filepath: str) -> None:
    """Guarda la lista de empleos en formato JSON estructurado."""
    if not jobs:
        print("⚠️ No hay empleos para guardar en JSON.")
        return

    with open(filepath, mode="w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"💾 Guardado JSON exitosamente en: {filepath} ({len(jobs)} registros)")


def show_db_stats(db_path: str) -> None:
    """Muestra estadísticas resumidas de los empleos almacenados en la base de datos local."""
    import os
    if not os.path.exists(db_path):
        print(f"\n⚠️  La base de datos '{db_path}' aún no existe.")
        print("💡 Ejecuta el scraper primero (ej: ./run.sh o python3 linkedin_scraper.py) para comenzar a recopilar ofertas.\n")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Verificar si existe la tabla 'empleos'
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='empleos';")
        if not cursor.fetchone():
            print(f"\n⚠️  La base de datos '{db_path}' no contiene la tabla 'empleos'.")
            conn.close()
            return

        cursor.execute("SELECT COUNT(*) FROM empleos;")
        total = cursor.fetchone()[0]

        print(f"\n=======================================================")
        print(f"📊 Estadísticas de la Base de Datos: {db_path}")
        print(f"=======================================================")
        print(f"  • Total de ofertas almacenadas: {total}")

        if total == 0:
            print("  (Aún no hay registros de empleos guardados)")
            print("=======================================================\n")
            conn.close()
            return

        # Rango de fechas de creación
        cursor.execute("SELECT MIN(fecha_creacion), MAX(fecha_creacion) FROM empleos WHERE fecha_creacion IS NOT NULL AND fecha_creacion != '';")
        min_date, max_date = cursor.fetchone()
        print(f"  • Rango de fecha de creación: {min_date} a {max_date}")

        # Con responsabilidades y requisitos
        cursor.execute("SELECT COUNT(*) FROM empleos WHERE responsabilidades IS NOT NULL AND responsabilidades != '';")
        con_resp = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM empleos WHERE requisitos IS NOT NULL AND requisitos != '';")
        con_req = cursor.fetchone()[0]
        print(f"  • Con responsabilidades extraídas: {con_resp}/{total} ({con_resp/total*100:.1f}%)")
        print(f"  • Con requisitos extraídos: {con_req}/{total} ({con_req/total*100:.1f}%)")

        # Top empresas
        print("\n🏢 Top 5 Empresas con más ofertas:")
        cursor.execute("SELECT empresa, COUNT(*) as c FROM empleos GROUP BY empresa ORDER BY c DESC LIMIT 5;")
        for emp, cnt in cursor.fetchall():
            print(f"    - {emp}: {cnt} empleo(s)")

        # Distribución por término de búsqueda
        print("\n🎯 Ofertas por criterio de búsqueda:")
        cursor.execute("SELECT criterio_busqueda, COUNT(*) as c FROM empleos GROUP BY criterio_busqueda ORDER BY c DESC;")
        for crit, cnt in cursor.fetchall():
            print(f"    - {crit}: {cnt}")

        # Últimos 3 empleos creados
        print("\n🆕 Últimos empleos publicados:")
        cursor.execute("SELECT identificador, empleo, empresa, fecha_creacion, url FROM empleos ORDER BY fecha_creacion DESC LIMIT 3;")
        for j_id, title, emp, f_crea, url in cursor.fetchall():
            print(f"    [{f_crea}] {title} en {emp} (ID: {j_id})")

        print("=======================================================\n")
        conn.close()
    except sqlite3.Error as e:
        print(f"❌ Error al consultar la base de datos: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Scraper de ofertas de empleo en LinkedIn para Data Science y BI en Santiago de Chile."
    )
    parser.add_argument(
        "-l", "--limit",
        type=int,
        default=100,
        help="Número total de empleos a recolectar (por defecto: 100)",
    )
    parser.add_argument(
        "--location",
        type=str,
        default="Santiago, Chile",
        help="Ubicación geográfica para la búsqueda (por defecto: 'Santiago, Chile')",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="empleos_santiago",
        help="Nombre base para los archivos de salida sin extensión (por defecto: 'empleos_santiago')",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="",
        help="Ruta personalizada para el archivo de base de datos SQLite (.db). Por defecto: <output>.db",
    )
    parser.add_argument(
        "-d", "--delay",
        type=float,
        default=1.5,
        help="Retardo base en segundos entre peticiones para evitar bloqueos (por defecto: 1.5)",
    )
    parser.add_argument(
        "-k", "--keywords",
        nargs="+",
        default=DEFAULT_KEYWORDS,
        help="Lista de palabras clave para buscar (por defecto: 'Científico de Datos', 'Data Scientist', 'Business Intelligence', 'Inteligencia de Negocios')",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Deshabilita la generación del archivo CSV.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Deshabilita la generación del archivo JSON.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Muestra estadísticas de los empleos guardados en la base de datos local y termina.",
    )

    args = parser.parse_args()
    db_file = args.db if args.db else f"{args.output}.db"

    # Si se pide únicamente mostrar estadísticas de la BD
    if args.stats:
        show_db_stats(db_file)
        return

    start_time = time.time()
    jobs = scrape_linkedin_jobs(
        keywords=args.keywords,
        location=args.location,
        total_target=args.limit,
        base_delay=args.delay,
        db_path=db_file,
    )

    elapsed = time.time() - start_time
    print(f"\n✨ Extracción finalizada: se procesaron {len(jobs)} empleos únicos en {elapsed:.1f} segundos.")
    print(f"💾 Guardados en Base de Datos SQLite: {db_file}")

    if jobs:
        if not args.no_csv:
            save_to_csv(jobs, f"{args.output}.csv")
        if not args.no_json:
            save_to_json(jobs, f"{args.output}.json")

    # Mostrar estadísticas finales de la base de datos
    show_db_stats(db_file)


if __name__ == "__main__":
    main()
