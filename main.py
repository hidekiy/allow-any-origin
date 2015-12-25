#coding=utf-8
import logging
import re
import sys
import hashlib
from time import time

from google.appengine.api import memcache
from google.appengine.api import urlfetch
from google.appengine.ext import ndb
from google.appengine.runtime import apiproxy_errors

import webapp2

class Quota(ndb.Model):
	count = ndb.IntegerProperty()
	bytes = ndb.IntegerProperty()
	reset_interval = ndb.IntegerProperty()
	default_id = 'default'

class UrlResp:
	def __init__(self, res):
		self.status_code = res.status_code
		self.headers = res.headers
		self.content = res.content
	
class HttpProxyHandler(webapp2.RequestHandler):
	quota = None
	quota_refreshed_at = 0
	quota_refresh_seconds = 60
	urlfetch_cache_seconds = 60

	@classmethod
	def _refresh_quota(cls):
		if time() - cls.quota_refreshed_at > cls.quota_refresh_seconds:
			cls.quota_refreshed_at = time()
			quota = Quota.get_by_id(Quota.default_id);
			logging.info('_refresh_quota: %r', quota)
			cls.quota = quota

	def abort(self, *args, **kwargs):
		super(HttpProxyHandler, self).abort(
			headers={'Access-Control-Allow-Origin': '*'},
			*args,
			**kwargs
		)

	def _timed_hash_key(self, key):
		return '%s:%d' % (
			hashlib.sha1(key).digest(),
			int(time()) / self.quota.reset_interval
		)

	def _abort_internal_quota(self):
		self.abort(
			code=403,
			detail='over internal quota',
		)
	
	def _check_quota_count(self, quota_key):
		key = 'quota:count:%s' % self._timed_hash_key(quota_key)
		count = memcache.incr(key, initial_value=0)
		logging.info('_check_quota_count: %r', count)

		if count is not None and count >= self.quota.count:
			logging.warning('over internal quota count (quota.count=%d)', self.quota.count)
			self._abort_internal_quota()

	def _check_quota_bytes(self, quota_key):
		key = 'quota:bytes:%s' % self._timed_hash_key(quota_key)
		bytes = memcache.get(key)
		logging.info('_check_quota_bytes: %r', bytes)

		if bytes is not None and bytes >= self.quota.bytes:
			logging.warning('over internal quota bytes (quota.bytes=%d)', self.quota.bytes)
			self._abort_internal_quota()

	def _update_quota_bytes(self, quota_key, delta):
		key = 'quota:bytes:%s' % self._timed_hash_key(quota_key)
		bytes = memcache.incr(key, delta=delta, initial_value=0)
		logging.info('_update_quota_bytes: %r', bytes)

	def _abort_incorrect_client(self):
		self.abort(
			code=403,
			detail='Please request via XmlHttpRequest Lv.2 API',
		)
	
	def _check_request(self, origin):
		logging.info('referrer %r', self.request.headers.get('referer'))

		user_agent = self.request.headers.get('user-agent')
		logging.info('user-agent %r', user_agent)
		if user_agent is None:
			logging.warning('missing user-agent')
			self._abort_incorrect_client();
			return

	def _urlfetch(self, url):
		key = 'urlfetch:%s' % url
		uresp = memcache.get(key)
		if uresp is not None:
			logging.info('urlfetch cache hit')
			return uresp
	
		res = None
		try:
			logging.info('urlfetch cache miss')
			res = urlfetch.fetch(url)

		except (urlfetch.InvalidURLError,
			urlfetch.DownloadError,
			urlfetch.ResponseTooLargeError,
			apiproxy_errors.DeadlineExceededError), err:

			logging.warning('urlfetch error: %r', err)
			self.abort(
				code=403,
				detail='urlfetch error: %r' % err,
			)

		uresp = UrlResp(res)
		try:
			setResult = memcache.set(key, uresp, self.urlfetch_cache_seconds)
			logging.info('memcache set: %s', setResult)

		except ValueError, err:
			logging.warning('memcache error: %r', err)

		return uresp

	def get(self, url):
		if self.request.query_string:
			url += '?' + self.request.query_string

		logging.info('url %r', url)
		origin = self.request.headers.get('origin', '').lower();
		logging.info('origin %r', origin)

		self._check_request(origin)
		self._refresh_quota()

		if self.quota is not None:
			self._check_quota_count(origin)
			self._check_quota_bytes(origin)

		uresp = self._urlfetch(url)
		for key, val in uresp.headers.iteritems():
			if key.lower() == 'set-cookie':
				continue
			self.response.headers[key] = val

		self.response.headers['access-control-allow-origin'] = '*'

		if uresp.status_code >= 500:
			logging.info('override response status_code %d', uresp.status_code)
			self.response.headers['x-original-status-code'] = str(uresp.status_code)
			self.response.set_status(403)

		else:
			self.response.set_status(uresp.status_code)

		content = uresp.content
		self._update_quota_bytes(origin, len(content))
		self.response.write(content)

class OkHandler(webapp2.RequestHandler):
	def get(self):
		quota = Quota.get_by_id(Quota.default_id);
		assert quota

		self.response.content_type = 'text/plain'
		self.response.write('ok')

	head = get

Quota.get_or_insert(Quota.default_id,
	count=1000,
	bytes=10*1024*1024,
	reset_interval=2*3600
)

app = webapp2.WSGIApplication([
	(r'/(https?://.*)', HttpProxyHandler),
	(r'/ok', OkHandler),
], debug=True)
