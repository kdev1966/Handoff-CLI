#!/usr/bin/env python3
import os
import sys
import sqlite3
import json

DB_PATH = os.path.expanduser("~/.mcp_memory/global_projects.db")
CONFIG_PATH = os.path.expanduser("~/.config/mcp/config.json")

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def get_db_connection():
    if not os.path.exists(DB_PATH):
        return None
    try:
        return sqlite3.connect(DB_PATH)
    except sqlite3.Error:
        return None

def show_status():
    disabled = os.environ.get("MCP_DISABLE", "false") == "true"
    status_str = "⏸️  EN PAUSE" if disabled else "🟢 ACTIF"
    print(f"\n--- Statut de l'écosystème Handoff ---")
    print(f"État actuel : {status_str}")
    print(f"Base de données : {DB_PATH}")

def list_projects():
    conn = get_db_connection()
    if not conn:
        print("⚠️ Aucun projet indexé pour le moment dans Handoff-CLI.")
        return None
    cursor = conn.cursor()
    cursor.execute("SELECT project_name, project_path, updated_at FROM project_memory")
    rows = cursor.fetchall()
    print(f"\n--- Workspaces gérés par Handoff-CLI ({len(rows)}) ---")
    for idx, row in enumerate(rows, start=1):
        print(f"[{idx}] Projet : {row[0]}")
        print(f"    Chemin : {row[1]}")
        print(f"    Dernier Handoff : {row[2]}\n")
    conn.close()
    return rows

def purge_project():
    projects = list_projects()
    if not projects:
        return
    try:
        choice = int(input("👉 Numéro du projet à détacher de Handoff-CLI : ")) - 1
        if 0 <= choice < len(projects):
            path_to_delete = projects[choice][1]
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM project_memory WHERE project_path=?", (path_to_delete,))
            conn.commit()
            conn.close()
            
            ctx_file = os.path.join(path_to_delete, ".ai_context.md")
            if os.path.exists(ctx_file):
                os.remove(ctx_file)
            print("✅ Contexte projet purgé avec succès.")
        else:
            print("⚠️ Choix hors limites.")
    except (ValueError, IndexError):
        print("⚠️ Sélection invalide.")

def uninstall_system():
    print("\n🛑 ATTENTION : Vous allez supprimer entièrement Handoff-CLI de votre machine.")
    confirm = input("Confirmer la désinstallation globale ? (y/N) : ")
    if confirm.lower() != 'y':
        return
    
    # 1. Nettoyage du hook de terminal
    for rc in [".bashrc", ".zshrc"]:
        rc_path = os.path.expanduser(f"~/{rc}")
        if os.path.exists(rc_path):
            with open(rc_path, 'r') as f:
                lines = f.readlines()
            with open(rc_path, 'w') as f:
                skip = False
                for line in lines:
                    if "# Initialisation automatique de la mémoire IA" in line:
                        skip = True
                    if skip and "mcp-init" in line:
                        skip = False
                        continue
                    if not skip:
                        f.write(line)
            print(f"  - Nettoyage du terminal terminé ({rc})")
            
    # 2. Retrait du serveur MCP
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                data = json.load(f)
            if 'mcpServers' in data and 'global-memory' in data['mcpServers']:
                del data['mcpServers']['global-memory']
            with open(CONFIG_PATH, 'w') as f:
                json.dump(data, f, indent=4)
            print("  - Serveur Handoff dissocié de la configuration MCP.")
        except Exception:
            print("  - Échec lors de la mise à jour du fichier config.json.")

    # 3. Suppression des exécutables
    for f in ["~/.local/bin/mcp-init", "~/.local/bin/mcp-dev-manager"]:
        p = os.path.expanduser(f)
        if os.path.exists(p):
            os.remove(p)
    print("  - Binaires locaux de Handoff-CLI supprimés.")
    
    purge_db = input("\nVoulez-vous aussi effacer définitivement la base de données SQLite globale ? (y/N) : ")
    if purge_db.lower() == 'y':
        db_dir = os.path.expanduser("~/.mcp_memory")
        if os.path.exists(db_dir):
            import shutil
            shutil.rmtree(db_dir)
            print("  - Base de données locale nettoyée.")
        
    print("\n🎉 Handoff-CLI a été entièrement retiré de votre machine.")
    sys.exit(0)

def main():
    while True:
        clear_screen()
        print("=========================================")
        print("          🔀 HANDOFF-CLI MANAGER         ")
        print("=========================================")
        show_status()
        print("\n Lister tous les projets sous contrôle")
        print(" Purger la mémoire d'un projet spécifique")
        print(" Désinstaller entièrement Handoff-CLI")
        print(" Quitter")
        
        choice = input("\n👉 Option (1-4) : ")
        if choice == '1':
            list_projects()
            input("\nAppuyez sur [Entrée] pour continuer...")
        elif choice == '2':
            purge_project()
            input("\nAppuyez sur [Entrée] pour continuer...")
        elif choice == '3':
            uninstall_system()
        elif choice == '4':
            break

if __name__ == "__main__":
    main()
