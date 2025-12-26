"""
Suwayomi Server GraphQL API Client
Using built-in urllib (no external dependencies)
Compatible with multiple Suwayomi-Server versions
"""
import json
import ssl
import socket
from base64 import b64encode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class SuwayomiAPI:
    """Client for Suwayomi Server GraphQL API"""
    
    def __init__(self, server_url, username=None, password=None, timeout=30):
        self.server_url = server_url.rstrip('/')
        self.graphql_url = f"{self.server_url}/api/graphql"
        self.timeout = timeout
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Kodi-Suwayomi/1.0'
        }
        
        # Set up authentication if provided
        if username and password:
            credentials = b64encode(f"{username}:{password}".encode()).decode()
            self.headers['Authorization'] = f'Basic {credentials}'
    
    def _execute_query(self, query, variables=None):
        """Execute a GraphQL query"""
        payload = {'query': query}
        if variables:
            payload['variables'] = variables
        
        data = json.dumps(payload).encode('utf-8')
        
        try:
            request = Request(self.graphql_url, data=data, method='POST')
            for key, value in self.headers.items():
                request.add_header(key, value)
            
            # Try without SSL context first (for http), with fallback for https
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
            except Exception:
                # Retry with permissive SSL context for https
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urlopen(request, timeout=self.timeout, context=ctx) as response:
                    result = json.loads(response.read().decode('utf-8'))
            
            if 'errors' in result:
                raise Exception(f"GraphQL Error: {result['errors']}")
            
            return result.get('data', {})
                
        except HTTPError as e:
            raise Exception(f"HTTP Error {e.code}: {e.reason}")
        except URLError as e:
            raise Exception(f"Connection error: {str(e.reason)}")
        except socket.timeout:
            raise Exception("Connection timed out")
        except Exception as e:
            raise Exception(f"Error: {str(e)}")
    
    def get_server_info(self):
        """Get server information"""
        query = """
        query {
            aboutServer {
                name
                version
                revision
            }
        }
        """
        return self._execute_query(query)
    
    # ==================== LIBRARY ====================
    
    def get_library_manga(self, category_id=None, offset=0, limit=50):
        """Get manga from library, optionally filtered by category"""
        # Try with unreadCount first, fallback if not available
        query = """
        query GetLibraryManga($offset: Int!, $limit: Int!) {
            mangas(
                condition: { inLibrary: true }
                offset: $offset
                first: $limit
                orderBy: TITLE
            ) {
                nodes {
                    id
                    title
                    thumbnailUrl
                    author
                    artist
                    description
                    genre
                    status
                    inLibrary
                    unreadCount
                }
                totalCount
                pageInfo {
                    hasNextPage
                    hasPreviousPage
                }
            }
        }
        """
        variables = {
            'offset': offset,
            'limit': limit
        }
        
        try:
            return self._execute_query(query, variables)
        except Exception:
            # Fallback query without unreadCount for older servers
            query_fallback = """
            query GetLibraryManga($offset: Int!, $limit: Int!) {
                mangas(
                    condition: { inLibrary: true }
                    offset: $offset
                    first: $limit
                    orderBy: TITLE
                ) {
                    nodes {
                        id
                        title
                        thumbnailUrl
                        author
                        artist
                        description
                        genre
                        status
                        inLibrary
                    }
                    totalCount
                    pageInfo {
                        hasNextPage
                        hasPreviousPage
                    }
                }
            }
            """
            return self._execute_query(query_fallback, variables)
    
    def get_categories(self):
        """Get all categories with manga count"""
        query = """
        query {
            categories {
                nodes {
                    id
                    name
                    order
                    mangas {
                        totalCount
                    }
                }
            }
        }
        """
        try:
            return self._execute_query(query)
        except Exception:
            # Fallback without manga count for older servers
            query_fallback = """
            query {
                categories {
                    nodes {
                        id
                        name
                        order
                    }
                }
            }
            """
            return self._execute_query(query_fallback)
    
    def add_manga_to_library(self, manga_id):
        """Add manga to library"""
        query = """
        mutation AddToLibrary($id: Int!) {
            updateManga(input: { id: $id, patch: { inLibrary: true } }) {
                manga {
                    id
                    inLibrary
                }
            }
        }
        """
        return self._execute_query(query, {'id': manga_id})
    
    def set_manga_categories(self, manga_id, category_ids):
        """Set manga categories"""
        query = """
        mutation UpdateMangaCategories($id: Int!, $categories: [Int!]!) {
            updateMangaCategories(input: { id: $id, patch: { addToCategories: $categories, clearCategories: true } }) {
                manga {
                    id
                    categories {
                        nodes {
                            id
                            name
                        }
                    }
                }
            }
        }
        """
        return self._execute_query(query, {'id': manga_id, 'categories': category_ids})
    
    def remove_manga_from_library(self, manga_id):
        """Remove manga from library"""
        query = """
        mutation RemoveFromLibrary($id: Int!) {
            updateManga(input: { id: $id, patch: { inLibrary: false } }) {
                manga {
                    id
                    inLibrary
                }
            }
        }
        """
        return self._execute_query(query, {'id': manga_id})
    
    # ==================== MANGA ====================
    
    def get_manga(self, manga_id):
        """Get detailed manga information"""
        query = """
        query GetManga($id: Int!) {
            manga(id: $id) {
                id
                title
                thumbnailUrl
                author
                artist
                description
                genre
                status
                url
                inLibrary
                initialized
            }
        }
        """
        return self._execute_query(query, {'id': manga_id})
    
    def refresh_manga(self, manga_id):
        """Fetch latest manga info from source"""
        query = """
        mutation FetchManga($id: Int!) {
            fetchManga(input: { id: $id }) {
                manga {
                    id
                    title
                }
            }
        }
        """
        return self._execute_query(query, {'id': manga_id})
    
    def fetch_chapters(self, manga_id):
        """Fetch chapters from source for a manga"""
        query = """
        mutation FetchChapters($id: Int!) {
            fetchChapters(input: { mangaId: $id }) {
                chapters {
                    id
                    name
                    chapterNumber
                }
            }
        }
        """
        return self._execute_query(query, {'id': manga_id})
    
    # ==================== CHAPTERS ====================
    
    def get_chapters(self, manga_id, offset=0, limit=100):
        """Get chapters for a manga"""
        query = """
        query GetChapters($mangaId: Int!, $offset: Int!, $limit: Int!) {
            chapters(
                condition: { mangaId: $mangaId }
                offset: $offset
                first: $limit
                orderBy: SOURCE_ORDER
                orderByType: DESC
            ) {
                nodes {
                    id
                    name
                    chapterNumber
                    scanlator
                    uploadDate
                    isRead
                    isDownloaded
                    isBookmarked
                    pageCount
                    lastPageRead
                    sourceOrder
                }
                totalCount
                pageInfo {
                    hasNextPage
                }
            }
        }
        """
        variables = {'mangaId': manga_id, 'offset': offset, 'limit': limit}
        return self._execute_query(query, variables)
    
    def get_chapter(self, chapter_id):
        """Get single chapter details including manga info"""
        query = """
        query GetChapter($id: Int!) {
            chapter(id: $id) {
                id
                name
                chapterNumber
                scanlator
                uploadDate
                isRead
                isDownloaded
                isBookmarked
                pageCount
                lastPageRead
                sourceOrder
                manga {
                    id
                    title
                    thumbnailUrl
                }
            }
        }
        """
        return self._execute_query(query, {'id': chapter_id})
    
    def get_chapter_pages(self, chapter_id):
        """Get pages for a chapter (fetches if not already fetched)"""
        query = """
        mutation FetchChapterPages($id: Int!) {
            fetchChapterPages(input: { chapterId: $id }) {
                pages
            }
        }
        """
        return self._execute_query(query, {'id': chapter_id})
    
    def mark_chapter_read(self, chapter_id, is_read=True):
        """Mark chapter as read or unread"""
        query = """
        mutation UpdateChapter($id: Int!, $isRead: Boolean!) {
            updateChapter(input: { id: $id, patch: { isRead: $isRead } }) {
                chapter {
                    id
                    isRead
                }
            }
        }
        """
        return self._execute_query(query, {'id': chapter_id, 'isRead': is_read})
    
    def update_chapter_progress(self, chapter_id, last_page_read):
        """Update reading progress for a chapter"""
        query = """
        mutation UpdateProgress($id: Int!, $lastPageRead: Int!) {
            updateChapter(input: { id: $id, patch: { lastPageRead: $lastPageRead } }) {
                chapter {
                    id
                    lastPageRead
                }
            }
        }
        """
        return self._execute_query(query, {'id': chapter_id, 'lastPageRead': last_page_read})
    
    def mark_chapters_read(self, chapter_ids, is_read=True):
        """Mark multiple chapters as read or unread"""
        query = """
        mutation UpdateChapters($ids: [Int!]!, $isRead: Boolean!) {
            updateChapters(input: { ids: $ids, patch: { isRead: $isRead } }) {
                chapters {
                    id
                    isRead
                }
            }
        }
        """
        return self._execute_query(query, {'ids': chapter_ids, 'isRead': is_read})
    
    # ==================== SOURCES ====================
    
    def get_sources(self, lang=None):
        """Get available sources"""
        query = """
        query {
            sources {
                nodes {
                    id
                    name
                    displayName
                    lang
                    iconUrl
                    isNsfw
                    supportsLatest
                }
            }
        }
        """
        return self._execute_query(query)
    
    def get_source(self, source_id):
        """Get source details"""
        query = """
        query GetSource($id: LongString!) {
            source(id: $id) {
                id
                name
                displayName
                lang
                iconUrl
                isNsfw
                supportsLatest
            }
        }
        """
        return self._execute_query(query, {'id': str(source_id)})
    
    def get_source_popular(self, source_id, page=1):
        """Get popular manga from a source"""
        query = """
        mutation GetPopular($sourceId: LongString!, $page: Int!) {
            fetchSourceManga(input: { source: $sourceId, type: POPULAR, page: $page }) {
                mangas {
                    id
                    title
                    thumbnailUrl
                    author
                    artist
                    description
                    inLibrary
                }
                hasNextPage
            }
        }
        """
        return self._execute_query(query, {'sourceId': str(source_id), 'page': page})
    
    def get_source_latest(self, source_id, page=1):
        """Get latest manga from a source"""
        query = """
        mutation GetLatest($sourceId: LongString!, $page: Int!) {
            fetchSourceManga(input: { source: $sourceId, type: LATEST, page: $page }) {
                mangas {
                    id
                    title
                    thumbnailUrl
                    author
                    artist
                    description
                    inLibrary
                }
                hasNextPage
            }
        }
        """
        return self._execute_query(query, {'sourceId': str(source_id), 'page': page})
    
    def search_source(self, source_id, search_term, page=1):
        """Search manga in a source"""
        query = """
        mutation SearchSource($sourceId: LongString!, $searchTerm: String!, $page: Int!) {
            fetchSourceManga(input: { source: $sourceId, type: SEARCH, query: $searchTerm, page: $page }) {
                mangas {
                    id
                    title
                    thumbnailUrl
                    author
                    artist
                    description
                    inLibrary
                }
                hasNextPage
            }
        }
        """
        return self._execute_query(query, {'sourceId': str(source_id), 'searchTerm': search_term, 'page': page})
    
    def _rest_request(self, endpoint, method='GET'):
        """Make a REST API request"""
        url = f"{self.server_url}{endpoint}"
        try:
            request = Request(url, method=method)
            for key, value in self.headers.items():
                request.add_header(key, value)
            
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            with urlopen(request, timeout=self.timeout, context=ctx) as response:
                status = response.status
                content = response.read().decode('utf-8')
                
                # Handle various status codes
                if status in (200, 201):
                    # Success - try to parse JSON if content exists
                    if content:
                        try:
                            return json.loads(content)
                        except json.JSONDecodeError:
                            return {'status': 'success', 'message': content}
                    return {'status': 'success'}
                elif status == 302:
                    # Already exists/at latest version
                    return {'status': 'already_exists'}
                else:
                    return {'status': 'unknown', 'code': status}
                    
        except HTTPError as e:
            if e.code == 302:
                return {'status': 'already_exists'}
            elif e.code == 404:
                raise Exception(f"Not found: {endpoint}")
            else:
                raise Exception(f"HTTP Error {e.code}: {e.reason}")
        except Exception as e:
            raise Exception(f"REST API error: {str(e)}")
    
    def get_source_popular_rest(self, source_id, page=1):
        """Get popular manga from source using REST API"""
        return self._rest_request(f"/api/v1/source/{source_id}/popular?pageNum={page}")
    
    def get_source_latest_rest(self, source_id, page=1):
        """Get latest manga from source using REST API"""
        return self._rest_request(f"/api/v1/source/{source_id}/latest?pageNum={page}")
    
    def search_source_rest(self, source_id, search_term, page=1):
        """Search manga in source using REST API"""
        from urllib.parse import quote
        return self._rest_request(f"/api/v1/source/{source_id}/search?searchTerm={quote(search_term)}&pageNum={page}")
    
    # ==================== EXTENSIONS ====================
    
    def get_extensions(self):
        """Get all extensions"""
        query = """
        query {
            extensions {
                nodes {
                    pkgName
                    name
                    lang
                    versionName
                    versionCode
                    iconUrl
                    apkName
                    isInstalled
                    hasUpdate
                    isObsolete
                    isNsfw
                }
            }
        }
        """
        return self._execute_query(query)
    
    def install_extension(self, pkg_name):
        """Install an extension from repository using REST API"""
        # REST API: GET /api/v1/extension/install/{pkgName}
        return self._rest_request(f"/api/v1/extension/install/{pkg_name}")
    
    def update_extension(self, pkg_name):
        """Update an extension using REST API"""
        # REST API: GET /api/v1/extension/update/{pkgName}
        return self._rest_request(f"/api/v1/extension/update/{pkg_name}")
    
    def uninstall_extension(self, pkg_name):
        """Uninstall an extension using REST API"""
        # REST API: GET /api/v1/extension/uninstall/{pkgName}
        return self._rest_request(f"/api/v1/extension/uninstall/{pkg_name}")
    
    def get_extension_icon_url(self, apk_name):
        """Get the icon URL for an extension using apkName"""
        return f"{self.server_url}/api/v1/extension/icon/{apk_name}"
    
    # ==================== DOWNLOADS ====================
    
    def get_download_status(self):
        """Get download queue status"""
        query = """
        query {
            downloadStatus {
                state
                queue {
                    state
                    progress
                    tries
                }
            }
        }
        """
        return self._execute_query(query)
    
    def enqueue_chapter_download(self, chapter_id):
        """Add chapter to download queue"""
        query = """
        mutation EnqueueDownload($id: Int!) {
            enqueueChapterDownload(input: { id: $id }) {
                downloadStatus {
                    state
                }
            }
        }
        """
        return self._execute_query(query, {'id': chapter_id})
    
    def enqueue_chapters_download(self, chapter_ids):
        """Add multiple chapters to download queue"""
        query = """
        mutation EnqueueDownloads($ids: [Int!]!) {
            enqueueChapterDownloads(input: { ids: $ids }) {
                downloadStatus {
                    state
                }
            }
        }
        """
        return self._execute_query(query, {'ids': chapter_ids})
    
    def delete_chapter_download(self, chapter_id):
        """Delete downloaded chapter"""
        query = """
        mutation DeleteDownload($id: Int!) {
            deleteDownloadedChapter(input: { id: $id }) {
                chapters {
                    id
                    isDownloaded
                }
            }
        }
        """
        return self._execute_query(query, {'id': chapter_id})
    
    def start_downloader(self):
        """Start the download queue"""
        query = """
        mutation {
            startDownloader(input: {}) {
                downloadStatus {
                    state
                }
            }
        }
        """
        return self._execute_query(query)
    
    def stop_downloader(self):
        """Stop the download queue"""
        query = """
        mutation {
            stopDownloader(input: {}) {
                downloadStatus {
                    state
                }
            }
        }
        """
        return self._execute_query(query)
    
    def clear_downloader(self):
        """Clear the download queue"""
        query = """
        mutation {
            clearDownloader(input: {}) {
                downloadStatus {
                    state
                }
            }
        }
        """
        return self._execute_query(query)
    
    # ==================== RECENT UPDATES ====================
    
    def get_recent_updates(self, offset=0, limit=50):
        """Get recently updated chapters"""
        query = """
        query GetUpdates($offset: Int!, $limit: Int!) {
            chapters(
                offset: $offset
                first: $limit
                orderBy: FETCHED_AT
                orderByType: DESC
            ) {
                nodes {
                    id
                    name
                    chapterNumber
                    uploadDate
                    isRead
                    isDownloaded
                    manga {
                        id
                        title
                        thumbnailUrl
                    }
                }
                totalCount
                pageInfo {
                    hasNextPage
                }
            }
        }
        """
        return self._execute_query(query, {'offset': offset, 'limit': limit})
    
    # ==================== READING HISTORY ====================
    
    def get_reading_history(self, offset=0, limit=50):
        """Get reading history (recently read chapters)"""
        query = """
        query GetHistory($offset: Int!, $limit: Int!) {
            chapters(
                offset: $offset
                first: $limit
                filter: { lastReadAt: { greaterThan: 0 } }
                orderBy: LAST_READ_AT
                orderByType: DESC
            ) {
                nodes {
                    id
                    name
                    chapterNumber
                    lastPageRead
                    lastReadAt
                    isRead
                    manga {
                        id
                        title
                        thumbnailUrl
                        source {
                            displayName
                        }
                    }
                }
                totalCount
                pageInfo {
                    hasNextPage
                }
            }
        }
        """
        try:
            return self._execute_query(query, {'offset': offset, 'limit': limit})
        except Exception:
            # Fallback if LAST_READ_AT ordering not supported
            query_fallback = """
            query GetHistory($offset: Int!, $limit: Int!) {
                chapters(
                    offset: $offset
                    first: $limit
                    condition: { isRead: true }
                    orderBy: ID
                    orderByType: DESC
                ) {
                    nodes {
                        id
                        name
                        chapterNumber
                        lastPageRead
                        isRead
                        manga {
                            id
                            title
                            thumbnailUrl
                        }
                    }
                    totalCount
                    pageInfo {
                        hasNextPage
                    }
                }
            }
            """
            return self._execute_query(query_fallback, {'offset': offset, 'limit': limit})
    
    # ==================== GLOBAL SEARCH ====================
    
    def global_search(self, search_term, source_ids=None):
        """Search across multiple sources"""
        query = """
        mutation GlobalSearch($searchTerm: String!) {
            fetchSourceManga(input: { type: SEARCH, query: $searchTerm }) {
                mangas {
                    id
                    title
                    thumbnailUrl
                    author
                    inLibrary
                }
                hasNextPage
            }
        }
        """
        return self._execute_query(query, {'searchTerm': search_term})
    
    # ==================== UTILITY ====================
    
    def get_thumbnail_url(self, manga_id):
        """Get the thumbnail URL for a manga"""
        return f"{self.server_url}/api/v1/manga/{manga_id}/thumbnail"
    
    def get_page_url(self, chapter_id, page_index):
        """Get the URL for a specific page"""
        return f"{self.server_url}/api/v1/manga/chapter/{chapter_id}/page/{page_index}"
    
    def get_source_icon_url(self, source_id):
        """Get the icon URL for a source"""
        return f"{self.server_url}/api/v1/source/{source_id}/icon"
    
    def get_extension_icon_url(self, pkg_name):
        """Get the icon URL for an extension"""
        return f"{self.server_url}/api/v1/extension/icon/{pkg_name}"
