# -*- coding: utf-8 -*-
# Tubi account authentication
#
# Tubi retired the old ``POST https://tubitv.com/oz/auth/login/`` form post, so
# the addon can no longer trade a username/password for a ``connect.sid``
# cookie in one call. The current web client signs in like this:
#
#   1. POST /device/anonymous/signing_key  -> {"id": ..., "key": ...}
#      The request carries a PKCE style challenge, base64url(sha256(verifier)).
#   2. POST /device/anonymous/token        -> anonymous device token
#      The query string is signed TUBI-HMAC-SHA256 (an AWS SigV4 lookalike)
#      with the key handed out in step 1.
#   3. POST /api/v2/user/login             -> user access/refresh token
#      Authorization: Bearer <anonymous access token from step 2>.
#   4. POST https://tubitv.com/oz/user     -> Set-Cookie: connect.sid
#      Registers the tokens with the web frontend, which is what the /oz
#      content endpoints used by the scraper authenticate against.
#
# Tokens are cached in the addon profile so a browse or a playback does not
# repeat the whole handshake on every plugin invocation.
#
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import requests
import xbmc
import xbmcvfs

ACCOUNT_API = 'https://account.production-public.tubi.io'
WEB_API = 'https://tubitv.com'

PLATFORM = 'web'
ALGORITHM = 'TUBI-HMAC-SHA256'
SIGNED_HEADERS = 'content-type'
SIGNING_KEY_VERSION = '1.0.0'

# Refresh a token this many seconds before the server side expiry.
EXPIRY_MARGIN = 300
# Do not hammer the login endpoint when the credentials are simply wrong.
RETRY_AFTER_FAILURE = 900
TIMEOUT = 15


class TubiAuthError(Exception):
    """Raised when Tubi refuses the credentials or the handshake fails.

    ``fresh`` is False when the error is replayed from the failure cache
    rather than raised by an actual sign-in attempt, so the caller can keep
    quiet instead of nagging on every single directory listing.
    """

    def __init__(self, message, fresh=True):
        super(TubiAuthError, self).__init__(message)
        self.fresh = fresh


