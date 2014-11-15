#coding=utf-8
import logging
import re
import sys
import hashlib
from time import time

import webapp2
from google.appengine.api import urlfetch
from google.appengine.ext import ndb
from google.appengine.runtime import apiproxy_errors

class Quota(ndb.Model):
	count = ndb.IntegerProperty()
	reset_interval = ndb.IntegerProperty()
	default_id = 'default'

class HttpProxyHandler(webapp2.RequestHandler):
	quota_dict = None
	quota_reset_at = 0
	quota_limits_refreshed_at = 0
	quota_limits_refresh_interval = 60
	quota_reset_interval = 0
	quota_count_threshold = sys.maxint
	cors_headers = {'Access-Control-Allow-Origin': '*'}

	@classmethod
	def _prepare_quota(cls):
		if time() - cls.quota_reset_at > cls.quota_reset_interval:
			logging.info('reset quota_dict')
			cls.quota_dict = dict()
			cls.quota_reset_at = time()

	@classmethod
	def _update_quota_limits(cls):
		if time() - cls.quota_limits_refreshed_at > cls.quota_limits_refresh_interval:
			cls.quota_limits_refreshed_at = time()
			quota = Quota.get_by_id(Quota.default_id);

			if quota:
				quota_count = quota.count
				quota_reset_interval = quota.reset_interval
				logging.info('_update_quota_limits count=%d, reset_interval=%d', quota_count, quota_reset_interval)
				cls.quota_count_threshold = quota_count
				cls.quota_reset_interval = quota_reset_interval

	def _check_quota(self, key):
		key_hash = hashlib.sha1(key).digest()
		quota_count = self.quota_dict.get(key_hash, 0) + 1
		self.quota_dict[key_hash] = quota_count
		logging.info('_check_quota: quota_count %d', quota_count)

		if (quota_count > self.quota_count_threshold):
			logging.warning('over internal quota')
			self.abort(
				code=403,
				detail='over internal quota',
				headers=self.cors_headers,
			)

	def get(self, url):
		self._prepare_quota()
		self._update_quota_limits()

		if self.request.query_string:
			url += '?' + self.request.query_string

		logging.debug('url %s', url)
		logging.debug('referrer %s', self.request.headers.get('referer', ''))

		origin = self.request.headers.get('origin', '').lower();
		logging.debug('origin %s', origin)

		if not origin:
			logging.warning('missing origin')
			self.abort(
				code=403,
				detail='Origin header is required',
				headers=self.cors_headers,
			)
			return

		self._check_quota(origin)

		res, errorReason = None, None
		try:
			res = urlfetch.fetch(url)

		except (urlfetch.InvalidURLError,
			urlfetch.DownloadError,
			urlfetch.ResponseTooLargeError,
			apiproxy_errors.DeadlineExceededError), error:

			errorReason = repr(error)

		if res:
			for key, val in res.headers.iteritems():
				self.response.headers[key] = val

			self.response.headers['Access-Control-Allow-Origin'] = '*'
			self.response.headers.pop('Set-Cookie', None)

			if res.status_code >= 500:
				logging.info('override response code %d', res.status_code)

				self.response.headers['X-Original-Status-Code'] = str(res.status_code)
				self.response.set_status(403)
			else:
				self.response.set_status(res.status_code)

			self.response.write(res.content)

		else:
			logging.warning('urlfetch error: %s', errorReason)

			self.abort(
				code=403,
				detail='urlfetch error: %s' % errorReason,
				headers=self.cors_headers,
			)

class OkHandler(webapp2.RequestHandler):
	def get(self):
		quota = Quota.get_by_id(Quota.default_id);
		assert quota

		self.response.content_type = 'text/plain'
		self.response.write('ok')

	head = get

Quota.get_or_insert(Quota.default_id,
	count=1000, reset_interval=60 * 60 * 2)

application = webapp2.WSGIApplication([
	(r'/(https?://.*)', HttpProxyHandler),
	(r'/ok', OkHandler),
], debug=True)
