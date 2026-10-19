#!/usr/bin/env python3
"""
Script interactivo y de ejemplo para consultar la base de datos local 'empleos_santiago.db'.

Permite:
1. Listar las ofertas más recientes.
2. Buscar empleos por palabra clave (ej. Python, Power BI, GCP, SQL, etc.).
3. Ver el detalle completo (requisitos, responsabilidades) de una vacante por ID.
4. Exportar a Pandas DataFrame (si pandas está instalado).
"""

import argparse
import os
import sqlite3
import sys

DB_DEFAULT = "empleos_santiago.db"


def get_connection(db_path: str = DB_DEFAULT) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        print(f"❌ La base de datos '{db_path}' no existe.")
        print("💡 Ejecuta primero el scraper con './run.sh' para generar los datos.")
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Permite acceder a las columnas por nombre (como dict)
    return conn


def ultimos_empleos(limit: int = 10, db_path: str = DB_DEFAULT):
    """Muestra los últimos empleos según su fecha de creación."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT identificador, fecha_creacion, empleo, empresa, ubicacion, url
        FROM empleos
        ORDER BY fecha_creacion DESC
        LIMIT ?;
    """, (limit,))
    rows = cursor.fetchall()

    print(f"\n📋 Últimos {len(rows)} empleos publicados:")
    print("-" * 80)
    for r in rows:
        fecha = r["fecha_creacion"] or "S/F"
        print(f"[{fecha}] {r['empleo']}")
        print(f"  🏢 Empresa: {r['empresa']} | 📍 Ubicación: {r['ubicacion']}")
        print(f"  🔗 ID: {r['identificador']} | Link: {r['url']}\n")
    conn.close()


def buscar_empleos(termino: str, db_path: str = DB_DEFAULT):
    """Busca ofertas que mencionen una tecnología o término en requisitos o descripción."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    query = f"%{termino}%"
    cursor.execute("""
        SELECT identificador, fecha_creacion, empleo, empresa, requisitos
        FROM empleos
        WHERE empleo LIKE ? OR descripcion LIKE ? OR requisitos LIKE ?
        ORDER BY fecha_creacion DESC;
    """, (query, query, query))
    rows = cursor.fetchall()

    print(f"\n🔍 Encontrados {len(rows)} empleos que mencionan '{termino}':")
    print("-" * 80)
    for r in rows:
        fecha = r["fecha_creacion"] or "S/F"
        print(f"[{fecha}] {r['empleo']} en {r['empresa']} (ID: {r['identificador']})")
        if r["requisitos"]:
            primeros_req = r["requisitos"].split("\n")[:2]
            print(f"   Requisitos destacados: {' / '.join(primeros_req)}")
        print()
    conn.close()


def ver_detalle(identificador: str, db_path: str = DB_DEFAULT):
    """Muestra todos los campos detallados de un empleo en particular."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM empleos WHERE identificador = ?;", (identificador,))
    row = cursor.fetchone()

    if not row:
        print(f"❌ No se encontró ningún empleo con ID: {identificador}")
        conn.close()
        return

    print("\n" + "=" * 80)
    print(f"📌 EMPLEO: {row['empleo']}")
    print(f"🏢 Empresa: {row['empresa']} | 📍 Ubicación: {row['ubicacion']}")
    print(f"📅 Fecha Creación: {row['fecha_creacion']} ({row['fecha_publicacion_texto']})")
    print(f"🔗 Enlace: {row['url']}")
    print(f"💼 Seniority: {row['nivel_experiencia']} | Tipo: {row['tipo_empleo']}")
    print(f"👥 Postulantes: {row['num_postulantes']}")
    print("=" * 80)

    if row["responsabilidades"]:
        print("\n🎯 RESPONSABILIDADES:")
        print(row["responsabilidades"])

    if row["requisitos"]:
        print("\n📋 REQUISITOS:")
        print(row["requisitos"])

    print("\n📄 DESCRIPCIÓN COMPLETA:")
    print(row["descripcion"])
    print("=" * 80 + "\n")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Consulta y explora la base de datos de empleos.")
    parser.add_argument("-n", "--ultimos", type=int, default=0, help="Ver los N últimos empleos publicados.")
    parser.add_argument("-b", "--buscar", type=str, default="", help="Buscar vacantes por palabra clave (ej: Python, SQL, Power BI).")
    parser.add_argument("-i", "--id", type=str, default="", help="Ver detalle completo de un empleo por su ID.")
    parser.add_argument("--db", type=str, default=DB_DEFAULT, help="Ruta de la base de datos (por defecto: empleos_santiago.db).")

    args = parser.parse_args()

    if args.id:
        ver_detalle(args.id, args.db)
    elif args.buscar:
        buscar_empleos(args.buscar, args.db)
    elif args.ultimos > 0:
        ultimos_empleos(args.ultimos, args.db)
    else:
        # Por defecto muestra los 10 últimos
        ultimos_empleos(10, args.db)


if __name__ == "__main__":
    main()