class TubiAuth(object):

    def __init__(self, addon):
        self.addon = addon
        self.addonName = addon.getAddonInfo('name')
        self.profile = xbmcvfs.translatePath(addon.getAddonInfo('profile'))
        self.cacheFile = os.path.join(self.profile, 'session.json')
        self.cache = self._readCache()

    # ------------------------------------------------------------------ cache

    def _readCache(self):
        try:
            with open(self.cacheFile, 'r') as fh:
                cache = json.load(fh)
            if isinstance(cache, dict):
                return cache
        except Exception:
            pass
        return {}

    def _writeCache(self):
        try:
            if not xbmcvfs.exists(self.profile):
                xbmcvfs.mkdirs(self.profile)
            with open(self.cacheFile, 'w') as fh:
                json.dump(self.cache, fh)
        except Exception as err:
            self.log('unable to store the session cache : %s' % err)

    def log(self, txt):
        xbmc.log(msg='%s : %s' % (self.addonName, txt), level=xbmc.LOGDEBUG)

    def clear(self):
        """Forget every cached token, keeping the device id."""
        self.cache = {'device_id': self.deviceId}
        self._writeCache()

    def signOut(self):
        """Tell Tubi the device is signing out, then forget it locally.

        Best effort on Tubi's side: a sign-out the server never hears about
        still has to leave the addon signed out, so the local tokens go
        whatever happens. Mirrors what the site does - the account service
        first, then the web session and its profile entry.
        """
        session = self.cache.get('user') or {}
        token = session.get('access_token')
        cookies = '; '.join(['deviceId=%s' % self.deviceId,
                             'connect.sid=%s' % session.get('connect_sid', '')])
        if token:
            self._quietly(self._post, ''.join([ACCOUNT_API, '/user_device/logout']),
                          json.dumps({'platform': PLATFORM, 'device_id': self.deviceId},
                                     separators=(',', ':')),
                          headers={'Authorization': ''.join(['Bearer ', token])})
        if session.get('tubi_id'):
            self._quietly(requests.delete,
                          ''.join([WEB_API, '/oz/user/list/', session['tubi_id']]),
                          headers={'Cookie': cookies}, timeout=TIMEOUT)
        if session.get('connect_sid'):
            self._quietly(self._post, ''.join([WEB_API, '/oz/user/logout']),
                          json.dumps({'intentional': True}, separators=(',', ':')),
                          headers={'Cookie': cookies})
        self.clear()

    def _quietly(self, call, *args, **kwargs):
        try:
            call(*args, **kwargs)
        except Exception as err:
            self.log(''.join(['sign-out step failed, carrying on : ', str(err)]))

    # ----------------------------------------------------------------- device

    @property
    def deviceId(self):
        deviceId = self.cache.get('device_id')
        if not deviceId:
            deviceId = str(uuid.uuid4())
            self.cache['device_id'] = deviceId
            self._writeCache()
        return deviceId

    # ---------------------------------------------------------------- signing

    @staticmethod
    def _codeVerifier():
        return base64.b16encode(os.urandom(16)).decode('ascii').lower()

    @staticmethod
    def _codeChallenge(verifier):
        digest = hashlib.sha256(verifier.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(digest).decode('ascii')

    @staticmethod
    def _sign(payload, path, key):
        """Sign a request body the way the Tubi web client does.

        Returns the exact body that has to go on the wire - the signature
        covers the serialised text, so it may not be re-encoded afterwards -
        together with the signature query parameters.
        """
        body = json.dumps(payload, separators=(',', ':'))
        canonical = '\n'.join(['POST',
                               path,
                               '',
                               'content-type:application/json',
                               '',
                               SIGNED_HEADERS,
                               hashlib.sha256(body.encode('utf-8')).hexdigest()])
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        stringToSign = '\n'.join([ALGORITHM,
                                  stamp,
                                  hashlib.sha256(canonical.encode('utf-8')).hexdigest()])

        signingKey = b'TUBI' + base64.b64decode(key)
        signingKey = hmac.new(signingKey, stamp.split('T')[0].encode('utf-8'), hashlib.sha256).digest()
        signingKey = hmac.new(signingKey, b'tubi_request', hashlib.sha256).digest()
        signature = hmac.new(signingKey, stringToSign.encode('utf-8'), hashlib.sha256).hexdigest()

        params = {'X-Tubi-Algorithm': ALGORITHM,
                  'X-Tubi-Date': stamp,
                  'X-Tubi-Expires': 30,
                  'X-Tubi-SignedHeaders': SIGNED_HEADERS,
                  'X-Tubi-Signature': signature}
        return body, params

    @staticmethod
    def _post(url, body, params=None, headers=None):
        postHeaders = {'Content-Type': 'application/json',
                       'Accept': 'application/json',
                       'Origin': WEB_API,
                       'Referer': ''.join([WEB_API, '/login'])}
        if headers is not None:
            postHeaders.update(headers)
        return requests.post(url, params=params, data=body.encode('utf-8'),
                             headers=postHeaders, timeout=TIMEOUT)

    @staticmethod
    def _errorMessage(response, fallback):
        try:
            body = response.json()
            return body.get('message') or body.get('code') or fallback
        except Exception:
            return fallback

    # ------------------------------------------------------- anonymous device

    def anonymousToken(self):
        """A signed device token. Every account call needs one as its bearer."""
        cached = self.cache.get('anonymous') or {}
        if cached.get('access_token') and cached.get('expires_at', 0) > time.time() + EXPIRY_MARGIN:
            return cached['access_token']

        deviceId = self.deviceId
        verifier = self._codeVerifier()
        payload = {'challenge': self._codeChallenge(verifier),
                   'version': SIGNING_KEY_VERSION,
                   'platform': PLATFORM,
                   'device_id': deviceId}
        response = self._post(''.join([ACCOUNT_API, '/device/anonymous/signing_key']),
                              json.dumps(payload, separators=(',', ':')))
        if response.status_code != 200:
            raise TubiAuthError(self._errorMessage(response, 'signing key request failed (%s)' % response.status_code))
        signingKey = response.json()

        # The key order below is part of the signature - it has to match the
        # body that is actually posted.
        payload = {'verifier': verifier,
                   'id': signingKey['id'],
                   'platform': PLATFORM,
                   'device_id': deviceId}
        body, params = self._sign(payload, '/device/anonymous/token', signingKey['key'])
        response = self._post(''.join([ACCOUNT_API, '/device/anonymous/token']), body, params=params)
        if response.status_code != 200:
            raise TubiAuthError(self._errorMessage(response, 'device token request failed (%s)' % response.status_code))

        token = response.json()
        self.cache['anonymous'] = {'access_token': token['access_token'],
                                   'refresh_token': token.get('refresh_token'),
                                   'expires_at': time.time() + int(token.get('expires_in', 0))}
        self._writeCache()
        return token['access_token']

    # ------------------------------------------------------------------ login

    def _login(self, username, password):
        payload = {'type': 'email',
                   'platform': PLATFORM,
                   'device_id': self.deviceId,
                   'credentials': {'email': username, 'password': password}}
        headers = {'Authorization': ''.join(['Bearer ', self.anonymousToken()])}
        response = self._post(''.join([ACCOUNT_API, '/api/v2/user/login']),
                              json.dumps(payload, separators=(',', ':')), headers=headers)
        if response.status_code != 200:
            raise TubiAuthError(self._errorMessage(response, 'login failed (%s)' % response.status_code))
        return response.json()

    def _webSession(self, user):
        """Hand the tokens to the frontend and collect the connect.sid cookie."""
        payload = {'authType': 'EMAIL',
                   'userId': user.get('user_id'),
                   'name': user.get('name'),
                   'first_name': user.get('first_name'),
                   'email': user.get('email'),
                   'accessToken': user.get('access_token'),
                   'refreshToken': user.get('refresh_token'),
                   'expiresIn': user.get('expires_in'),
                   'hasPassword': user.get('has_password'),
                   'hasAge': user.get('has_age'),
                   'avatarUrl': user.get('avatar_url'),
                   'tubiId': user.get('tubi_id'),
                   'kids': user.get('kids', []),
                   'isMultipleAccountsEnabled': True}
        headers = {'Cookie': ''.join(['deviceId=', self.deviceId])}
        response = self._post(''.join([WEB_API, '/oz/user']),
                              json.dumps(payload, separators=(',', ':')), headers=headers)
        if response.status_code not in (200, 201, 204):
            raise TubiAuthError(self._errorMessage(response, 'session handover failed (%s)' % response.status_code))
        return response.cookies.get('connect.sid')

    def signIn(self, username, password):
        """Sign in and cache the result. Returns the cached session."""
        user = self._login(username, password)
        session = {'user_id': user.get('user_id'),
                   'name': user.get('name'),
                   'tubi_id': user.get('tubi_id'),
                   'access_token': user.get('access_token'),
                   'refresh_token': user.get('refresh_token'),
                   'expires_at': time.time() + int(user.get('expires_in', 0)),
                   'connect_sid': self._webSession(user),
                   'credentials': self._fingerprint(username, password)}
        self.cache['user'] = session
        self.cache.pop('failure', None)
        self._writeCache()
        self.log('signed in as %s' % session.get('name'))
        return session

    @staticmethod
    def _fingerprint(username, password):
        # Only ever store a digest, never the credentials themselves. Used to
        # notice that the settings changed and the cached session is stale.
        digest = hashlib.sha256(':'.join([username, password]).encode('utf-8'))
        return digest.hexdigest()

    # -------------------------------------------------------------- addon use

    def session(self):
        """The cached session, signing in again when needed.

        ``None`` is returned when no credentials are configured - Tubi is free
        to browse, so an anonymous run is a perfectly normal outcome.
        """
        username = self.addon.getSetting('login_name')
        password = self.addon.getSetting('login_pass')
        if not username or not password:
            return None

        fingerprint = self._fingerprint(username, password)
        session = self.cache.get('user') or {}
        if session.get('credentials') != fingerprint:
            session = {}
        if session.get('connect_sid') and session.get('expires_at', 0) > time.time() + EXPIRY_MARGIN:
            return session

        failure = self.cache.get('failure') or {}
        if failure.get('credentials') == fingerprint and failure.get('at', 0) > time.time() - RETRY_AFTER_FAILURE:
            raise TubiAuthError(failure.get('message', 'login failed'), fresh=False)

        try:
            return self.signIn(username, password)
        except TubiAuthError as err:
            self.cache['failure'] = {'credentials': fingerprint,
                                     'at': time.time(),
                                     'message': str(err)}
            self._writeCache()
            raise
        except Exception as err:
            raise TubiAuthError(str(err))

    def bearer(self):
        """The token the content API authenticates with.

        The signed in user token when there is a live one, otherwise the
        anonymous device token - Tubi serves its free catalogue to either.
        """
        session = self.cache.get('user') or {}
        if session.get('access_token') and session.get('expires_at', 0) > time.time() + EXPIRY_MARGIN:
            return session['access_token']
        return self.anonymousToken()

    @property
    def signedIn(self):
        session = self.cache.get('user') or {}
        return bool(session.get('access_token'))

    @property
    def userId(self):
        """The signed in account id, which Tubi uses to personalise the guide."""
        return (self.cache.get('user') or {}).get('user_id')

    def apply(self, headers):
        """Fill in the headers the content API expects.

        Signing in is best effort: when it fails, the anonymous device token
        still gets browsing working, so the headers are filled in as far as
        they can be before the failure is reported.
        """
        error = None
        session = None
        try:
            session = self.session()
        except TubiAuthError as err:
            error = err

        cookies = [''.join(['deviceId=', self.deviceId])]
        if session and session.get('connect_sid'):
            cookies.append(''.join(['connect.sid=', session['connect_sid']]))
        headers['Cookie'] = ''.join(['; '.join(cookies), ';'])
        headers['Origin'] = WEB_API
        headers['Referer'] = ''.join([WEB_API, '/'])

        try:
            headers['Authorization'] = ''.join(['Bearer ', self.bearer()])
        except TubiAuthError as err:
            if error is None:
                error = err

        if error is not None:
            raise error
        return headers
