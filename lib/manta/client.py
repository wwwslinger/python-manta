# Copyright (c) 2012 Joyent, Inc.  All rights reserved.

"""The Manta client."""

import sys
import logging
import os
from os.path import exists, join
from posixpath import join as ujoin, dirname as udirname, basename as ubasename
import json
from pprint import pprint, pformat
from urllib import urlencode
import hashlib
import datetime
import base64

import httplib2
import appdirs

from manta.version import __version__
import manta.errors as errors



#---- globals

log = logging.getLogger("manta.client")
DEFAULT_HTTP_CACHE_DIR = appdirs.user_cache_dir(
    "python-manta", "Joyent", "http")
DEFAULT_USER_AGENT = "python-manta/%s (%s) Python/%s" % (
    __version__, sys.platform, sys.version.split(None, 1)[0])



#---- compat

# Python version compat
# Use `bytes` for byte strings and `unicode` for unicode strings (str in Py3).
if sys.version_info[0] <= 2:
    py3 = False
    try:
        bytes
    except NameError:
        bytes = str
    base_string_type = basestring
elif sys.version_info[0] >= 3:
    py3 = True
    unicode = str
    base_string_type = str
    unichr = chr



#---- internal support stuff

def http_date(d=None):
    """Return HTTP Date format string for the given date.
    http://www.w3.org/Protocols/rfc2616/rfc2616-sec3.html#sec3.3.1

    @param d {datetime.datetime} Optional. Defaults to `utcnow()`.
    """
    if not d:
        d = datetime.datetime.utcnow()
    return d.strftime("%a, %d %b %Y %H:%M:%S GMT")


def _indent(s, indent='    '):
    return indent + indent.join(s.splitlines(True))

class MantaHttp(httplib2.Http):
    def _request(self, conn, host, absolute_uri, request_uri, method, body, headers, redirections, cachekey):
        if log.isEnabledFor(logging.DEBUG):
            body_str = body or ''
            if body and len(body) > 1024:
                body_str = body[:1021] + '...'
            log.debug("req: %s %s\n%s", method, request_uri,
                '\n'.join([
                    _indent("host: " + host),
                    _indent("headers: " + pformat(headers)),
                    _indent("cachekey: " + pformat(cachekey)), #XXX
                    _indent("body: " + body_str)
                ]))
        res, content = httplib2.Http._request(self, conn, host, absolute_uri, request_uri, method, body, headers, redirections, cachekey)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("res: %s %s\n%s\n%s", method, request_uri,
                _indent(pformat(res)),
                (len(content) < 1024 and _indent(content)
                 or _indent(content[:1021]+'...')))
        return (res, content)




#---- exports

