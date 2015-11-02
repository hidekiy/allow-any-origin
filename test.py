import unittest
import webapp2

from google.appengine.api import memcache
from google.appengine.ext import ndb
from google.appengine.ext.testbed import Testbed

testbed = Testbed()
testbed.activate()
testbed.init_datastore_v3_stub()
testbed.init_memcache_stub()
ndb.get_context().clear_cache()

import main

class AppTest(unittest.TestCase):
	def setUp(self):
		ndb.get_context().clear_cache()

	def test_ok(self):
		request = webapp2.Request.blank('/ok')
		response = request.get_response(main.app)
		self.assertEqual(response.status_int, 200)
		self.assertEqual(response.body, 'ok')
