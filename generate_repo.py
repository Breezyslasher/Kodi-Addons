#!/usr/bin/env python3
"""
    Kodi Repository Generator
    Generates addons.xml and addons.xml.md5 for your Kodi repository
    
    Usage: Run this script from the root of your repository
"""

import os
import hashlib
import xml.etree.ElementTree as ET

# Folders to scan for addon.xml files
ADDON_FOLDERS = [
    "repository.breezyslasher",
    "context.plexkodiconnect.download",
    "plugin.audio.audiobookshelf", 
    "script.webhook.runner"
]

def get_addon_xml_content(folder):
    """Read addon.xml from a folder and return its content"""
    addon_xml_path = os.path.join(folder, "addon.xml")
    if os.path.exists(addon_xml_path):
        with open(addon_xml_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Remove XML declaration if present
            if content.startswith("<?xml"):
                content = content.split("?>", 1)[1].strip()
            return content
    return None

def generate_addons_xml():
    """Generate addons.xml from all addon folders"""
    addons_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<addons>\n'
    
    for folder in ADDON_FOLDERS:
        if os.path.isdir(folder):
            addon_content = get_addon_xml_content(folder)
            if addon_content:
                # Indent the addon content
                indented = "\n".join("    " + line if line.strip() else line 
                                    for line in addon_content.split("\n"))
                addons_xml += indented + "\n"
                print(f"Added: {folder}")
            else:
                print(f"Warning: No addon.xml found in {folder}")
        else:
            print(f"Warning: Folder not found: {folder}")
    
    addons_xml += "</addons>\n"
    return addons_xml

def generate_md5(content):
    """Generate MD5 hash of content"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()

def main():
    # Generate addons.xml
    addons_xml = generate_addons_xml()
    
    # Write addons.xml
    with open("addons.xml", "w", encoding="utf-8") as f:
        f.write(addons_xml)
    print("\nGenerated: addons.xml")
    
    # Generate and write MD5
    md5_hash = generate_md5(addons_xml)
    with open("addons.xml.md5", "w", encoding="utf-8") as f:
        f.write(md5_hash)
    print(f"Generated: addons.xml.md5 ({md5_hash})")
    
    print("\nDone! Don't forget to:")
    print("1. Create zip files for each addon in the 'zips' folder")
    print("2. Also put a copy of repository.breezyslasher zip in 'zips' folder")

if __name__ == "__main__":
    main()
