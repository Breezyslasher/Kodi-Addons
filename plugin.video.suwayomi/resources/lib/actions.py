"""
Library and chapter actions for Suwayomi addon
"""

import xbmc
import xbmcgui
import xbmcaddon

from .helpers import (
    log_info, log_error, get_setting, get_setting_bool,
    show_notification, show_error, show_yesno_dialog,
    get_hidden_sources, ADDON
)

ADDON = xbmcaddon.Addon()


def add_to_library(api, manga_id):
    """Add manga to library with optional category selection"""
    try:
        categories_result = api.get_categories()
        categories = categories_result.get('categories', {}).get('nodes', [])
        
        selected_category_ids = []
        
        if categories:
            category_names = [cat.get('name', f"Category {cat.get('id')}") for cat in categories]
            
            dialog = xbmcgui.Dialog()
            
            if dialog.yesno("Add to Library", "Do you want to select categories?", 
                           nolabel="Use Default", yeslabel="Select Categories"):
                selected = dialog.multiselect("Select Categories", category_names)
                
                if selected is not None and len(selected) > 0:
                    selected_category_ids = [categories[i].get('id') for i in selected]
        
        api.add_manga_to_library(manga_id)
        
        if selected_category_ids:
            try:
                api.set_manga_categories(manga_id, selected_category_ids)
                cat_names = [categories[i].get('name') for i in range(len(categories)) if categories[i].get('id') in selected_category_ids]
                show_notification(f"Added to: {', '.join(cat_names)}")
            except Exception as e:
                log_error(f"Failed to set categories: {e}")
                show_notification("Added to library (category setting failed)")
        else:
            show_notification("Added to library")
        
        xbmc.executebuiltin('Container.Refresh')
        
    except Exception as e:
        show_error(f"Error: {str(e)}")


def remove_from_library(api, manga_id):
    """Remove manga from library"""
    if not show_yesno_dialog("Confirm", "Remove from library?"):
        return
    
    try:
        api.remove_manga_from_library(manga_id)
        show_notification("Removed from library")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def refresh_manga(api, manga_id):
    """Refresh manga info"""
    try:
        api.refresh_manga(manga_id)
        show_notification("Manga refreshed")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def mark_chapter_read(api, chapter_id):
    """Mark chapter as read"""
    try:
        api.mark_chapter_read(chapter_id, True)
        show_notification("Marked as read")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def mark_chapter_unread(api, chapter_id):
    """Mark chapter as unread"""
    try:
        api.mark_chapter_read(chapter_id, False)
        show_notification("Marked as unread")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def mark_all_chapters_read(api, manga_id):
    """Mark all chapters as read"""
    if not show_yesno_dialog("Confirm", "Mark all chapters as read?"):
        return
    
    try:
        result = api.get_manga(manga_id)
        manga = result.get('manga', {})
        chapters = manga.get('chapters', {}).get('nodes', [])
        
        dialog = xbmcgui.DialogProgress()
        dialog.create('Marking chapters...', 'Please wait')
        
        for i, chapter in enumerate(chapters):
            if dialog.iscanceled():
                break
            dialog.update(int((i / len(chapters)) * 100))
            if not chapter.get('isRead'):
                api.mark_chapter_read(chapter['id'], True)
        
        dialog.close()
        show_notification("All chapters marked as read")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def download_chapter(api, chapter_id):
    """Download chapter"""
    try:
        api.download_chapter(chapter_id)
        show_notification("Download started")
    except Exception as e:
        show_error(f"Error: {str(e)}")


def delete_chapter_download(api, chapter_id):
    """Delete downloaded chapter"""
    try:
        api.delete_downloaded_chapter(chapter_id)
        show_notification("Download deleted")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def download_all_chapters(api, manga_id):
    """Download all chapters"""
    if not show_yesno_dialog("Confirm", "Download all chapters?"):
        return
    
    try:
        result = api.get_manga(manga_id)
        manga = result.get('manga', {})
        chapters = manga.get('chapters', {}).get('nodes', [])
        
        for chapter in chapters:
            if not chapter.get('isDownloaded'):
                api.download_chapter(chapter['id'])
        
        show_notification(f"Started downloading {len(chapters)} chapters")
    except Exception as e:
        show_error(f"Error: {str(e)}")


def hide_source(source_name):
    """Add a source to the hidden sources list"""
    if not source_name:
        return
    
    hidden = get_setting('hidden_sources') or ''
    hidden_list = [s.strip() for s in hidden.split(',') if s.strip()]
    
    if source_name.lower() not in [h.lower() for h in hidden_list]:
        hidden_list.append(source_name)
        ADDON.setSetting('hidden_sources', ', '.join(hidden_list))
        show_notification(f"Hidden: {source_name}")
        xbmc.executebuiltin('Container.Refresh')


def manage_hidden_sources():
    """Show dialog to manage hidden sources"""
    hidden = get_setting('hidden_sources') or ''
    hidden_list = [s.strip() for s in hidden.split(',') if s.strip()]
    
    if not hidden_list:
        show_notification("No hidden sources")
        return
    
    dialog = xbmcgui.Dialog()
    
    while True:
        options = hidden_list + ["[Clear All]", "[Done]"]
        selected = dialog.select("Hidden Sources (select to unhide)", options)
        
        if selected == -1 or selected == len(options) - 1:
            break
        elif selected == len(options) - 2:
            if dialog.yesno("Clear All", "Remove all hidden sources?"):
                ADDON.setSetting('hidden_sources', '')
                show_notification("All sources unhidden")
                break
        else:
            removed = hidden_list.pop(selected)
            ADDON.setSetting('hidden_sources', ', '.join(hidden_list))
            show_notification(f"Unhidden: {removed}")
            if not hidden_list:
                break
    
    xbmc.executebuiltin('Container.Refresh')


def start_downloader(api):
    """Start downloader"""
    try:
        api.start_downloader()
        show_notification("Downloader started")
    except Exception as e:
        show_error(f"Error: {str(e)}")


def stop_downloader(api):
    """Stop downloader"""
    try:
        api.stop_downloader()
        show_notification("Downloader stopped")
    except Exception as e:
        show_error(f"Error: {str(e)}")


def clear_downloader(api):
    """Clear download queue"""
    try:
        api.clear_downloader()
        show_notification("Download queue cleared")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def install_extension(api, pkg_name):
    """Install extension"""
    try:
        api.install_extension(pkg_name)
        show_notification("Extension installed")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def update_extension(api, pkg_name):
    """Update extension"""
    try:
        api.update_extension(pkg_name)
        show_notification("Extension updated")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")


def uninstall_extension(api, pkg_name):
    """Uninstall extension"""
    if not show_yesno_dialog("Confirm", "Uninstall extension?"):
        return
    
    try:
        api.uninstall_extension(pkg_name)
        show_notification("Extension uninstalled")
        xbmc.executebuiltin('Container.Refresh')
    except Exception as e:
        show_error(f"Error: {str(e)}")
