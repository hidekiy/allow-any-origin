#coding=utf-8
import logging
import re
from time import time
import hashlib
import webapp2
from google.appengine.api import urlfetch
from google.appengine.runtime import apiproxy_errors

class HttpProxyHandler(webapp2.RequestHandler):

	internal_quota_reset_interval = 60 * 61
	internal_quota_request_threshold = 500
	internal_quota_dict = dict()
	internal_quota_reset_at = time()

	@classmethod
	def _prepare_internal_quota(cls):
		if time() - cls.internal_quota_reset_at > cls.internal_quota_reset_interval:
			cls.internal_quota_dict = dict()
			cls.internal_quota_reset_at = time()

	def _check_internal_quota(self, key):
		key_hash = hashlib.sha1(key).digest()
		quota_count = self.internal_quota_dict.get(key_hash, 0) + 1
		self.internal_quota_dict[key_hash] = quota_count
		logging.info('quota_count %s %d', key, quota_count)

		if (quota_count > self.internal_quota_request_threshold):
			logging.warning('over internal quota')
			self.abort(
				code=403,
				detail='over internal quota',
				headers={'Access-Control-Allow-Origin': '*'},
			)

	def get(self, url):
		self._prepare_internal_quota()

		if self.request.query_string:
			url += '?' + self.request.query_string

		logging.debug('url %s', url)

		origin = self.request.headers.get('origin', '').lower();
		logging.debug('origin %s', origin)
		if origin:
			self._check_internal_quota(origin)

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
				headers={'Access-Control-Allow-Origin': '*'},
			)

class OkHandler(webapp2.RequestHandler):

	def get(self):
		self.response.content_type = 'text/plain'
		self.response.write('ok')

	head = get

application = webapp2.WSGIApplication([
	(r'/(https?://.*)', HttpProxyHandler),
	(r'/ok', OkHandler),
], debug=True)