class RawMantaClient(object):
    """A raw client for accessing the Manta REST API. Here "raw" means that
    the API is limited to the strict set of endpoints in the REST API. No
    sugar. See `MantaClient` for the sugar.

    http://apidocs.joyent.com/manta/manta/
    http://apidocs.joyent.com/manta/pythonsdk/

    @param url {str} The Manta URL
    @param user {str} The Manta username.
    @param signer {Signer instance} A python-manta Signer class instance
        that handles signing request to Manta using the http-signature
        auth scheme.
    @param user_agent {str} Optional. User-Agent header string.
    @param cache_dir {str} Optional. A dir to use for HTTP caching. It will
        be created as needed.
    @param disable_ssl_certificate_validation {bool} Default false.
    @param debug {bool} Optional. Default false. If true, then will log
        debugging info.
    """
    def __init__(self, url, user, sign=None, signer=None,
            user_agent=None, cache_dir=None,
            disable_ssl_certificate_validation=False,
            debug=False):
        assert user, 'user'
        # Prefer 'signer', but accept 'sign' a la node-manta.
        assert signer or sign, 'signer'
        self.url = url
        assert not url.endswith('/'), "don't want trailing '/' on url: %r" % url
        self.user = user
        self.signer = signer or sign
        self.cache_dir = cache_dir or DEFAULT_HTTP_CACHE_DIR
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.disable_ssl_certificate_validation = disable_ssl_certificate_validation
        if debug:
            # TODO: log should be `self.log`
            global log
            log.setLevel(logging.DEBUG)

    _http_cache = None
    def _get_http(self):
        if not self._http_cache:
            if not exists(self.cache_dir):
                os.makedirs(self.cache_dir)
            self._http_cache = MantaHttp(self.cache_dir,
                disable_ssl_certificate_validation=self.disable_ssl_certificate_validation)
        return self._http_cache

    def _sign_request(self, headers):
        pass

    def _request(self, path, method="GET", query=None, body=None, headers=None):
        """Make a Manta request

        ...
        @returns (res, content)
        """
        assert path.startswith('/'), "bogus path: %r" % path

        if query:
            path += '?' + urlencode(query)
        url = self.url + path
        http = self._get_http()

        ubody = body
        if body is not None and isinstance(body, dict):
            ubody = urlencode(body)
        if headers is None:
            headers = {}
        headers["User-Agent"] = self.user_agent

        # Signature auth.
        if "Date" not in headers:
            headers["Date"] = http_date()
        algorithm, fingerprint, signature = self.signer.sign(headers["Date"])
        headers["Authorization"] = \
            'Signature keyId="/%s/keys/%s",algorithm="%s" %s' % (
                self.user, fingerprint, algorithm, signature)

        return http.request(url, method, ubody, headers)

    def put_directory(self, mdir):
        """PutDirectory
        http://apidocs.joyent.com/manta/manta/#PutDirectory

        @param mdir {str} A manta path, e.g. '/trent/stor/mydir'.
        """
        log.debug('PutDirectory %r', mdir)
        headers = {
            "Content-Type": "application/json; type=directory"
        }
        res, content = self._request(mdir, "PUT", headers=headers)
        if res["status"] != "204":
            raise errors.MantaAPIError(res, content)

    def list_directory(self, mdir, limit=None, marker=None):
        """ListDirectory
        http://apidocs.joyent.com/manta/manta/#ListDirectory

        @param mdir {str} A manta path, e.g. '/trent/stor/mydir'.
        @param limit {int} Limits the number of records to come back (default
            and max is 1000).
        @param marker {str} Key name at which to start the next listing.
        @returns Directory entries (dirents). E.g.:
            [{u'mtime': u'2012-12-11T01:54:07Z', u'name': u'play', u'type': u'directory'},
             ...]
        """
        res, dirents = self.list_directory2(mdir, limit=limit, marker=marker)
        return dirents

    def list_directory2(self, mdir, limit=None, marker=None):
        """A lower-level version of `list_directory` that returns the
        response object (which includes the headers).

        ...
        @returns (res, dirents) {2-tuple}
        """
        log.debug('ListDirectory %r', mdir)

        query = {}
        if limit:
            query["limit"] = limit
        if marker:
            query["marker"] = marker

        res, content = self._request(mdir, "GET", query=query)
        if res["status"] != "200":
            raise errors.MantaAPIError(res, content)
        lines = content.split('\r\n')
        dirents = []
        for line in lines:
            if not line.strip():
                continue
            try:
                dirents.append(json.loads(line))
            except ValueError:
                raise errors.MantaError('invalid directory entry: %r' % line)
        return res, dirents

    def head_directory(self, mdir):
        """HEAD method on ListDirectory
        http://apidocs.joyent.com/manta/manta/#ListDirectory

        This is not strictly a documented Manta API call. However it is
        provided to allow access to the useful 'result-set-size' header.

        @param mdir {str} A manta path, e.g. '/trent/stor/mydir'.
        @returns The response object, which acts as a dict with the headers.
        """
        log.debug('HEAD ListDirectory %r', mdir)
        res, content = self._request(mdir, "HEAD")
        if res["status"] != "200":
            raise errors.MantaAPIError(res, content)
        return res

    def delete_directory(self, mdir):
        """DeleteDirectory
        http://apidocs.joyent.com/manta/manta/#DeleteDirectory

        @param mdir {str} A manta path, e.g. '/trent/stor/mydir'.
        """
        log.debug('DeleteDirectory %r', mdir)
        res, content = self._request(mdir, "DELETE")
        if res["status"] != "204":
            raise errors.MantaAPIError(res, content)

    def put_object(self, mpath, content=None, path=None, file=None,
                   content_length=None,
                   content_type="application/octet-stream",
                   durability_level=None):
        """PutObject
        http://apidocs.joyent.com/manta/manta/#PutObject

        Examples:
            client.put_object('/trent/stor/foo', 'foo\nbar\nbaz')
            client.put_object('/trent/stor/foo', path='path/to/foo.txt')
            client.put_object('/trent/stor/foo', file=open('path/to/foo.txt'),
                              size=11)

        One of `content`, `path` or `file` is required.

        @param mpath {str} Required. A manta path, e.g. '/trent/stor/myobj'.
        @param content {bytes}
        @param path {str}
        @param file {file-like object}
        @param content_type {string} Optional, but suggested. Default is
            'application/octet-stream'.
        @param durability_level {int} Optional. Default is 2. This tells
            Manta the number of copies to keep.
        """
        log.debug('PutObject %r', mpath)
        headers = {
            "Content-Type": content_type,
        }
        if durability_level:
            headers["x-durability-level"] = durability_level

        methods = [m for m in [content, path, file] if m is not None]
        if len(methods) != 1:
            raise errors.MantaError("exactly one of 'content', 'path' or "
                "'file' must be provided")
        if content:
            pass
        elif path:
            f = open(path)
            try:
                content = f.read()
            finally:
                f.close()
        else:
            content = f.read()
        if not isinstance(content, bytes):
            raise errors.MantaError("'content' must be bytes, not unicode")

        headers["Content-Length"] = str(len(content))
        md5 = hashlib.md5(content)
        headers["Content-MD5"] = base64.b64encode(md5.digest())
        res, content = self._request(mpath, "PUT", body=content,
                                     headers=headers)
        if res["status"] != "204":
            raise errors.MantaAPIError(res, content)

    def get_object(self, mpath, path=None, accept="*/*"):
        """GetObject
        http://apidocs.joyent.com/manta/manta/#GetObject

        @param mpath {str} Required. A manta path, e.g. '/trent/stor/myobj'.
        @param path {str} Optional. If given, the retrieved object will be
            written to the given file path instead of the content being
            returned.
        @param accept {str} Optional. Default is '*/*'. The Accept header
            for content negotiation.
        @returns {str|None} None if `path` is provided, else the object
            content.
        """
        log.debug('GetObject %r', mpath)
        headers = {
            "Accept": accept
        }

        res, content = self._request(mpath, "GET", headers=headers)
        if res["status"] not in ("200", "304"):
            raise errors.MantaAPIError(res, content)
        if len(content) != int(res["content-length"]):
            raise errors.MantaError("content-length mismatch: expected %d, "
                "got %s" % (res["content-length"], content))
        if res["content-md5"]:
            md5 = hashlib.md5(content)
            content_md5 = base64.b64encode(md5.digest())
            if content_md5 != res["content-md5"]:
                raise errors.MantaError("content-md5 mismatch: expected %d, "
                    "got %s" % (res["content-md5"], content_md5))
        if path is not None:
            f = open(path, 'wb')
            try:
                f.write(content)
            finally:
                f.close()
        else:
            return content

    def delete_object(self, mpath):
        """DeleteObject
        http://apidocs.joyent.com/manta/manta/#DeleteObject

        @param mpath {str} Required. A manta path, e.g. '/trent/stor/myobj'.
        """
        log.debug('DeleteObject %r', mpath)
        res, content = self._request(mpath, "DELETE")
        if res["status"] != "204":
            raise errors.MantaAPIError(res, content)
        return res

    def put_link(self, mpath, location):
        """PutLink
        http://apidocs.joyent.com/manta/manta/#PutLink

        @param mpath {str} Required. A manta path, e.g. '/trent/stor/mylink'.
        @param location {str} Required. The manta path to an existing target
            manta object.
        """
        log.debug('PutLink %r -> %r', mpath, location)
        headers = {
            "Content-Type": "application/json; type=link",
            "Content-Length": "0",   #XXX Needed?
            "Location": location
        }
        res, content = self._request(mpath, "PUT", headers=headers)
        if res["status"] != "204":
            raise errors.MantaAPIError(res, content)


