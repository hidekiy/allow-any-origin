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
			if not quota:
				return

			logging.info('_refresh_quota %r', quota)
			cls.quota = quota

	def abort(self, *args, **kwargs):
		super(HttpProxyHandler, self).abort(
			headers={'Access-Control-Allow-Origin': '*'},
			*args,
			**kwargs
		)

	def _check_quota(self, quota_key):
		key = 'quota:%s:%d' % (
			hashlib.sha1(quota_key).digest(),
			int(time()) / self.quota.reset_interval
		)
		count = memcache.incr(key, initial_value=0)
		logging.info('_check_quota: count %d', count)

		if self.quota:
			if count >= self.quota.count:
				logging.warning('over internal quota count %d', self.quota.count)
				self.abort(
					code=403,
					detail='over internal quota',
				)

	def _abort_incorrect_client(self):
		self.abort(
			code=403,
			detail='Please request via XmlHttpRequest Lv.2 API',
		)
	
	def _check_request(self, origin):
		if not origin:
			logging.info('missing origin')
			return

		user_agent = self.request.headers.get('user-agent', '')
		logging.debug('user-agent %s', user_agent)
		if not user_agent:
			logging.warning('missing user-agent')
			self._abort_incorrect_client();
			return

	def _urlfetch(self, url):
		key = 'urlfetch:%s' % url
		uresp = memcache.get(key)
		if uresp is not None:
			logging.debug('urlfetch cache hit')
			return uresp
	
		res = None
		try:
			logging.debug('urlfetch cache miss')
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
			memcache.set(key, uresp, self.urlfetch_cache_seconds)

		except ValueError, err:
			logging.warning('memcache error: %r', err)

		return uresp

	def get(self, url):
		if self.request.query_string:
			url += '?' + self.request.query_string

		logging.debug('url %s', url)
		logging.debug('referrer %s', self.request.headers.get('referer', ''))
		origin = self.request.headers.get('origin', '').lower();
		logging.debug('origin %s', origin)

		self._check_request(origin)
		self._refresh_quota()
		self._check_quota(origin)

		uresp = self._urlfetch(url)
		for key, val in uresp.headers.iteritems():
			self.response.headers[key] = val

		self.response.headers['access-control-allow-origin'] = '*'
		self.response.headers.pop('set-cookie', None)

		if uresp.status_code >= 500:
			logging.info('override response code %d', uresp.status_code)

			self.response.headers['x-original-status-code'] = str(uresp.status_code)
			self.response.set_status(403)
		else:
			self.response.set_status(uresp.status_code)

		self.response.write(uresp.content)

class OkHandler(webapp2.RequestHandler):
	def get(self):
		quota = Quota.get_by_id(Quota.default_id);
		assert quota

		self.response.content_type = 'text/plain'
		self.response.write('ok')

	head = get

Quota.get_or_insert(Quota.default_id,
	count=1000,
	reset_interval=3600
)

application = webapp2.WSGIApplication([
	(r'/(https?://.*)', HttpProxyHandler),
	(r'/ok', OkHandler),
], debug=True)
