"""
Custom Manga/Webtoon Viewer for Kodi
- Dynamic screen resolution support
- Proper zoom modes for both Paged and Webtoon
- Background color options
- Fullscreen overlay
"""

import xbmc
import xbmcgui

# Action IDs
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_STOP = 13
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4
ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_PAGE_UP = 5
ACTION_PAGE_DOWN = 6
ACTION_SELECT_ITEM = 7
ACTION_MOUSE_WHEEL_UP = 104
ACTION_MOUSE_WHEEL_DOWN = 105
ACTION_CONTEXT_MENU = 117
ACTION_SHOW_INFO = 11
ACTION_SHOW_GUI = 18  # Another Info key variation
ACTION_SHOW_OSD = 19  # Another variation

# Zoom modes
ZOOM_FIT_WIDTH = 0
ZOOM_FIT_HEIGHT = 1
ZOOM_FIT_SCREEN = 2
ZOOM_ORIGINAL = 3

ZOOM_NAMES = ['Fit Width', 'Fit Height', 'Fit Screen', 'Original']

# Background colors (name, ARGB hex)
BG_COLORS = [
    ('Black', 'FF000000'),
    ('Dark', 'FF1a1a1a'),
    ('Gray', 'FF404040'),
    ('Light', 'FFE0E0E0'),
    ('White', 'FFFFFFFF'),
    ('Sepia', 'FFF4ECD8')
]


