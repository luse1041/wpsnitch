# This software is in the public domain. Where that dedication is not
# recognized, you are granted a perpetual, irrevocable license to copy,
# distribute, and modify this file as you see fit.

import datetime
import json
import logging
import pickle
import urllib2
from urlparse import urlparse

from google.appengine.ext import db


class SnitchException(Exception):
    """any exception"""


class AppCache(db.Model):
    sku_id = db.StringProperty(required=True)
    data = db.TextProperty(required=True)
    updated = db.DateTimeProperty(auto_now=True)


class App(object):
    def __init__(self, url):
        self.url = url
        self.sku_id = None
        self.guid = None
        self.data = {}

    def get_sku_id(self):
        parsed_url = urlparse(self.url)

        if parsed_url.netloc != 'www.microsoft.com':
            raise SnitchException('Invalid URL.')

        parts = parsed_url.path.split('/')

        try:
            self.sku_id = parts[5].lower()[:12]
        except IndexError:
            raise SnitchException('Invalid URL.')

    @staticmethod
    def find_payload(response):
        """the json response we get from the store is a list of dicts with
            several payloads containing different things, like a list of
            related apps, etc. we need to find which one is the one with
            the app details. it usually is the last one, but I don't think
            it is a good idea to hardcode that."""
        for entry in response:
            try:
                payload = entry['Payload']
            except KeyError:
                continue  # might not be fatal, just try the next one
            if ('Microsoft.Marketplace.Storefront.Contracts.V3.ProductDetails'
                    in payload['$type']):
                return payload
        raise SnitchException('No app details in the response from the Store.')

    def get_cache(self):
        q = db.Query(AppCache)
        q.filter('sku_id =', self.sku_id)
        q.filter('updated >',
                 datetime.datetime.now() - datetime.timedelta(days=1))
        entry = q.get()

        if entry:
            logging.info('Loading from cache: %s' % self.sku_id)
            self.data = pickle.loads(entry.data.encode('windows-1252'))

    def set_cache(self):
        data = pickle.dumps(self.data).decode('windows-1252')

        q = db.Query(AppCache)
        q.filter('sku_id =', self.sku_id)
        entry = q.get()

        if entry:
            logging.info('Cache refresh: %s' % self.sku_id)
            entry.data = data
        else:
            entry = AppCache(sku_id=self.sku_id, data=data)

        entry.put()

    def get(self):
        # Get the SKU id from the url
        self.get_sku_id()

        # check the cache and return already if it's populated
        self.get_cache()

        if self.data:
            return

        logging.info('Cache miss: %s' % self.sku_id)

        request_url = 'https://storeedgefd.dsx.mp.microsoft.com/' \
            'v8.0/pages/pdp?productId={sku_id}&market=US&locale=en-US' \
            '&appversion=11703.1001.45.0'.format(sku_id=self.sku_id)

        try:
            request = urllib2.Request(request_url)
            response = urllib2.urlopen(request).read()
        except urllib2.URLError:
            raise SnitchException('Error retrieving info from the Store.')

        try:
            response_dict = json.loads(response)
        except ValueError:
            raise SnitchException('Invalid info retrieved from the Store.')

        payload = self.find_payload(response_dict)

        def fix_date(date):
            if date in {'1601-01-01T00:00:00.000000Z',
                        '0001-01-01T00:00:00Z'}:
                return 'Never'

            return date[:19].replace('T', ' ')

        try:
            self.data['name'] = payload['Title']
            self.data['last_updated'] = fix_date(payload['LastUpdateDateUtc'])
            self.data['release_date'] = fix_date(payload['ReleaseDateUtc'])
        except KeyError:
            raise SnitchException("Can't find what I'm looking for.")

        self.set_cache()