class MantaClient(RawMantaClient):
    """A Manta client that builds on `RawMantaClient` to provide some
    API sugar.
    """
    get = RawMantaClient.get_object
    put = RawMantaClient.put_object
    rm = RawMantaClient.delete_object

    def walk(self, mtop, topdown=True):
        """`os.walk(path)` for a directory in Manta.

        A somewhat limited form in that some of the optional args to
        `os.walk` are not supported. Instead of dir *names* and file *names*,
        the dirents for those are returned. E.g.:

            >>> for dirpath, dirents, objents in client.walk('/trent/stor/test'):
            ...     pprint((dirpath, dirents, objents))
            ('/trent/stor/test',
             [{u'mtime': u'2012-12-12T05:40:23Z',
               u'name': u'__pycache__',
               u'type': u'directory'}],
             [{u'etag': u'a5ab3753-c691-4645-9c14-db6653d4f064',
               u'mtime': u'2012-12-12T05:40:22Z',
               u'name': u'test.py',
               u'size': 5627,
               u'type': u'object'},
              ...])
            ...

        @param mtop {Manta dir}
        """
        dirents = self.ls(mtop)

        mdirs, mnondirs = [], []
        for dirent in dirents.values():
            if dirent["type"] == "directory":
                mdirs.append(dirent)
            else:
                mnondirs.append(dirent)

        if topdown:
            yield mtop, mdirs, mnondirs
        for mdir in mdirs:
            mpath = ujoin(mtop, mdir["name"])
            for x in self.walk(mpath, topdown):
                yield x
        if not topdown:
            yield mtop, mdirs, mnondirs

    def ls(self, mdir, limit=None, marker=None):
        """List a directory.

        Dev Notes:
        - If `limit` and `marker` are *not* specified. This handles paging
          through a directory with more entries than Manta will return in
          one request (1000).
        - This returns a dict mapping name to dirent as a convenience.
          Note that that makes this inappropriate for streaming a huge
          listing. A streaming-appropriate `ls` will be a separate method
          if/when that is added.

        @param mdir {str} A manta directory, e.g. '/trent/stor/a-dir'.
        @returns {dict} A mapping of names to their directory entry (dirent).
        """
        assert limit is None and marker is None, "not yet implemented"
        dirents = {}

        if limit or marker:
            entries = self.list_directory(mdir, limit=limit, marker=marker)
            for entry in entries:
                dirents[entry["name"]] = entry

        else:
            # TODO: think through this with a dir adding/removing entries
            # *while* we are paging through results.
            marker = None
            while True:
                res, entries = self.list_directory2(mdir, marker=marker)
                result_set_size = int(res.get("result-set-size", 0))
                if marker:
                    entries.pop(0)  # first one is a repeat (the marker)
                for entry in entries:
                    dirents[entry["name"]] = entry
                if len(dirents) >= result_set_size:
                    break
                else:
                    marker = entries[-1]["name"]

        return dirents

    def mkdir(self, mdir, parents=False):
        """Make a directory.

        Note that this will not error out if the directory already exists
        (that is how the PutDirectory Manta API behaves).

        @param mdir {str} A manta path, e.g. '/trent/stor/mydir'.
        @param parents {bool} Optional. Default false. Like 'mkdir -p', this
            will create parent dirs as necessary.
        @param log_write {function} Optional. A `logging.Logger.debug|info|...`
            method to which to write
        """
        assert mdir.startswith('/'), "%s: invalid manta path" % mdir
        parts = mdir.split('/')
        assert len(parts) > 3, "%s: cannot create top-level dirs" % mdir
        if not parents:
            self.put_directory(mdir)
        else:
            # Find the first non-existant dir: binary search. Because
            # PutDirectory doesn't error on 'mkdir .../already-exists' we
            # don't have a way to detect a miss on `start`. So basically we
            # keep doing the binary search until we hit a close the `start`
            # to `end` gap.
            end = len(parts) + 1
            start = 4 # Index of the first possible dir to create.
            while start < end - 1:
                idx = (end - start) / 2 + start
                d = '/'.join(parts[:idx])
                try:
                    self.put_directory(d)
                except errors.MantaAPIError, ex:
                    if ex.code == 'DirectoryDoesNotExist':
                        end = idx
                    else:
                        raise
                else:
                    start = idx

            # Now need to create from (end-1, len(parts)].
            for i in range(end - 1, len(parts)):
                d = '/'.join(parts[:i])
                self.put_directory(d)

    def mkdirp(self, mdir):
        """A convenience wrapper around mkdir a la `mkdir -p`, i.e. always
        create parent dirs as necessary.

        @param mdir {str} A manta path, e.g. '/trent/stor/mydir'.
        """
        return self.mkdir(mdir, parents=True)

    def stat(self, mpath):
        """Return available dirent info for the given Manta path."""
        parts = mpath.split('/')
        if len(parts) == 0:
            raise errors.MantaError("cannot stat empty manta path: %r" % mpath)
        elif len(parts) <= 3:
            raise errors.MantaError(
                "cannot stat special manta path: %r" % mpath)
        mparent = udirname(mpath)
        name = ubasename(mpath)
        dirents = self.ls(mparent)
        if name in dirents:
            return dirents[name]
        else:
            raise errors.MantaResourceNotFoundError(
                "%s: no such object or directory" % mpath)

    def type(self, mpath):
        """Return the manta type for the given manta path.

        @param mpath {str} The manta path for which to get the type.
        @returns {str|None} The manta type, e.g. "object" or "directory",
            or None if the path doesn't exist.
        """
        try:
            return self.stat(mpath)["type"]
        except errors.MantaResourceNotFoundError, ex:
            return None
        except errors.MantaAPIError, ex:
            if ex.code in ('ResourceNotFound', 'DirectoryDoesNotExist'):
                return None
            else:
                raise