class FullscreenViewer(xbmcgui.WindowDialog):
    """
    Fullscreen manga viewer with proper zoom modes for both paged and webtoon.
    """
    
    def __init__(self, image_paths, start_index=0, zoom_mode=ZOOM_FIT_HEIGHT,
                 padding_percent=0, is_webtoon=False, two_page_mode=False,
                 two_page_start='single_first', bg_color_index=0, on_close_callback=None):
        super(FullscreenViewer, self).__init__()
        
        self.image_paths = image_paths
        self.current_index = start_index
        self.zoom_mode = zoom_mode
        self.padding_percent = padding_percent
        self.is_webtoon = is_webtoon
        self.two_page_mode = two_page_mode and not is_webtoon
        self.two_page_start = two_page_start
        self.bg_color_index = bg_color_index
        self.on_close_callback = on_close_callback
        
        # Get screen dimensions
        self._update_screen_size()
        
        # Scroll state for webtoon
        self.scroll_y = 0
        self.scroll_step = int(self.screen_height * 0.15)
        self.fast_scroll = int(self.screen_height * 0.5)
        
        # Manga aspect ratio (height / width)
        self.manga_aspect = 1.4
        
        # Track controls for cleanup
        self.image_controls = []
        
        self._build_ui()
        self._load_current_page()
    
    def _update_screen_size(self):
        """Get current screen dimensions"""
        self.screen_width = self.getWidth()
        self.screen_height = self.getHeight()
        
        if self.screen_width <= 0 or self.screen_height <= 0:
            try:
                self.screen_width = int(xbmc.getInfoLabel('System.ScreenWidth') or 1920)
                self.screen_height = int(xbmc.getInfoLabel('System.ScreenHeight') or 1080)
            except:
                self.screen_width = 1920
                self.screen_height = 1080
        
        if self.screen_width <= 0:
            self.screen_width = 1920
        if self.screen_height <= 0:
            self.screen_height = 1080
    
    def _get_bg_texture(self):
        """Create background with current color"""
        return 'special://xbmc/media/black.png'
    
    def _build_ui(self):
        """Build the UI"""
        # Get background color
        bg_hex = BG_COLORS[self.bg_color_index][1]
        
        # Extended background for full coverage
        self.bg_extended = xbmcgui.ControlImage(
            -100, -100,
            self.screen_width + 200, self.screen_height + 200,
            self._get_bg_texture()
        )
        self.addControl(self.bg_extended)
        self.bg_extended.setColorDiffuse('0x' + bg_hex)
        
        # Main background
        self.bg_main = xbmcgui.ControlImage(
            0, 0, self.screen_width, self.screen_height,
            self._get_bg_texture()
        )
        self.addControl(self.bg_main)
        self.bg_main.setColorDiffuse('0x' + bg_hex)
        
        # Calculate content area
        self._calculate_content_area()
        
        # Create image controls
        self._create_image_controls()
        
        # UI overlays
        self._create_ui_overlays()
    
    def _calculate_content_area(self):
        """Calculate content area with padding"""
        pad_px = int(self.screen_width * self.padding_percent / 200)
        self.content_x = pad_px
        self.content_y = 0
        self.content_w = self.screen_width - (pad_px * 2)
        self.content_h = self.screen_height
    
    def _get_webtoon_dimensions(self):
        """Get webtoon image dimensions based on zoom mode"""
        if self.zoom_mode == ZOOM_FIT_WIDTH:
            # Fill width, tall scrollable image
            w = self.content_w
            h = int(self.content_w * 3)  # Tall for scrolling
            x = self.content_x
            aspect = 2  # Scale to fill width
        elif self.zoom_mode == ZOOM_FIT_HEIGHT:
            # Fit height, center horizontally
            h = self.content_h
            w = int(h / self.manga_aspect)
            x = self.content_x + (self.content_w - w) // 2
            aspect = 1
            h = self.content_h * 2  # Still need some height for scroll
        elif self.zoom_mode == ZOOM_FIT_SCREEN:
            # Fit entire image
            w = int(self.content_h / self.manga_aspect)
            h = self.content_h
            x = self.content_x + (self.content_w - w) // 2
            aspect = 1
            h = self.content_h * 2
        else:  # Original
            w = int(self.screen_height * 0.6 / self.manga_aspect)
            h = int(self.screen_height * 0.6)
            x = self.content_x + (self.content_w - w) // 2
            aspect = 1
            h = self.content_h * 2
        
        return x, w, h, aspect
    
    def _get_paged_dimensions(self, is_two_page=False):
        """Get paged image dimensions based on zoom mode"""
        available_w = self.content_w // 2 if is_two_page else self.content_w
        available_h = self.content_h
        
        if self.zoom_mode == ZOOM_FIT_WIDTH:
            # Fill width, may crop top/bottom
            w = available_w
            h = int(available_w * self.manga_aspect)
            x = 0  # Will be adjusted
            y = (available_h - h) // 2
            aspect = 2
        elif self.zoom_mode == ZOOM_FIT_HEIGHT:
            # Fill height, bars on sides
            h = available_h
            w = int(available_h / self.manga_aspect)
            x = (available_w - w) // 2
            y = 0
            aspect = 1
        elif self.zoom_mode == ZOOM_FIT_SCREEN:
            # Fit entire image, no crop
            scale = min(available_w / (available_h / self.manga_aspect), 1.0)
            h = int(available_h * scale)
            w = int(h / self.manga_aspect)
            if w > available_w:
                w = available_w
                h = int(w * self.manga_aspect)
            x = (available_w - w) // 2
            y = (available_h - h) // 2
            aspect = 1
        else:  # Original
            h = int(self.screen_height * 0.7)
            w = int(h / self.manga_aspect)
            x = (available_w - w) // 2
            y = (available_h - h) // 2
            aspect = 1
        
        return x, y, w, h, aspect
    
    def _create_image_controls(self):
        """Create image controls"""
        # Clear existing
        for ctrl in self.image_controls:
            try:
                self.removeControl(ctrl)
            except:
                pass
        self.image_controls = []
        
        if self.is_webtoon:
            self._create_webtoon_controls()
        elif self.two_page_mode:
            self._create_two_page_controls()
        else:
            self._create_single_page_controls()
    
    def _create_single_page_controls(self):
        """Create single page control"""
        x, y, w, h, aspect = self._get_paged_dimensions()
        
        self.img_main = xbmcgui.ControlImage(
            self.content_x + x, y, w, h, '', aspectRatio=aspect
        )
        self.addControl(self.img_main)
        self.image_controls.append(self.img_main)
    
    def _create_two_page_controls(self):
        """Create two-page controls"""
        x, y, w, h, aspect = self._get_paged_dimensions(is_two_page=True)
        half = self.content_w // 2
        
        # For two-page mode, position images adjacent to each other in the center
        # Instead of centering each in their half, we center the pair
        total_width = w * 2
        gap = 4  # Small gap between pages
        left_start = self.content_x + (self.content_w - total_width - gap) // 2
        
        self.img_left = xbmcgui.ControlImage(
            left_start, y, w, h, '', aspectRatio=aspect
        )
        self.addControl(self.img_left)
        self.image_controls.append(self.img_left)
        
        self.img_right = xbmcgui.ControlImage(
            left_start + w + gap, y, w, h, '', aspectRatio=aspect
        )
        self.addControl(self.img_right)
        self.image_controls.append(self.img_right)
    
    def _create_webtoon_controls(self):
        """Create webtoon scroll controls"""
        x, w, h, aspect = self._get_webtoon_dimensions()
        self.webtoon_h = h
        self.webtoon_x = x
        self.webtoon_w = w
        self.webtoon_aspect = aspect
        
        self.img_prev = xbmcgui.ControlImage(x, -h, w, h, '', aspectRatio=aspect)
        self.addControl(self.img_prev)
        self.image_controls.append(self.img_prev)
        
        self.img_main = xbmcgui.ControlImage(x, 0, w, h, '', aspectRatio=aspect)
        self.addControl(self.img_main)
        self.image_controls.append(self.img_main)
        
        self.img_next = xbmcgui.ControlImage(x, h, w, h, '', aspectRatio=aspect)
        self.addControl(self.img_next)
        self.image_controls.append(self.img_next)
    
    def _create_ui_overlays(self):
        """Create UI overlays"""
        # Page counter (top-right)
        self.ui_page_bg = xbmcgui.ControlImage(
            self.screen_width - 200, 10, 190, 45,
            self._get_bg_texture()
        )
        self.addControl(self.ui_page_bg)
        self.ui_page_bg.setColorDiffuse('0xBB000000')
        
        self.ui_page = xbmcgui.ControlLabel(
            self.screen_width - 195, 18, 180, 35, '',
            font='font14', textColor='0xFFFFFFFF', alignment=0x02
        )
        self.addControl(self.ui_page)
        
        # Mode indicator (top-left)
        self.ui_mode_bg = xbmcgui.ControlImage(
            10, 10, 450, 45,
            self._get_bg_texture()
        )
        self.addControl(self.ui_mode_bg)
        self.ui_mode_bg.setColorDiffuse('0xBB000000')
        
        self.ui_mode = xbmcgui.ControlLabel(
            20, 18, 430, 35, '',
            font='font13', textColor='0xFFFFFFFF'
        )
        self.addControl(self.ui_mode)
        
        # Help text (bottom)
        self.ui_help_bg = xbmcgui.ControlImage(
            10, self.screen_height - 55, 800, 45,
            self._get_bg_texture()
        )
        self.addControl(self.ui_help_bg)
        self.ui_help_bg.setColorDiffuse('0x99000000')
        
        help_text = 'OK:Zoom | C/Info:Background | Menu:Padding | Back:Exit'
        if self.is_webtoon:
            help_text = 'Up/Down:Scroll | Left/Right:Page | ' + help_text
        else:
            help_text = 'Left/Right:Page | ' + help_text
        
        self.ui_help = xbmcgui.ControlLabel(
            20, self.screen_height - 47, 780, 35, help_text,
            font='font12', textColor='0xEEFFFFFF'
        )
        self.addControl(self.ui_help)
    
    def _get_two_page_indices(self):
        """Get page indices for two-page mode"""
        total = len(self.image_paths)
        if self.two_page_start == 'single_first':
            if self.current_index == 0:
                return (0, None)
            left = self.current_index if self.current_index % 2 == 1 else max(1, self.current_index - 1)
            right = left + 1 if left + 1 < total else None
            return (left, right)
        else:
            left = (self.current_index // 2) * 2
            right = left + 1 if left + 1 < total else None
            return (left, right)
    
    def _load_current_page(self):
        """Load current page(s)"""
        if self.is_webtoon:
            self._load_webtoon_pages()
        elif self.two_page_mode:
            self._load_two_pages()
        else:
            self._load_single_page()
        self._update_ui_labels()
    
    def _load_single_page(self):
        """Load single page"""
        if 0 <= self.current_index < len(self.image_paths):
            self.img_main.setImage(self.image_paths[self.current_index], useCache=False)
    
    def _load_two_pages(self):
        """Load two pages"""
        left_i, right_i = self._get_two_page_indices()
        
        if left_i is not None and 0 <= left_i < len(self.image_paths):
            self.img_left.setImage(self.image_paths[left_i], useCache=False)
            self.img_left.setVisible(True)
            self.current_index = left_i
        else:
            self.img_left.setVisible(False)
        
        if right_i is not None and 0 <= right_i < len(self.image_paths):
            self.img_right.setImage(self.image_paths[right_i], useCache=False)
            self.img_right.setVisible(True)
        else:
            self.img_right.setVisible(False)
    
    def _load_webtoon_pages(self):
        """Load webtoon pages"""
        total = len(self.image_paths)
        
        if 0 <= self.current_index < total:
            self.img_main.setImage(self.image_paths[self.current_index], useCache=False)
        
        if self.current_index > 0:
            self.img_prev.setImage(self.image_paths[self.current_index - 1], useCache=True)
            self.img_prev.setVisible(True)
        else:
            self.img_prev.setVisible(False)
        
        if self.current_index < total - 1:
            self.img_next.setImage(self.image_paths[self.current_index + 1], useCache=True)
            self.img_next.setVisible(True)
        else:
            self.img_next.setVisible(False)
        
        self._update_webtoon_positions()
    
    def _update_webtoon_positions(self):
        """Update webtoon scroll positions"""
        y = -self.scroll_y
        self.img_main.setPosition(self.webtoon_x, y)
        self.img_prev.setPosition(self.webtoon_x, y - self.webtoon_h)
        self.img_next.setPosition(self.webtoon_x, y + self.webtoon_h)
    
    def _update_ui_labels(self):
        """Update UI labels"""
        total = len(self.image_paths)
        
        if self.two_page_mode and not self.is_webtoon:
            left_i, right_i = self._get_two_page_indices()
            if right_i is not None:
                self.ui_page.setLabel(f'{left_i + 1}-{right_i + 1} / {total}')
            else:
                self.ui_page.setLabel(f'{left_i + 1} / {total}')
        else:
            self.ui_page.setLabel(f'{self.current_index + 1} / {total}')
        
        mode = 'Webtoon' if self.is_webtoon else ('2-Page' if self.two_page_mode else 'Single')
        zoom = ZOOM_NAMES[self.zoom_mode]
        bg = BG_COLORS[self.bg_color_index][0]
        pad = f' | Pad:{self.padding_percent}%' if self.padding_percent > 0 else ''
        self.ui_mode.setLabel(f'{mode} | {zoom} | BG:{bg}{pad}')
    
    def _scroll_webtoon(self, delta):
        """Scroll webtoon"""
        new_scroll = self.scroll_y + delta
        max_scroll = max(0, self.webtoon_h - self.screen_height)
        
        if new_scroll > max_scroll:
            if self.current_index < len(self.image_paths) - 1:
                self.current_index += 1
                self.scroll_y = 0
                self._load_webtoon_pages()
            else:
                self.scroll_y = max_scroll
                self._update_webtoon_positions()
        elif new_scroll < 0:
            if self.current_index > 0:
                self.current_index -= 1
                self._load_webtoon_pages()
                self.scroll_y = max(0, self.webtoon_h - self.screen_height)
                self._update_webtoon_positions()
            else:
                self.scroll_y = 0
                self._update_webtoon_positions()
        else:
            self.scroll_y = new_scroll
            self._update_webtoon_positions()
        self._update_ui_labels()
    
    def _go_next_page(self):
        """Next page"""
        total = len(self.image_paths)
        if self.two_page_mode and not self.is_webtoon:
            if self.two_page_start == 'single_first' and self.current_index == 0:
                self.current_index = 1
            else:
                self.current_index = min(self.current_index + 2, total - 1)
        else:
            if self.current_index < total - 1:
                self.current_index += 1
        self.scroll_y = 0
        self._load_current_page()
    
    def _go_prev_page(self):
        """Previous page"""
        if self.two_page_mode and not self.is_webtoon:
            if self.two_page_start == 'single_first' and self.current_index <= 2:
                self.current_index = 0
            else:
                self.current_index = max(self.current_index - 2, 0)
        else:
            if self.current_index > 0:
                self.current_index -= 1
        self.scroll_y = 0
        self._load_current_page()
    
    def _cycle_zoom(self):
        """Cycle zoom modes"""
        self.zoom_mode = (self.zoom_mode + 1) % 4
        self._create_image_controls()
        self._load_current_page()
    
    def _cycle_background(self):
        """Cycle background colors"""
        self.bg_color_index = (self.bg_color_index + 1) % len(BG_COLORS)
        bg_hex = BG_COLORS[self.bg_color_index][1]
        self.bg_extended.setColorDiffuse('0x' + bg_hex)
        self.bg_main.setColorDiffuse('0x' + bg_hex)
        self._update_ui_labels()
    
    def _cycle_padding(self):
        """Cycle padding"""
        pads = [0, 5, 10, 15, 20, 25]
        try:
            i = pads.index(self.padding_percent)
            self.padding_percent = pads[(i + 1) % len(pads)]
        except:
            self.padding_percent = 0
        self._calculate_content_area()
        self._create_image_controls()
        self._load_current_page()
    
    def onAction(self, action):
        """Handle input"""
        aid = action.getId()
        
        # Exit
        if aid in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK, ACTION_STOP):
            if self.on_close_callback:
                self.on_close_callback(self.current_index)
            self.close()
            return
        
        # Navigation
        if aid in (ACTION_MOVE_UP, ACTION_MOUSE_WHEEL_UP):
            if self.is_webtoon:
                self._scroll_webtoon(-self.scroll_step)
            else:
                self._go_prev_page()
        elif aid in (ACTION_MOVE_DOWN, ACTION_MOUSE_WHEEL_DOWN):
            if self.is_webtoon:
                self._scroll_webtoon(self.scroll_step)
            else:
                self._go_next_page()
        elif aid == ACTION_MOVE_LEFT:
            self._go_prev_page()
        elif aid == ACTION_MOVE_RIGHT:
            self._go_next_page()
        elif aid == ACTION_PAGE_UP:
            if self.is_webtoon:
                self._scroll_webtoon(-self.fast_scroll)
            else:
                self._go_prev_page()
        elif aid == ACTION_PAGE_DOWN:
            if self.is_webtoon:
                self._scroll_webtoon(self.fast_scroll)
            else:
                self._go_next_page()
        
        # OK = Cycle zoom
        elif aid == ACTION_SELECT_ITEM:
            self._cycle_zoom()
        
        # Info / C = Cycle background (multiple action codes for compatibility)
        elif aid in (ACTION_SHOW_INFO, ACTION_SHOW_GUI, ACTION_SHOW_OSD, 195):
            # 195 is typically 'c' key on keyboard
            self._cycle_background()
        
        # Context/Menu = Cycle padding
        elif aid in (ACTION_CONTEXT_MENU, 229):
            self._cycle_padding()


def show_webtoon_viewer(image_paths, start_index=0, zoom_mode=ZOOM_FIT_HEIGHT,
                        padding_percent=0, is_webtoon=False, two_page_mode=False,
                        two_page_start='single_first', on_close=None):
    """Display the fullscreen manga/webtoon viewer."""
    if not image_paths:
        return start_index
    
    final_index = [start_index]
    
    def callback(idx):
        final_index[0] = idx
        if on_close:
            on_close(idx)
    
    viewer = FullscreenViewer(
        image_paths=image_paths,
        start_index=start_index,
        zoom_mode=zoom_mode,
        padding_percent=padding_percent,
        is_webtoon=is_webtoon,
        two_page_mode=two_page_mode,
        two_page_start=two_page_start,
        bg_color_index=0,
        on_close_callback=callback
    )
    
    viewer.doModal()
    del viewer
    
    return final_index[0]
