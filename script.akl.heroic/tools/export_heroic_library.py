import os
import json

HEROIC_STORE_CACHE = os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic/store_cache")
OUTPUT_FILE = "akl_heroic_import.json"

def load_games_from_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("games", [])
    except Exception as e:
        print(f"[DEBUG] Failed to read {filepath}: {e}")
        return []

def main():
    all_games = []
    if not os.path.exists(HEROIC_STORE_CACHE):
        print(f"[DEBUG] Store cache not found: {HEROIC_STORE_CACHE}")
        return

    for fname in os.listdir(HEROIC_STORE_CACHE):
        if not fname.endswith("_library.json"):
            continue
        fpath = os.path.join(HEROIC_STORE_CACHE, fname)
        print(f"[DEBUG] Reading {fpath} ...")
        games = load_games_from_file(fpath)
        print(f"[DEBUG]   Found {len(games)} entries")
        for g in games:
            title = g.get("title", "Unknown Title")
            runner = g.get("runner", "unknown")
            app_name = g.get("app_name")
            install_path = g.get("install", {}).get("install_path") or g.get("install", {}).get("path") or ""

            print(f"[DEBUG]     Added! runner={runner}, app_name={app_name}, path={install_path}")

            all_games.append({
                "title": title,
                "runner": runner,
                "app_name": app_name,
                "install_path": install_path,
                "launch_cmd": f"heroic run {app_name}" if app_name else ""
            })

    print(f"[DEBUG] Total games collected: {len(all_games)}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(all_games, out, indent=2)
    print(f"[DEBUG] Exported {len(all_games)} Heroic games to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
